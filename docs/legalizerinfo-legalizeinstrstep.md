# LegalizerInfo 规则表与 LegalizerHelper::legalizeInstrStep 算法

> 分析对象：
> - `llvm/include/llvm/CodeGen/GlobalISel/LegalizerInfo.h`
> - `llvm/lib/CodeGen/GlobalISel/LegalizerInfo.cpp`
> - `llvm/include/llvm/CodeGen/GlobalISel/LegalizerHelper.h`
> - `llvm/lib/CodeGen/GlobalISel/LegalizerHelper.cpp`
> - `llvm/lib/CodeGen/GlobalISel/Legalizer.cpp`
> - `llvm/lib/Target/ToyCPU/GISel/ToyCPULegalizerInfo.cpp`
>
> 行号以当前 `<llvm-src>` 工作区为准。
>
> 相关文档（本仓库）：
> - [LegiSpec 与复杂规则走读](walkthrough.md)
> - [LegiSpec v0.1 语言定义](legispec-v1.0.md)
> - [规则表设计](design.md)
>
> 同源的 LLVM 通用分析（IRTranslator / MachineIRBuilder / SelectionDAG、
> Lowering 方法论）不在本仓库；需要时回上游研究树查阅。

---

## 0. 一句话理解

GlobalISel 的 Legalizer 可以拆成三层：

1. **`LegalizerInfo`：声明式规则表。**  
   回答“这个 opcode + 这些类型/内存属性，下一步应该做什么？”

2. **`LegalizerHelper::legalizeInstrStep`：查表 + 动作分派器。**  
   把一个 `MachineInstr` 的 legality 查询结果翻译成具体的 `narrowScalar`、`widenScalar`、`lower`、`libcall`、`custom` 等调用。

3. **`LegalizerPass` / `legalizeMachineFunction`：工作队列驱动。**  
   反向遍历指令，反复调用 `legalizeInstrStep`，处理新生成的指令和 `G_TRUNC/G_MERGE_VALUES` 等 artifact，直到没有非法指令。

一句话：

> **LegalizerInfo 决定“做什么”，LegalizerHelper 执行“这一步怎么做”，Legalizer pass 负责“什么时候继续做”。**

---

## 1. 在 GlobalISel 管线中的位置

```text
IR
 |
 v
IRTranslator
 |  生成 generic MachineInstr
 v
Legalizer
 |  LegalizerInfo 查询 + LegalizerHelper 改写
 v
RegBankSelect
 |  选择寄存器 bank
 v
InstructionSelect
 |  选择目标指令
 v
MachineScheduler / RegAlloc / ...
```

三者的职责边界：

| 组件 | 职责 |
|---|---|
| `LegalizerInfo` | 保存每个 generic opcode 的规则集合；根据 `LegalityQuery` 返回 `LegalizeActionStep` |
| `LegalizerHelper` | 实现具体的 scalar/vector/libcall/lower 改写算法；持有 `MachineIRBuilder` |
| `LegalizerPass` | 建立工作队列，调用 Helper，维护 artifact 列表，循环到不动点 |

`LegalizerInfo` 通常由 target subtarget 持有。例如 ToyCPU：

```cpp
// ToyCPUSubtarget.cpp:42
Legalizer.reset(new ToyCPULegalizerInfo(*this));

// ToyCPUSubtarget.cpp:56-57
const LegalizerInfo *ToyCPUSubtarget::getLegalizerInfo() const {
  return Legalizer.get();
}
```

`LegalizerHelper` 则是在每个函数执行 Legalizer 时创建：

```cpp
// Legalizer.cpp:220-223
LegalizerHelper Helper(MF, LI, WrapperObserver, MIRBuilder, Libcalls, VT);
```

---

## 2. 规则表的数据模型

### 2.1 `LegalizeAction`

`LegalizerInfo.h:43-96` 定义所有 action：

| Action | 含义 | 典型实现 |
|---|---|---|
| `Legal` | 目标可以直接选择，无需修改 | 不做任何事 |
| `NarrowScalar` | 用更窄的标量/limb 实现 | `LegalizerHelper::narrowScalar` |
| `WidenScalar` | 用更宽的标量实现，再截回原宽度 | `LegalizerHelper::widenScalar` |
| `FewerElements` | 把向量拆成更少 lane 的子向量 | `fewerElementsVector` |
| `MoreElements` | 把向量加宽，忽略新增 lane | `moreElementsVector` |
| `Bitcast` | 改成同尺寸的另一种类型 | `bitcast` |
| `Lower` | 展开成更简单的 generic opcode 序列 | `lower` |
| `Libcall` | 变成运行时库调用 | `libcall` |
| `Custom` | 调用 target 的 `legalizeCustom` | target 自定义 |
| `Unsupported` | 目标完全不支持，通常是错误 | 返回 `UnableToLegalize` |
| `NotFound` | 未找到规则的哨兵值 | 不作为正常 action 使用 |

注意：

- `Legal` 不是“语义上绝对合法”，而是“**这个 target 的 InstructionSelect 能处理它**”。
- `Unsupported` 和“没有规则”在 `LegalizeRuleSet::apply` 里最终都会走向失败路径。

### 2.2 `LegalityQuery`

```cpp
// LegalizerInfo.h:106-139
struct LegalityQuery {
  unsigned Opcode;
  ArrayRef<LLT> Types;

  struct MemDesc {
    LLT MemoryTy;
    uint64_t AlignInBits;
    AtomicOrdering Ordering;
    AtomicOrdering FailureOrdering;
  };

  ArrayRef<MemDesc> MMODescrs;
  ArrayRef<int64_t> Immediates;
};
```

它不是简单的 `(Opcode, LLT)`，而是包含：

- `Types`：按 **type index** 排列的 LLT 数组；
- `MMODescrs`：每个内存操作数的 memory type、alignment、atomic ordering；
- `Immediates`：generic immediate 操作数。

### 2.3 type index 不是 operand index

Generic opcode 的 TableGen 定义会把类型位置编号为 `type0`、`type1` 等。例如：

```tablegen
// GenericOpcodes.td:499-502
def G_ICMP : GenericInstruction {
  let OutOperandList = (outs type0:$dst);
  let InOperandList = (ins unknown:$tst, type1:$src1, type1:$src2);
}
```

所以 `G_ICMP` 的：

- `TypeIdx 0` = 结果类型；
- `TypeIdx 1` = LHS/RHS 的类型；
- predicate 是 immediate/unknown 操作数，不占 type index。

常见 opcode 的 type index：

| opcode | type index 0 | type index 1 |
|---|---|---|
| `G_ADD` | dst/src 共用类型 | 无独立 type1 |
| `G_ICMP` | dst | src1/src2 |
| `G_SELECT` | dst/src1/src2 | condition |
| `G_LOAD` | dst | pointer |
| `G_STORE` | value | pointer |
| `G_TRUNC/G_ZEXT/G_SEXT` | dst | src |
| `G_PTR_ADD` | pointer dst/src1 | offset 类型 |

因此 ToyCPU 里的：

```cpp
.customFor({{v4s1, v4s32}})
```

表示：

- type 0 = `v4s1`（比较结果）；
- type 1 = `v4s32`（两个输入向量）。

### 2.4 `LegalizeActionStep`

```cpp
// LegalizerInfo.h:144-160
struct LegalizeActionStep {
  LegalizeAction Action;
  unsigned TypeIdx;
  LLT NewType;
};
```

它表示：

- `Action`：下一步动作；
- `TypeIdx`：要修改哪个 type index；
- `NewType`：修改成什么类型。

例如：

```cpp
{ WidenScalar, 0, s32 }
```

表示“把 type index 0 扩展到 s32”。

`Legal` / `Custom` / `Lower` 等动作可能不需要 mutation，此时 `NewType` 为空。

### 2.5 `LegalizeRule`

```cpp
// LegalizerInfo.h:400-423
class LegalizeRule {
  LegalityPredicate Predicate;
  LegalizeAction Action;
  LegalizeMutation Mutation;

public:
  bool match(const LegalityQuery &Query) const;
  LegalizeAction getAction() const;
  std::pair<unsigned, LLT> determineMutation(const LegalityQuery &Query) const;
};
```

一条规则就是：

```text
predicate(Query) 为真
    -> 返回 Action
    -> 可选地计算 (TypeIdx, NewType)
```

### 2.6 `LegalizeRuleSet`：规则按插入顺序匹配

```cpp
// LegalizerInfo.h:425-435
class LegalizeRuleSet {
  SmallVector<LegalizeRule, 2> Rules;
};
```

`apply` 的行为非常关键：

```cpp
// LegalizerInfo.cpp:194-211（压缩）
for (const LegalizeRule &Rule : Rules) {
  if (Rule.match(Query)) {
    auto Mutation = Rule.determineMutation(Query);
    return {Rule.getAction(), Mutation.first, Mutation.second};
  }
}
return {LegalizeAction::Unsupported, 0, LLT{}};
```

即：

> **规则表是 first-match-wins，先加的规则优先。**

这直接决定了 ToyCPU 中 `.legalFor(...)`、`.customFor(...)`、`.clampScalar(...)` 的书写顺序。

### 2.7 `LegalizerInfo` 的整体存储

```cpp
// LegalizerInfo.h:1338-1429
class LegalizerInfo {
private:
  static const int FirstOp = TargetOpcode::PRE_ISEL_GENERIC_OPCODE_START;
  static const int LastOp = TargetOpcode::PRE_ISEL_GENERIC_OPCODE_END;
  LegalizeRuleSet RulesForOpcode[LastOp - FirstOp + 1];
};
```

每个 generic opcode 对应一个 `LegalizeRuleSet`，按 opcode 偏移直接索引。

查询路径：

```cpp
// LegalizerInfo.cpp:341-343
LegalizeActionStep
LegalizerInfo::getAction(const LegalityQuery &Query) const {
  return getActionDefinitions(Query.Opcode).apply(Query);
}
```

### 2.8 `getAction(MI, MRI)` 如何构造 Query

`LegalizerInfo.cpp:346-374`：

```cpp
SmallVector<LLT, 8> Types;
SmallVector<int64_t, 8> Immediates;
SmallBitVector SeenTypes(8);

for (unsigned i = 0; i < MI.getDesc().getNumOperands(); ++i) {
  if (OpInfo[i].isGenericType()) {
    unsigned TypeIdx = OpInfo[i].getGenericTypeIndex();
    if (SeenTypes[TypeIdx])
      continue;
    SeenTypes.set(TypeIdx);

    LLT Ty = getTypeFromTypeIdx(MI, MRI, i, TypeIdx);
    Types.push_back(Ty);
  } else if (OpInfo[i].isGenericImm()) {
    Immediates.push_back(MI.getOperand(i).getImm());
  }
}

for (const auto &MMO : MI.memoperands())
  MemDescrs.push_back({*MMO});

return getAction({MI.getOpcode(), Types, MemDescrs, Immediates});
```

三个细节：

1. **同一个 TypeIdx 只记录一次。**  
   比如 `G_ADD` 的三个操作数都是 `type0`，不能重复放入 `Types`。

2. **类型从虚拟寄存器的 `MRI.getType()` 读取。**  
   不是从 IR 类型直接读。

3. **`G_UNMERGE_VALUES` 有特例。**  
   `getTypeFromTypeIdx` 对 `G_UNMERGE_VALUES` 的 TypeIdx 1 使用最后一个操作数，因为它的多结果类型需要特殊处理（`LegalizerInfo.cpp:274-283`）。

### 2.9 规则表构建辅助函数

`LegalizeRuleSet` 提供大量 DSL。常用接口：

| 接口 | 作用 |
|---|---|
| `legalFor({...})` | type 0 属于集合时 Legal |
| `legalFor({{...}, {...}})` | type 0/1 的 pair 属于集合时 Legal |
| `legalForTypesWithMemDesc({...})` | 同时匹配 type 0/1、MMO 类型和最小对齐 |
| `legalIf(pred)` | 任意谓词为真时 Legal |
| `alwaysLegal()` | 所有情况 Legal |
| `customFor(...)` / `customIf(...)` | Custom action |
| `libcallFor(...)` | Libcall action |
| `lowerFor(...)` / `lowerIf(...)` | Lower action |
| `widenScalarToNextPow2` | 按 2 的幂扩展 |
| `minScalar` / `maxScalar` | 限制标量宽度 |
| `clampScalar` | `minScalar` + `maxScalar` |
| `unsupported()` | 兜底 Unsupported |

这些 helper 本质上都是：

```cpp
actionIf(Action, Predicate, Mutation);
```

它们只是把 predicate/mutation 组合起来。

例如：

```cpp
// LegalizerInfo.h:1141-1146
LegalizeRuleSet &clampScalar(unsigned TypeIdx, const LLT MinTy,
                             const LLT MaxTy) {
  return minScalar(TypeIdx, MinTy).maxScalar(TypeIdx, MaxTy);
}
```

所以 `clampScalar` 实际会追加两条规则：

1. 小于 MinTy -> `WidenScalar`；
2. 大于 MaxTy -> `NarrowScalar`。

### 2.10 调试期规则覆盖检查

`LegalizerInfo::verify`（`LegalizerInfo.cpp:396-445`）会遍历所有 generic opcode：

- 从 MCID 计算 `NumTypeIdxs` / `NumImmIdxs`；
- 检查每个 type/imm index 是否至少被某条规则覆盖；
- 缺失时报告：

```text
ill-defined LegalizerInfo, try -debug-only=legalizer-info and
-verbose-gisel-verify-legalizer-info for details
```

`LegalizeRuleSet` 在 debug 构建里用 `TypeIdxsCovered` / `ImmIdxsCovered` 位图记录覆盖情况。自由形式的 predicate 会保守地标记所有 index 已覆盖。

这就是为什么 ToyCPU 在 `G_SHUFFLE_VECTOR` 的最后一条规则使用：

```cpp
.customIf(...)
.unsupported();
```

`unsupported()` 既是兜底语义，也是覆盖检查的显式声明。

---

## 3. 规则表如何被“构造”和“别名”

### 3.1 单 opcode builder

```cpp
getActionDefinitionsBuilder(G_MUL)
    .legalFor({v4s32})
    .libcallFor({s32, s64})
    .clampScalar(0, s32, s32);
```

它会取得 `G_MUL` 的 `LegalizeRuleSet&`，然后按调用顺序追加规则。

### 3.2 多 opcode builder 是“别名”，不是复制

```cpp
getActionDefinitionsBuilder({G_ADD, G_SUB, G_AND, G_OR, G_XOR})
    .legalFor({s32, v4s32})
    .clampScalar(0, s32, s32);
```

`LegalizerInfo.cpp:318-331` 会把第一个 opcode 作为 representative：

```cpp
unsigned Representative = *Opcodes.begin();

for (unsigned Op : llvm::drop_begin(Opcodes))
  aliasActionDefinitions(Representative, Op);

auto &Return = getActionDefinitionsBuilder(Representative);
Return.setIsAliasedByAnother();
return Return;
```

意义：

- `G_ADD/G_SUB/G_AND/G_OR/G_XOR` 共享同一份规则；
- 不能给其中一个 aliased opcode 再单独加规则；
- 需要不同规则时，必须拆成不同的 builder。

ToyCPU 的注释特别提醒了这一点：

```cpp
// ToyCPULegalizerInfo.cpp:185-191
// G_ZEXT and G_SEXT must NOT share one rule set: getActionDefinitionsBuilder
// with an initializer list aliases the opcodes, and an opcode that is aliased
// by another one can no longer be given its own rules.
getActionDefinitionsBuilder(G_ZEXT)
    .legalFor({{s32, s32}})
    .customFor({{s32, s1}, {s32, s8}, {s32, s16}})
    .customIf(LegalityPredicates::scalarWiderThan(0, 32));
```

### 3.3 查询时的别名解析

`LegalizerInfo.cpp:286-303`：

```cpp
unsigned LegalizerInfo::getActionDefinitionsIdx(unsigned Opcode) const {
  unsigned OpcodeIdx = getOpcodeIdxForOpcode(Opcode);
  if (unsigned Alias = RulesForOpcode[OpcodeIdx].getAlias()) {
    OpcodeIdx = getOpcodeIdxForOpcode(Alias);
  }
  return OpcodeIdx;
}
```

因此：

- 构造时共享规则；
- 查询时解析到 representative；
- 规则表本身不需要为每个 opcode 复制一份。

---

## 4. ToyCPU 规则表实战

ToyCPU 的规则表在：

```text
<llvm-src>\llvm\lib\Target\ToyCPU\GISel\ToyCPULegalizerInfo.cpp
```

构造函数大约在 `:45-344`。

### 4.1 标量算术：i32 是基本宽度

```cpp
// ToyCPULegalizerInfo.cpp:61-63
getActionDefinitionsBuilder({G_ADD, G_SUB, G_AND, G_OR, G_XOR})
    .legalFor({s32, v4s32})
    .clampScalar(0, s32, s32);
```

解释：

- `s32` 和 `v4s32` 直接合法；
- 其他标量宽度通过 `clampScalar` 修正；
- 小于 s32 -> `WidenScalar`；
- 大于 s32 -> `NarrowScalar`；
- 这里的 type index 0 覆盖 dst 和两个 src，因为 `G_ADD` 的三个操作数都是 `type0`。

### 4.2 移位：规则顺序很关键

```cpp
// ToyCPULegalizerInfo.cpp:67-75
getActionDefinitionsBuilder({G_SHL, G_ASHR, G_LSHR})
    .legalFor({{s32, s32}, {v4s32, v4s32}})
    .clampScalar(1, s32, s32)
    .clampScalar(0, s32, s32);
```

为什么先 clamp type 1，再 clamp type 0？

源码注释给出了原因：

```text
narrowScalarShift() 会把 s64 shift 拆成多个 s32 shift，
但仍使用原始 shift amount 类型。
如果先 clamp 结果类型，就可能生成 G_ASHR s32, s64，
这个组合无法继续合法化。
```

这是一条非常重要的学习点：

> 规则不是“集合”，而是“有序决策列表”；先处理哪个 type index 可能决定算法是否能收敛。

### 4.3 乘法/除法/取余：libcall

```cpp
// ToyCPULegalizerInfo.cpp:83-89
getActionDefinitionsBuilder(G_MUL)
    .legalFor({v4s32})
    .libcallFor({s32, s64})
    .clampScalar(0, s32, s32);

getActionDefinitionsBuilder({G_SDIV, G_UDIV, G_SREM, G_UREM})
    .libcallFor({s32, s64})
    .clampScalar(0, s32, s32);
```

含义：

- 向量乘法有 `VMUL`，所以 `v4s32` legal；
- 标量乘法/除法/取余走 `__mulsi3`、`__divsi3`、`__modsi3` 等运行时 helper；
- 更宽或更窄的标量先通过 clamp 调整。

注意 `G_MUL` 不能和向量规则共享 builder，否则 `libcallFor` 也可能捕获 `v4s32`。

### 4.4 carry 链：s32 + s1

```cpp
// ToyCPULegalizerInfo.cpp:96-100
getActionDefinitionsBuilder({G_UADDO, G_USUBO, G_UADDE, G_USUBE,
                             G_SADDO, G_SSUBO, G_SADDE, G_SSUBE})
    .legalFor({{s32, s1}})
    .clampScalar(0, s32, s32)
    .clampScalar(1, s1, s1);
```

这是 i64 拆 limb 的基础：

- 值宽度是 s32；
- carry 是 s1；
- ToyCPU 把 s1 carry 也放在 32 位 GPR 中处理。

### 4.5 常量：窄常量也合法

```cpp
// ToyCPULegalizerInfo.cpp:107-109
getActionDefinitionsBuilder(G_CONSTANT)
    .legalFor({s32, p0, s1, s8, s16})
    .clampScalar(0, s1, s32);
```

这说明：

- `s8/s16` 常量不需要立即被扩展到 s32；
- 后续 `G_STORE/G_ZEXT/G_SEXT` 会携带正确的截断/扩展语义；
- 这样能避免简单代码过早产生 artifact 或失败。

### 4.6 内存：类型 + MMO 大小 + 对齐

```cpp
// ToyCPULegalizerInfo.cpp:130-137
getActionDefinitionsBuilder({G_LOAD, G_STORE})
    .legalForTypesWithMemDesc({{s32, p0, s32, 1},
                               {s32, p0, s8, 1},
                               {s32, p0, s16, 1},
                               {p0, p0, s32, 1},
                               {v4s32, p0, v4s32, 128},
                               {v2s32, p0, v2s32, 64}})
    .clampScalar(0, s32, s32);
```

tuple 含义：

```text
(Type0, Type1, MemoryTy, MinAlignInBits)
```

例如：

```text
{s32, p0, s8, 1}
```

表示：

- type 0 = s32（load 结果 / store 值）；
- type 1 = p0（地址）；
- 内存访问类型 = s8；
- 最小对齐 = 1 bit。

这里不是“任意 s8 load 都原生支持”，而是“**s32 GPR + s8 内存宽度 + 至少 1 bit 对齐**”这个组合合法。

### 4.7 `G_ICMP`：Legal、Custom、Widen 的组合

```cpp
// ToyCPULegalizerInfo.cpp:154-159
getActionDefinitionsBuilder(G_ICMP)
    .legalFor({{s32, s32}, {p0, p0}, {v4s32, v4s32}})
    .customFor({{v4s1, v4s32}})
    .widenScalarToNextPow2(0, 32)
    .clampScalar(0, s32, s32)
    .clampScalar(1, s32, s32);
```

这里展示了三类规则：

1. `legalFor`：i32 比较、向量 i32 比较直接合法；
2. `customFor`：v4i1 结果需要 Target 自定义；
3. `widenScalarToNextPow2` / `clampScalar`：其余结果/操作数宽度做调整。

`v4s1` 被 custom 的原因不是“不能比较”，而是 ToyCPU 没有 predicate 寄存器文件：

- `VCMP` 产生 `v4i32` 的 0/1 值；
- IRTranslator 先给的是 `v4i1`；
- custom legalizer 把它改写为 `v4s32`，并更新使用者。

### 4.8 `G_PHI`：布尔也要变成 32 位

```cpp
// ToyCPULegalizerInfo.cpp:164-166
getActionDefinitionsBuilder(G_PHI)
    .legalFor({s32, p0, v4s32, v2s32})
    .widenScalarToNextMultipleOf(0, 32);
```

原因：AtomicExpandPass 可能在 IR 级 cmpxchg 循环中生成 i1 PHI；ToyCPU 的布尔值最终要放在 32 位 GPR 中。

### 4.9 截断/扩展：宽值走 custom

```cpp
// ToyCPULegalizerInfo.cpp:179-192
getActionDefinitionsBuilder(G_TRUNC)
    .legalFor({{s32, s32}, {s8, s32}, {s16, s32}, {s1, s32}})
    .customIf(LegalityPredicates::scalarWiderThan(1, 32));

getActionDefinitionsBuilder(G_ANYEXT)
    .legalFor({{s32, s32}, {s32, s8}, {s32, s16}, {s32, s1}})
    .customIf(LegalityPredicates::scalarWiderThan(0, 32));

getActionDefinitionsBuilder(G_ZEXT)
    .legalFor({{s32, s32}})
    .customFor({{s32, s1}, {s32, s8}, {s32, s16}})
    .customIf(LegalityPredicates::scalarWiderThan(0, 32));

getActionDefinitionsBuilder(G_SEXT)
    .legalFor({{s32, s32}, {v4s32, v4s1}})
    .customFor({{s32, s1}, {s32, s8}, {s32, s16}})
    .customIf(LegalityPredicates::scalarWiderThan(0, 32));
```

设计意图：

- 单 GPR 内的截断/扩展可以是 legal；
- i64/i128 这类宽值不能作为单个 vreg 存活到 RegBankSelect；
- 因此 `scalarWiderThan` 触发 custom，把它拆成 s32 limb + `G_MERGE_VALUES/G_UNMERGE_VALUES`；
- `G_ZEXT` 和 `G_SEXT` 必须分开建规则，不能被别名合并。

### 4.10 `G_SELECT`

```cpp
// ToyCPULegalizerInfo.cpp:199-206
getActionDefinitionsBuilder(G_SELECT)
    .legalFor({{s32, s32}, {v4s32, v4s32}, {v4s32, v4s1}})
    .clampScalar(0, s32, s32)
    .clampScalar(1, s32, s32);
```

注意 `v4s1` 条件被保留为 legal。原因是 Legalizer 反向处理指令：

- `G_SELECT` 可能先于定义其 mask 的 `G_ICMP` 被访问；
- 后续 custom `G_ICMP` 会把 `v4s1` 定义改成 `v4s32`，并原位更新这个 use。

### 4.11 浮点：libcall 与 custom

```cpp
// ToyCPULegalizerInfo.cpp:226-253
getActionDefinitionsBuilder({G_FADD, G_FSUB, G_FMUL, G_FDIV})
    .libcallFor({s32, s64});

getActionDefinitionsBuilder(G_FNEG).lowerFor({s32, s64});

getActionDefinitionsBuilder(G_FCMP)
    .customForCartesianProduct({s1}, {s32, s64});

getActionDefinitionsBuilder(G_FCONSTANT)
    .customFor({s32, s64});
```

ToyCPU 没有硬件浮点：

- 算术 -> `__addsf3` 等 libcall；
- `FNEG` 是纯符号位翻转，可以 lower 成 XOR；
- `FCMP` 需要 target 选择正确的 AEABI 布尔 helper；
- `FCONSTANT` 要保留 bit pattern，不能当成普通整数语义处理。

### 4.12 向量 lane 操作：custom + unsupported 兜底

```cpp
// ToyCPULegalizerInfo.cpp:314-333
getActionDefinitionsBuilder(G_IMPLICIT_DEF)
    .customIf(typeInSet(0, {v4s32, v2s32}));

getActionDefinitionsBuilder(G_BUILD_VECTOR)
    .customIf(all(typeInSet(0, {v4s32, v2s32}), typeIs(1, s32)));

getActionDefinitionsBuilder(G_EXTRACT_VECTOR_ELT)
    .customIf(all(typeIs(0, s32), typeInSet(1, {v4s32, v2s32}),
                  typeIs(2, s32)));

getActionDefinitionsBuilder(G_INSERT_VECTOR_ELT)
    .customIf(all(typeInSet(0, {v4s32, v2s32}), typeIs(1, s32),
                  typeIs(2, s32)));

getActionDefinitionsBuilder(G_SHUFFLE_VECTOR)
    .customIf(all(typeInSet(0, {v4s32, v2s32}),
                  typeInSet(1, {v4s32, v2s32})))
    .unsupported();

getActionDefinitionsBuilder(G_CONCAT_VECTORS)
    .customIf(all(typeIs(0, v4s32), typeIs(1, v2s32)))
    .unsupported();

getActionDefinitionsBuilder(G_EXTRACT_SUBVECTOR)
    .customIf(all(typeIs(0, v2s32), typeIs(1, v4s32)))
    .unsupported();
```

ToyCPU 没有原生的 lane insert/extract/shuffle：

- 常用 lane 操作走 16 字节栈往返；
- 不支持的组合显式 `unsupported()`，而不是静默留下非法指令。

### 4.13 merge/unmerge：合法 artifact

```cpp
// ToyCPULegalizerInfo.cpp:282-285
getActionDefinitionsBuilder(G_MERGE_VALUES)
    .legalFor({{s64, s32}, {s128, s32}});

getActionDefinitionsBuilder(G_UNMERGE_VALUES)
    .legalFor({{s32, s64}, {s32, s128}});
```

这些指令在 Legalizer 阶段允许暂时存在：

- `narrowScalar` 会把 i64 拆成两个 s32；
- call lowering / libcall 会用 merge/unmerge 组装参数和返回值；
- 之后的 `LegalizationArtifactCombiner` 会尝试折叠它们；
- 如果折叠不掉，再由后续阶段处理。

---

## 5. `LegalizerHelper::legalizeInstrStep` 算法

源码：

```text
<llvm-src>\llvm\lib\CodeGen\GlobalISel\LegalizerHelper.cpp:122-160
```

### 5.1 主流程

```cpp
LegalizerHelper::LegalizeResult
LegalizerHelper::legalizeInstrStep(MachineInstr &MI,
                                   LostDebugLocObserver &LocObserver) {
  MIRBuilder.setInstrAndDebugLoc(MI);

  if (isa<GIntrinsic>(MI))
    return LI.legalizeIntrinsic(*this, MI)
               ? Legalized
               : UnableToLegalize;

  auto Step = LI.getAction(MI, MRI);

  switch (Step.Action) {
  case Legal:
    return AlreadyLegal;
  case Libcall:
    return libcall(MI, LocObserver);
  case NarrowScalar:
    return narrowScalar(MI, Step.TypeIdx, Step.NewType);
  case WidenScalar:
    return widenScalar(MI, Step.TypeIdx, Step.NewType);
  case Bitcast:
    return bitcast(MI, Step.TypeIdx, Step.NewType);
  case Lower:
    return lower(MI, Step.TypeIdx, Step.NewType);
  case FewerElements:
    return fewerElementsVector(MI, Step.TypeIdx, Step.NewType);
  case MoreElements:
    return moreElementsVector(MI, Step.TypeIdx, Step.NewType);
  case Custom:
    return LI.legalizeCustom(*this, MI, LocObserver)
               ? Legalized
               : UnableToLegalize;
  default:
    return UnableToLegalize;
  }
}
```

关键点：

1. **先设置插入点和 DebugLoc。**  
   之后所有 helper 生成的指令都会插到 `MI` 前面，或者按需要插到 `MI` 后面。

2. **Intrinsic 走特殊路径。**  
   `G_INTRINSIC` / `G_INTRINSIC_W_SIDE_EFFECTS` 不按普通 type table 处理，而是交给 `legalizeIntrinsic`。

3. **查询和分派是解耦的。**  
   `LegalizerInfo` 不知道如何改 IR，只返回 action；`LegalizerHelper` 不知道规则表细节，只执行 action。

4. **它只做“一步”。**  
   `legalizeInstrStep` 不循环；循环由 `LegalizerPass` 的工作队列完成。

### 5.2 返回值

`LegalizerHelper.h:67-80`：

```cpp
enum LegalizeResult {
  AlreadyLegal,       // 本来合法，没有修改
  Legalized,          // 已改写，MachineFunction 发生变化
  UnableToLegalize,   // 无法处理
};
```

`LegalizerPass` 根据返回值决定：

- `AlreadyLegal`：继续；
- `Legalized`：标记 Changed，继续处理工作队列；
- `UnableToLegalize`：artifact 可重试，否则报错/回退。

### 5.3 与 `LegalizerInfo::legalizeCustom` 的关系

`LegalizerInfo.h:1404-1409`：

```cpp
virtual bool legalizeCustom(LegalizerHelper &Helper, MachineInstr &MI,
                            LostDebugLocObserver &LocObserver) const {
  llvm_unreachable("must implement this if custom action is used");
}
```

只要规则表返回 `Custom`，target 就必须覆写这个方法。ToyCPU 的实现：

```cpp
// ToyCPULegalizerInfo.cpp:348-963
bool ToyCPULegalizerInfo::legalizeCustom(
    LegalizerHelper &Helper, MachineInstr &MI,
    LostDebugLocObserver &LocObserver) const {
  MachineIRBuilder &MIRBuilder = Helper.MIRBuilder;
  ...
  switch (MI.getOpcode()) {
  case TargetOpcode::G_ICMP: ...
  case TargetOpcode::G_BUILD_VECTOR: ...
  ...
  }
}
```

`legalizeCustom` 返回 `true` 表示“处理成功”，返回 `false` 会变成 `UnableToLegalize`。

注意：helper 不会自动删除旧 MI。target 的 custom 代码通常需要：

```cpp
MIRBuilder.build...;
MI.eraseFromParent();
return true;
```

或者原地修改 MI 并通知 observer。

---

## 6. 各 action 的实现算法

### 6.1 `Legal`

```cpp
case Legal:
  return AlreadyLegal;
```

不生成指令，不修改 MI。

这要求 target 的 InstructionSelect 真的能处理这个 opcode/type 组合。ToyCPU 的 `s32` 算术、`v4s32` 算术、`G_LOAD s32/p0` 等都走这里。

### 6.2 `NarrowScalar`

入口：

```cpp
// LegalizerHelper.cpp:1555
LegalizerHelper::LegalizeResult
LegalizerHelper::narrowScalar(MachineInstr &MI,
                              unsigned TypeIdx,
                              LLT NarrowTy);
```

它不是简单地把类型改成 NarrowTy，而是按 opcode 做语义拆分。

#### 例子：i64 加法

概念上的变换：

```text
A = G_ADD i64 B, C
```

变成一个 limb 序列：

```text
B0, B1 = G_UNMERGE_VALUES B
C0, C1 = G_UNMERGE_VALUES C
S0, carry = G_UADDO B0, C0
S1, carry2 = G_UADDE B1, C1, carry
A = G_MERGE_VALUES S0, S1
```

`narrowScalarAddSub` 负责这个模式；carry 使用 s1，ToyCPU 再把 s1 carry 保存在 GPR 中。

#### 例子：常量拆分

`G_CONSTANT i64`：

```text
K = G_CONSTANT i64 0x123456789abcdef0
```

会拆成若干个 narrow 常量，再 merge：

```text
K0 = G_CONSTANT s32 low
K1 = G_CONSTANT s32 high
K  = G_MERGE_VALUES K0, K1
```

#### 例子：位运算

`G_AND/G_OR/G_XOR` 走 `narrowScalarBasic`：

```text
B0, B1 = unmerge B
C0, C1 = unmerge C
R0 = op B0, C0
R1 = op B1, C1
R  = merge R0, R1
```

#### 例子：load/store

`G_LOAD` / `G_STORE` 会：

- 减少访问宽度；
- 或插入 `G_TRUNC/G_ANYEXT`；
- 保留 MMO 的访问大小和对齐信息。

#### 失败条件

`narrowScalar` 的 switch 中没有对应 opcode 时：

```cpp
default:
  return UnableToLegalize;
```

所以 `NarrowScalar` 不是“通用万能拆分器”，而是“对一组已知 opcode 的语义拆分器”。

### 6.3 `WidenScalar`

入口：

```cpp
// LegalizerHelper.cpp:2798
LegalizerHelper::LegalizeResult
LegalizerHelper::widenScalar(MachineInstr &MI,
                             unsigned TypeIdx,
                             LLT WideTy);
```

它通常做三件事：

1. 把源操作数扩展到 WideTy；
2. 用 WideTy 执行原 opcode；
3. 把结果截回原类型。

#### 源操作数扩展

```cpp
// LegalizerHelper.cpp:2183-2188
void LegalizerHelper::widenScalarSrc(MachineInstr &MI, LLT WideTy,
                                     unsigned OpIdx, unsigned ExtOpcode) {
  MachineOperand &MO = MI.getOperand(OpIdx);
  auto ExtB = MIRBuilder.buildInstr(ExtOpcode, {WideTy}, {MO});
  MO.setReg(ExtB.getReg(0));
}
```

扩展指令插在 `MI` 前面，然后替换 `MI` 的操作数。

#### 目标操作数扩展

```cpp
// LegalizerHelper.cpp:2205-2212
void LegalizerHelper::widenScalarDst(MachineInstr &MI, LLT WideTy,
                                     unsigned OpIdx, unsigned TruncOpcode) {
  MachineOperand &MO = MI.getOperand(OpIdx);
  Register DstExt = MRI.createGenericVirtualRegister(WideTy);

  MIRBuilder.setInsertPt(MIRBuilder.getMBB(),
                         ++MIRBuilder.getInsertPt());
  MIRBuilder.buildInstr(TruncOpcode, {MO}, {DstExt});
  MO.setReg(DstExt);
}
```

注意：

- 目标先改成 WideTy 的新 vreg；
- 截断指令插到 `MI` 后面；
- 原来的目标 vreg 接收截断结果。

#### 例子：G_ADD

```text
A = G_ADD s16 B, C
```

变成：

```text
B' = G_ANYEXT s32 B
C' = G_ANYEXT s32 C
A' = G_ADD s32 B', C'
A  = G_TRUNC s16 A'
```

#### 扩展 opcode 的选择

`widenScalar` 会根据语义选择：

- `G_ANYEXT`：高位不关心，常用于普通算术/位运算；
- `G_ZEXT`：需要保留无符号值，例如 shift amount；
- `G_SEXT`：需要保留符号值，例如 `G_SDIV`、`G_ABS`。

ToyCPU 的 shift 规则就先 clamp amount，避免 `G_ASHR s32, s64` 这种不合法组合。

#### flags

`widenScalar` 有时会显式清除 `NoUWrap` / `NoSWrap` / `Disjoint`，因为 `G_ANYEXT` 的高位不受原窄类型约束，原来的 nowrap 证明不再成立。相关代码在 `widenScalar` 的 `G_ADD/G_SUB/G_MUL` 分支中。

### 6.4 `Bitcast`

入口：

```cpp
// LegalizerHelper.cpp:4558
LegalizerHelper::LegalizeResult
LegalizerHelper::bitcast(MachineInstr &MI, unsigned TypeIdx, LLT CastTy);
```

它通常原地改类型，而不是生成一条新指令：

- `G_LOAD`：改 dst 类型，同时改 MMO 的 memory type；
- `G_STORE`：改 src 类型，同时改 MMO；
- `G_SELECT`：改条件/值/结果类型；
- `G_AND/G_OR/G_XOR`：改 src/dst 类型；
- 向量相关 opcode 有专门处理。

`bitcastSrc` / `bitcastDst`（`LegalizerHelper.cpp:2248-2256`）分别处理 use 和 def。

这类 action 的前提是“同尺寸、不同解释”，例如：

```text
s32 <-> float bit pattern
p0  <-> s32
v4s32 <-> 128-bit value
```

### 6.5 `Lower`

入口：

```cpp
// LegalizerHelper.cpp:4646
LegalizerHelper::LegalizeResult
LegalizerHelper::lower(MachineInstr &MI, unsigned TypeIdx,
                       LLT LowerHintTy);
```

`lower` 是一个按 opcode 分派的“语义展开器”。

典型变换：

| 原指令 | Lower 后 |
|---|---|
| `G_SREM/G_UREM` | `G_SDIV/G_UDIV` + `G_MUL` + `G_SUB` |
| `G_FNEG` | 与符号位 mask 做 `G_XOR` |
| `G_FSUB` | `G_FNEG` + `G_FADD` |
| `G_SMULO/G_UMULO` | `G_MUL` + `G_SMULH/G_UMULH` + `G_ICMP` |
| `G_ATOMIC_CMPXCHG_WITH_SUCCESS` | `G_ATOMIC_CMPXCHG` + `G_ICMP` |
| `G_LOAD/G_STORE` | `lowerLoad` / `lowerStore` |
| `G_CTLZ/G_CTPOP/...` | `lowerBitCount` |

例如 `G_SREM` 的展开：

```cpp
auto Quot = MIRBuilder.buildInstr(
    MI.getOpcode() == G_SREM ? G_SDIV : G_UDIV, {Ty},
    {MI.getOperand(1), MI.getOperand(2)});
auto Prod = MIRBuilder.buildMul(Ty, Quot, MI.getOperand(2));
MIRBuilder.buildSub(MI.getOperand(0), MI.getOperand(1), Prod);
MI.eraseFromParent();
```

不是所有 opcode 都有 Lower 实现；default 仍然返回 `UnableToLegalize`。

### 6.6 `FewerElements`

入口：

```cpp
// LegalizerHelper.cpp:5750
LegalizerHelper::LegalizeResult
LegalizerHelper::fewerElementsVector(MachineInstr &MI,
                                      unsigned TypeIdx,
                                      LLT NarrowTy);
```

核心算法：

```text
原向量操作
  -> 拆成多个 NarrowTy 子向量
  -> 对每个子向量执行同类操作
  -> 用 merge/build/concat 重新组合
  -> 处理不能整除时的 leftover
```

相关实现：

- `fewerElementsVectorMultiEltType`（`:5311`）：多数算术/比较/转换的通用拆分；
- `fewerElementsVectorMerge`（`:5483`）：`G_BUILD_VECTOR/G_CONCAT_VECTORS` 的特殊处理；
- `insertParts`（`:166`）：把多个子结果拼回目标类型；
- `G_EXTRACT_SUBVECTOR` 有单独分支。

`scalarize(TypeIdx)` 本质上是 `FewerElements` 的极端情况：把向量拆成 1 个 scalar。

### 6.7 `MoreElements`

入口：

```cpp
// LegalizerHelper.cpp:6921
LegalizerHelper::LegalizeResult
LegalizerHelper::moreElementsVector(MachineInstr &MI,
                                     unsigned TypeIdx,
                                     LLT MoreTy);
```

算法相反：

```text
原操作数
  -> 用 undef 填充到更多 lane
  -> 执行更宽向量操作
  -> 删除目标中新增的尾部 lane
```

关键 helper：

```cpp
// LegalizerHelper.cpp:2242-2246
void LegalizerHelper::moreElementsVectorSrc(MachineInstr &MI,
                                            LLT MoreTy, unsigned OpIdx) {
  MachineOperand &MO = MI.getOperand(OpIdx);
  MO.setReg(MIRBuilder.buildPadVectorWithUndefElements(MoreTy, MO).getReg(0));
}
```

```cpp
// LegalizerHelper.cpp:2232-2240
void LegalizerHelper::moreElementsVectorDst(MachineInstr &MI,
                                            LLT WideTy, unsigned OpIdx) {
  MachineOperand &MO = MI.getOperand(OpIdx);
  MIRBuilder.setInsertPt(MIRBuilder.getMBB(), ++MIRBuilder.getInsertPt());
  Register Dst = MO.getReg();
  Register DstExt = MRI.createGenericVirtualRegister(WideTy);
  MO.setReg(DstExt);
  MIRBuilder.buildDeleteTrailingVectorElements(Dst, DstExt);
}
```

特殊情况：

- `G_SELECT` 的标量条件需要广播；
- `G_PHI` 走 `moreElementsVectorPhi`；
- `G_SHUFFLE_VECTOR` 走 `moreElementsVectorShuffle`。

### 6.8 `Libcall`

入口：

```cpp
// LegalizerHelper.cpp:1327
LegalizerHelper::LegalizeResult
LegalizerHelper::libcall(MachineInstr &MI,
                         LostDebugLocObserver &LocObserver);
```

它不是通用“把任意指令变成函数调用”，而是按 opcode 分支：

- 整数乘除：`simpleLibcall`；
- 浮点算术/数学函数：`simpleLibcall`；
- 浮点转换：`conversionLibcall`；
- 浮点比较：`createFCMPLibcall`；
- 原子操作：`createAtomicLibcall`；
- mem*：`createMemLibcall`。

大致生成序列：

```text
ADJCALLSTACKDOWN
参数扩展 / 拆分 / COPY 到 ABI 寄存器
JAL <libcall>
结果 COPY 到 vreg
合并宽结果
ADJCALLSTACKUP
```

ToyCPU 的 `m12_i64_libcall` 就是很好的例子：

- `G_SREM i64` -> `__moddi3`；
- `G_SDIV i64` -> `__divdi3`；
- `G_MUL i64` -> `__muldi3`；
- i64 参数先 unmerge 成两个 s32 limb，再放入 r3/r4/r5/r6；
- 返回值再 merge 回 i64。

### 6.9 `Custom`

```cpp
case Custom:
  return LI.legalizeCustom(*this, MI, LocObserver)
             ? Legalized
             : UnableToLegalize;
```

这是 target 自定义逻辑的入口。ToyCPU 的 custom 处理包括：

- `G_ICMP v4s1` -> `v4s32`；
- `G_IMPLICIT_DEF` / `G_BUILD_VECTOR` / lane extract/insert/shuffle 的栈往返；
- `G_FCMP` 的 AEABI 布尔 helper；
- `G_FCONSTANT` 的 bit pattern 保留；
- `G_VASTART/G_VAARG`；
- mem* intrinsic；
- 宽 `G_TRUNC/G_ZEXT/G_SEXT/G_ANYEXT` 的 limb 拆分。

例如 G_ICMP custom：

```cpp
// ToyCPULegalizerInfo.cpp:611-656
case TargetOpcode::G_ICMP: {
  Register Dst = MI.getOperand(0).getReg();
  LLT DstTy = MRI.getType(Dst);
  if (DstTy != V4S1)
    return false;
  ...
  auto NewCmp = MIRBuilder.buildICmp(Pred, V4S32, LHS, RHS);
  Register NewReg = NewCmp.getReg(0);
  ...
  MRI.replaceRegWith(Dst, NewReg);
  MI.eraseFromParent();
  return true;
}
```

这段代码同时展示了 custom legalizer 的职责：

- 生成新 MIR；
- 更新使用者；
- 替换旧 vreg；
- 删除旧 MI；
- 返回成功/失败。

### 6.10 `Unsupported` / `NotFound`

`LegalizeRuleSet::apply` 没有匹配规则时返回：

```cpp
return {LegalizeAction::Unsupported, 0, LLT{}};
```

`legalizeInstrStep` 的 `default` 分支把它变成：

```cpp
return UnableToLegalize;
```

之后由 `LegalizerPass` 处理：

- 如果是 artifact，放入 `RetryList`，等 ArtifactCombiner 处理；
- 如果不是 artifact，报告 failure；
- ToyCPU 设置了 `GlobalISelAbortMode::DisableWithDiag`，所以可以回退 SelectionDAG：

```cpp
// ToyCPUTargetMachine.cpp:54
setGlobalISelAbort(GlobalISelAbortMode::DisableWithDiag);
```

注意：`Unsupported` 和 `NotFound` 的区别主要在语义/调试上；正常规则表路径下，没有匹配就返回 `Unsupported`。

---

## 7. Legalizer pass 的工作队列算法

源码：

```text
<llvm-src>\llvm\lib\CodeGen\GlobalISel\Legalizer.cpp
```

### 7.1 建立工作队列

`legalizeMachineFunction`（`:179`）做：

```cpp
InstListTy InstList;
ArtifactListTy ArtifactList;
ReversePostOrderTraversal<MachineFunction *> RPOT(&MF);

for (auto *MBB : RPOT) {
  for (MachineInstr &MI : *MBB) {
    if (!isPreISelGenericOpcode(MI.getOpcode()))
      continue;

    if (isArtifact(MI))
      ArtifactList.deferred_insert(&MI);
    else
      InstList.deferred_insert(&MI);
  }
}
```

`isArtifact`（`:95-117`）包括：

```text
G_TRUNC
G_ZEXT
G_ANYEXT
G_SEXT
G_MERGE_VALUES
G_UNMERGE_VALUES
G_CONCAT_VECTORS
G_BUILD_VECTOR
G_EXTRACT
G_INSERT
```

为什么分开？

- 普通指令：优先 legalize；
- artifact：legalizer 本身经常生成它们，先尝试 combine 掉，避免做无意义的 legalization；
- 如果不能 combine，再放回普通指令队列。

### 7.2 为什么反向处理

代码遍历 BB 时是正向插入，但之后用：

```cpp
MachineInstr &MI = *InstList.pop_back_val();
```

所以整体是**从函数尾部向头部、从基本块尾部向头部**处理。

原因：

- 先处理使用者，很多时候可以在定义被处理前简化/删除；
- 便于 DCE；
- 某些 user 的合法性依赖 later definition（如 ToyCPU `G_SELECT` 先看到 `G_ICMP` 的 v4s1 结果）。

ToyCPU 的 `G_SELECT` 注释就是这个现象的直接说明：

```cpp
// v4i1 condition is left legal on purpose: the legalizer processes
// instructions in reverse order, so a select can be visited before the
// G_ICMP that defines its mask.
```

### 7.3 主循环

```cpp
do {
  while (!InstList.empty()) {
    MachineInstr &MI = *InstList.pop_back_val();

    if (isTriviallyDead(MI, MRI)) {
      eraseInstr(MI, MRI, &LocObserver);
      continue;
    }

    auto Res = Helper.legalizeInstrStep(MI, LocObserver);

    if (Res == UnableToLegalize) {
      if (isArtifact(MI)) {
        RetryList.push_back(&MI);
        continue;
      }
      return {Changed, &MI};
    }

    Changed |= Res == Legalized;
  }

  // 处理 artifact
  while (!ArtifactList.empty()) {
    MachineInstr &MI = *ArtifactList.pop_back_val();
    if (ArtCombiner.tryCombineInstruction(MI, ...)) {
      ...
      continue;
    }
    InstList.insert(&MI);
  }
} while (!InstList.empty());
```

### 7.4 观察者如何保持队列正确

`LegalizerWorkListManager`（`Legalizer.cpp:123-172`）：

- `createdInstr`：新指令加入 `InstList` 或 `ArtifactList`；
- `changedInstr`：修改后的指令重新加入队列；
- `erasingInstr`：从两个队列移除。

因此：

```cpp
MIRBuilder.build...
MI.eraseFromParent();
```

不是“改完就结束”，而是会通过 observer 自动把新指令送到正确的队列，继续合法化。

### 7.5 ArtifactCombiner

`ArtifactCombiner` 尝试消除：

- `G_TRUNC` + `G_ZEXT/G_SEXT/G_ANYEXT`
- `G_MERGE_VALUES` + `G_UNMERGE_VALUES`
- `G_BUILD_VECTOR` + `G_EXTRACT_VECTOR_ELT`
- 其他可折叠的 cast/merge/unmerge 组合

ToyCPU 的 i64 处理依赖这个阶段：

```text
G_UNMERGE_VALUES / G_UADDO / G_UADDE / G_MERGE_VALUES
```

如果 artifact 能消掉，最终不会留下宽 vreg；如果不能，后续阶段仍然要能处理。

### 7.6 CSE

`Legalizer.cpp:312-335`：

```cpp
static bool isCSEEnabled() {
  return EnableCSEInLegalizer.getNumOccurrences()
             ? EnableCSEInLegalizer
             : true;
}

if (EnableCSE) {
  MIRBuilder = std::make_unique<CSEMIRBuilder>();
  CSEInfo = GetCSEInfo();
  MIRBuilder->setCSEInfo(CSEInfo);
} else {
  MIRBuilder = std::make_unique<MachineIRBuilder>();
}
```

默认启用 CSE。Legalizer 生成大量 cast/constant/merge 时，`CSEMIRBuilder` 可以做局部 CSE 和常量折叠，减少 artifact。---

## 8. ToyCPU 实证

### 8.1 `G_ICMP v4s1` 的 custom legalization

测试：

```text
<legacy-checkout>\tests\m24_vector_cmp_select.ll
```

命令：

```powershell
& <llvm-build-toycpu>\bin\llc.exe `
  -march=toycpu -mtriple=toycpu-unknown-elf `
  -global-isel -stop-after=legalizer -simplify-mir `
  -o - <legacy-checkout>\tests\m24_vector_cmp_select.ll
```

关键 debug 输出：

```text
Legalizing: %34:_(<4 x s32>) = G_SELECT %6:_(<4 x s1>), %0:_, %2:_
.. Already legal
Legalizing: %6:_(<4 x s1>) = G_ICMP intpred(slt), %0:_(<4 x s32>), %2:_
.. Custom legalization
Legalizing: %54:_(<4 x s32>) = G_ICMP intpred(slt), %0:_(<4 x s32>), %2:_
.. Already legal
```

说明：

1. `G_SELECT` 先被访问，因为工作是反向的；
2. `v4s1` 条件在 `G_SELECT` 规则中是 legal；
3. 之后 `G_ICMP v4s1` 被 custom legalize；
4. 生成 `G_ICMP v4s32`；
5. 新的 `G_ICMP v4s32` 再被查询时已经是 legal。

最终 MIR 片段：

```text
%56:_(<4 x s32>) = G_ICMP intpred(eq), %0(<4 x s32>), %2
%54:_(<4 x s32>) = G_ICMP intpred(slt), %0(<4 x s32>), %2
%34:_(<4 x s32>) = G_SELECT %54(<4 x s32>), %0, %2
```

这正是 ToyCPU 注释所说的“v4i1 compare result -> v4i32 mask”策略。

### 8.2 i64 加法的 narrow scalar

测试：

```text
<legacy-checkout>\tests\m12_i64_libcall.ll
```

debug 输出：

```text
Legalizing: %10:_(s64) = G_ADD %9:_, %8:_
.. Narrow scalar
.. .. New MI: %13:_(s32), %14:_(s32) = G_UNMERGE_VALUES %9:_(s64)
.. .. New MI: %15:_(s32), %16:_(s32) = G_UNMERGE_VALUES %8:_(s64)
.. .. New MI: %17:_(s32), %18:_(s1) = G_UADDO %13:_, %15:_
.. .. New MI: %19:_(s32), %20:_(s1) = G_UADDE %14:_, %16:_, %18:_
.. .. New MI: %10:_(s64) = G_MERGE_VALUES %17:_(s32), %19:_
```

这就是 `NarrowScalar` 的 limb 算法：

```text
s64 add
  -> unmerge 成两个 s32
  -> low limb: UADDO
  -> high limb: UADDE + carry
  -> merge 回 s64
```

### 8.3 i64 除法的 libcall

同一测试：

```text
Legalizing: %8:_(s64) = G_SREM %0:_, %1:_
.. Convert to libcall
.. .. New MI: ADJCALLSTACKDOWN 0, 0, ...
.. .. New MI: %29:_(s32), %30:_(s32) = G_UNMERGE_VALUES %0:_(s64)
.. .. New MI: %31:_(s32), %32:_(s32) = G_UNMERGE_VALUES %1:_(s64)
.. .. New MI: $r3 = COPY %29:_(s32)
.. .. New MI: $r4 = COPY %30:_(s32)
.. .. New MI: $r5 = COPY %31:_(s32)
.. .. New MI: $r6 = COPY %32:_(s32)
.. .. New MI: JAL &__moddi3, ...
.. .. New MI: %33:_(s32) = COPY $r3
.. .. New MI: %34:_(s32) = COPY $r4
.. .. New MI: %8:_(s64) = G_MERGE_VALUES %33:_(s32), %34:_(s32)
.. .. New MI: ADJCALLSTACKUP 0, 0, ...
```

这展示了 `Libcall` action 的完整形状：

1. 建立调用帧；
2. 把 i64 参数拆成 ABI 可传递的 s32 limb；
3. 按 ToyCPU 调用约定放入 r3/r4/r5/r6；
4. 调用 `__moddi3`；
5. 把返回值 merge 回 i64；
6. 恢复调用帧。

---

## 9. 如何新增/修改一条 Legalizer 规则

以“让某个新 opcode 在 ToyCPU 上合法”为例：

### 9.1 确认 type index

先查 `GenericOpcodes.td` 或生成的 MCID，确认：

- 哪个操作数是 `type0`；
- 哪个是 `type1`；
- 是否有 immediate；
- 是否有 MMO。

不要凭 operand 顺序猜 type index。

### 9.2 选择 action

| 情况 | 选择 |
|---|---|
| Target 的 selector 能直接处理 | `legalFor` |
| 需要拆成更窄 limb | `NarrowScalar` / `clampScalar` |
| 可以先按更宽类型算再截回 | `WidenScalar` / `minScalar` |
| 需要拆向量 lane | `FewerElements` |
| 需要填充向量 lane | `MoreElements` |
| 是纯类型重解释 | `Bitcast` |
| 能用更简单 generic opcode 表达 | `Lower` |
| 需要运行时 helper | `Libcall` |
| 只有 target 知道怎么做 | `Custom` |
| 完全不可能支持 | `Unsupported` |

### 9.3 注意规则顺序

一个常见结构：

```cpp
getActionDefinitionsBuilder(Opcode)
    .legalFor(...)          // 1. 原生合法组合
    .customFor(...)         // 2. 需要 target 特殊处理的组合
    .libcallFor(...)        // 3. 运行时 helper
    .lowerFor(...)          // 4. generic 展开
    .clampScalar(...)       // 5. 类型修正
    .unsupported();         // 6. 兜底（如果需要显式覆盖）
```

但顺序不是固定模板，必须根据语义决定。ToyCPU 的 shift 规则就是反例：先 clamp amount，再 clamp 结果。

### 9.4 实现 custom callback

如果选择 `Custom`，必须在 `ToyCPULegalizerInfo::legalizeCustom` 中添加 case，并：

- 校验操作数数量和类型；
- 用 `MachineIRBuilder` 生成新 MIR；
- 更新使用者 / `MRI.replaceRegWith`；
- `MI.eraseFromParent()`；
- 返回 `true`。

### 9.5 验证

```powershell
. <workspace>\vsenv.ps1

cmake --build <llvm-build-toycpu> --target LLVMToyCPUCodeGen --parallel 8
cmake --build <llvm-build-toycpu> --target llc --parallel 8

# 查看 legalizer 后的 MIR
& <llvm-build-toycpu>\bin\llc.exe `
  -march=toycpu -mtriple=toycpu-unknown-elf `
  -global-isel -stop-after=legalizer -simplify-mir `
  -o - your-test.ll

# 查看规则匹配过程
& <llvm-build-toycpu>\bin\llc.exe `
  -march=toycpu -mtriple=toycpu-unknown-elf `
  -global-isel -debug-only=legalizer `
  -stop-after=legalizer -simplify-mir `
  -o NUL your-test.ll 2>&1
```

### 9.6 常见验证点

- `-debug-only=legalizer` 是否显示 `Already legal` / `Custom legalization` / `Narrow scalar` / `Convert to libcall`；
- legalizer 输出中是否还有非法宽 vreg（例如 ToyCPU 不应留下 s64 单寄存器）；
- 是否产生大量无法 combine 的 `G_MERGE_VALUES/G_UNMERGE_VALUES`；
- `-stop-after=reg-bank-select` 是否还能继续；
- 最终 `-global-isel` 输出是否与 SDAG 结果一致。

---

## 10. 常见坑

1. **把 type index 当 operand index。**  
   `G_ICMP` 的 predicate 不是 type index；`G_SELECT` 的 condition 是 type 1，不是 type 0。

2. **忽略规则顺序。**  
   `LegalizeRuleSet::apply` 是 first-match-wins；`legalFor` 写在 `customFor` 前面会截获本来应该 custom 的组合。

3. **用多 opcode builder 后又想单独定制。**  
   多 opcode builder 是别名共享规则；需要不同规则时必须拆开。

4. **把 `G_ZEXT` 和 `G_SEXT` 合并。**  
   ToyCPU 注释明确说明这会阻止后续单独定制；两者语义也不同。

5. **以为 `NarrowScalar` 对任意 opcode 都可用。**  
   `narrowScalar` 内部是按 opcode 的 switch，没有实现就返回 `UnableToLegalize`。

6. **以为 `WidenScalar` 只改类型。**  
   它会插入 `G_ANYEXT/G_ZEXT/G_SEXT` 和 `G_TRUNC`，还可能清除 nowrap flags。

7. **忘记 custom 的清理工作。**  
   `legalizeCustom` 不会自动删除旧 MI；必须显式 `eraseFromParent` 或正确修改并通知 observer。

8. **没有覆盖所有 type/imm index。**  
   Debug 构建的 `LegalizerInfo::verify` 会报“ill-defined LegalizerInfo”。

9. **把 `Custom` 当成 `Legal`。**  
   `isLegalOrCustom` 在 verifier 中把 Custom 当作“可能不修改”，但运行期仍需 target 返回 true。

10. **把 `legalizeInstrStep` 当完整循环。**  
    它只处理一条指令的一步；重复、worklist、artifact combine 都在 `LegalizerPass` 中。

---

## 11. 建议阅读顺序

1. `LegalizerInfo.h:43-160`：Action、Query、ActionStep
2. `LegalizerInfo.h:400-530`：Rule、RuleSet、action DSL
3. `LegalizerInfo.h:580-780`：legal/custom/libcall/lower helper
4. `LegalizerInfo.h:1075-1160`：clamp/min/max scalar
5. `LegalizerInfo.h:1338-1429`：LegalizerInfo 整体结构
6. `LegalizerInfo.cpp:194-214`：first-match apply
7. `LegalizerInfo.cpp:274-375`：Query 构造、别名、getAction
8. `LegalizerInfo.cpp:396-445`：debug 覆盖检查
9. `LegalizerHelper.cpp:122-160`：legalizeInstrStep
10. `LegalizerHelper.cpp:166-280`：insertParts / extractGCDType / LCM
11. `LegalizerHelper.cpp:2183-2256`：src/dst 扩展、截断、bitcast helper
12. `LegalizerHelper.cpp:1555`：narrowScalar
13. `LegalizerHelper.cpp:2798`：widenScalar
14. `LegalizerHelper.cpp:4558`：bitcast
15. `LegalizerHelper.cpp:4646`：lower
16. `LegalizerHelper.cpp:5750`：fewerElementsVector
17. `LegalizerHelper.cpp:6921`：moreElementsVector
18. `Legalizer.cpp:179-307`：worklist 主循环
19. `ToyCPULegalizerInfo.cpp:61-339`：ToyCPU 规则表
20. `ToyCPULegalizerInfo.cpp:348-963`：ToyCPU custom legalizer

读完这二十处，基本可以把“规则表怎么查、action 怎么分派、具体怎么改写、worklist 怎么收敛”四条线连起来。