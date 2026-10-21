# Legalizer DSL 与复杂规则走读

> 目标：把 `LegalizerInfo` 当成一门小语言来学，而不是当成一堆 API。
> 方法：先看规则表，再跟 `legalizeInstrStep`，最后看 helper 如何生成新 MIR。
> 前置：`legalizerinfo-legalizeinstrstep.md` 已给出完整数据模型与算法；本文只做 DSL 视角和实战走读。

相关文档：

- `legalizerinfo-legalizeinstrstep.md` —— 规则表、action、helper、工作队列的完整分析
- `hardware-model-method.md` —— 硬件模型与证伪
- `machineirbuilder-analysis.md` —— `MachineIRBuilder` 的构建算法
- `irtranslator-translation-model.md` —— generic MIR 是怎么来的
- `legalizer-dsl-design.md` —— 规则 DSL 语法、宏展开与 Python 实现

---

## 0. 一句话

> **`LegalizerInfo` 是一套 C++ 内嵌的声明式 legality 规则 DSL；`legalizeInstrStep` 是它的单步解释器；`LegalizerPass` 是让它反复执行到不动点的运行时。**

~~~text
规则表（policy）  →  查询/匹配（apply）  →  动作分派（legalizeInstrStep）
        →  helper 改写（operational semantics）  →  新 MI 回队列  →  直到不动点
~~~

---

## 1. DSL 的五层结构

| 层 | 角色 | 关键代码 |
|---|---|---|
| **表面语法** | builder 链式调用 | `getActionDefinitionsBuilder(...).legalFor(...).customFor(...)` |
| **抽象语法** | 规则 = pred + action + mutation | `LegalizeRule` |
| **程序** | 每个 opcode 一组有序规则 | `LegalizeRuleSet` |
| **求值结果** | action + type index + new type | `LegalizeActionStep` |
| **运行时** | 查表、分派、改写、回队列 | `legalizeInstrStep` + `LegalizerPass` |

形式化一点：

~~~text
Query   ::= (opcode, types[], mmos[], immediates[])
Rule    ::= when Predicate do Action [mutate TypeIdx -> LLT]
RuleSet ::= Rule*
Program ::= opcode -> RuleSet

apply(q):
    for r in RuleSet:
        if r.Predicate(q):
            return (r.Action, r.Mutation(q))
    return Unsupported
~~~

这就是一个**有序的守卫重写系统**：

- `Predicate` 决定规则是否匹配；
- `Action` 决定下一步做什么；
- `Mutation` 决定改哪个 type index、改成什么类型；
- 第一条匹配的规则赢，没有回溯。

---

## 2. 全局调用图

先记住这一张图，再看具体规则：

~~~text
LegalizerPass / legalizeMachineFunction
    │
    │  pop_back_val()
    ▼
LegalizerHelper::legalizeInstrStep(MI)
    │
    ├─ GIntrinsic ──▶ LegalizerInfo::legalizeIntrinsic
    │
    ├─ LegalizerInfo::getAction(MI, MRI)
    │       │
    │       ├─ 构造 LegalityQuery
    │       └─ getActionDefinitions(opcode).apply(Query)
    │               │
    │               ├─ LegalizeRule::match(Query)
    │               └─ LegalizeRule::determineMutation(Query)
    │
    ▼
LegalizeActionStep { Action, TypeIdx, NewType }
    │
    ├─ Legal        ──▶ AlreadyLegal
    ├─ NarrowScalar ──▶ LegalizerHelper::narrowScalar
    ├─ WidenScalar  ──▶ LegalizerHelper::widenScalar
    ├─ Lower        ──▶ LegalizerHelper::lower
    ├─ Libcall      ──▶ LegalizerHelper::libcall
    └─ Custom       ──▶ LegalizerInfo::legalizeCustom
                            │
                            ▼
                       Target 自定义 C++
~~~

helper 内部生成新 MI：

~~~text
MachineIRBuilder::build...
    │
    ▼
GISelChangeObserver::createdInstr
    │
    ├─ 普通 generic MI -> InstList
    └─ artifact MI     -> ArtifactList
            │
            ▼
     LegalizationArtifactCombiner
            │
            ├─ 能折叠 -> 删除/替换
            └─ 不能折叠 -> 回到 InstList
~~~

关键源码位置：

| 内容 | 位置 |
|---|---|
| `LegalizeRule` / `LegalizeRuleSet` | `LegalizerInfo.h:400-530` |
| `actionIf` 与各种 DSL helper | `LegalizerInfo.h:471-1152` |
| `LegalizeRuleSet::apply` | `LegalizerInfo.cpp:194-212` |
| `getAction(MI, MRI)` | `LegalizerInfo.cpp:341-375` |
| `legalizeInstrStep` | `LegalizerHelper.cpp:122-160` |
| 工作队列与观察者 | `Legalizer.cpp:123-309` |
| ToyCPU 规则表 | `ToyCPULegalizerInfo.cpp:61-343` |
| ToyCPU custom 回调 | `ToyCPULegalizerInfo.cpp:348-963` |

---

## 3. 走读方法：一条规则只问五个问题

拿到任何一条规则，按这个顺序问：

1. **它属于哪个 opcode 的 RuleSet？**
2. **Query 长什么样？** type0/type1 分别是什么？有没有 MMO/imm？
3. **哪条规则先匹配？** 为什么不是前面的规则？
4. **Action 和 Mutation 是什么？** 改哪个 type index，改成什么？
5. **哪个 helper 执行？它生成哪些新 MI？这些新 MI 怎么回到队列？**

这五个问题能把“读代码”变成“执行一遍 DSL”。

---

## 4. 走读一：`G_ICMP v4s1` —— Custom 与跨指令改写

### 4.1 规则表

`ToyCPULegalizerInfo.cpp:154-159`：

~~~cpp
getActionDefinitionsBuilder(G_ICMP)
    .legalFor({{s32, s32}, {p0, p0}, {v4s32, v4s32}})
    .customFor({{v4s1, v4s32}})
    .widenScalarToNextPow2(0, 32)
    .clampScalar(0, s32, s32)
    .clampScalar(1, s32, s32);
~~~

按顺序读：

~~~text
1. 结果 s32 / 输入 s32        -> Legal
2. 结果 p0  / 输入 p0         -> Legal
3. 结果 v4s32 / 输入 v4s32    -> Legal
4. 结果 v4s1 / 输入 v4s32     -> Custom
5. 其它标量结果               -> WidenScalar 到 32 位
6. 结果/输入宽度越界           -> clamp 到 s32
~~~

### 4.2 Query 是什么

`G_ICMP` 的 type index：

| type index | 含义 |
|---|---|
| `type0` | 比较结果 |
| `type1` | 两个输入操作数 |
| predicate | immediate/unknown，不占 type index |

所以：

~~~text
%6:_(v4s1) = G_ICMP intpred(slt), %0:_(v4s32), %2:_(v4s32)
~~~

对应：

~~~text
Query.Opcode = G_ICMP
Query.Types[0] = v4s1
Query.Types[1] = v4s32
~~~

`legalFor` 的三个 pair 都不匹配，`customFor({{v4s1, v4s32}})` 匹配。

### 4.3 解释器分派

`legalizeInstrStep` 拿到：

~~~text
LegalizeActionStep { Action = Custom, TypeIdx = 0, NewType = {} }
~~~

进入：

~~~cpp
case Custom:
  return LI.legalizeCustom(*this, MI, LocObserver)
             ? Legalized
             : UnableToLegalize;
~~~

### 4.4 Custom 做了什么

`ToyCPULegalizerInfo.cpp:611-657`：

~~~cpp
case TargetOpcode::G_ICMP: {
  Register Dst = MI.getOperand(0).getReg();
  LLT DstTy = MRI.getType(Dst);
  if (DstTy != V4S1)
    return false;

  Register LHS = MI.getOperand(2).getReg();
  Register RHS = MI.getOperand(3).getReg();
  if (MRI.getType(LHS) != V4S32 || MRI.getType(RHS) != V4S32)
    return false;

  CmpInst::Predicate Pred = ...;

  // zext/sext of a 0/1 compare result is the result itself.
  SmallVector<MachineInstr *, 4> Users;
  for (MachineInstr &Use : MRI.use_instructions(Dst))
    Users.push_back(&Use);

  auto NewCmp = MIRBuilder.buildICmp(Pred, V4S32, LHS, RHS);
  Register NewReg = NewCmp.getReg(0);

  for (MachineInstr *Use : Users) {
    if (Use->getOperand(1).getReg() != Dst ||
        MRI.getType(Use->getOperand(0).getReg()) != V4S32)
      continue;

    if (Use->getOpcode() == TargetOpcode::G_ZEXT) {
      MRI.replaceRegWith(Use->getOperand(0).getReg(), NewReg);
      Use->eraseFromParent();
    } else if (Use->getOpcode() == TargetOpcode::G_SEXT) {
      Register ZeroS = MIRBuilder.buildConstant(S32, 0).getReg(0);
      Register ZeroV = MIRBuilder.buildSplatBuildVector(V4S32, ZeroS).getReg(0);
      Register Neg = MIRBuilder.buildSub(V4S32, ZeroV, NewReg).getReg(0);
      MRI.replaceRegWith(Use->getOperand(0).getReg(), Neg);
      Use->eraseFromParent();
    }
  }

  MRI.replaceRegWith(Dst, NewReg);
  MI.eraseFromParent();
  return true;
}
~~~

这段代码说明：**Custom 不是“改一条指令”，而是“改一小片数据流”**。

它做了四件事：

1. 把 `v4s1` 比较改写成 `v4s32` 比较；
2. 扫描所有使用者；
3. 把 `zext(v4s1)` 直接替换成新比较结果；
4. 把 `sext(v4s1)` 改写成 `0 - cmp`；
5. 最后替换旧结果，删除旧 MI。

### 4.5 为什么 `G_SELECT` 可以先被访问

`G_SELECT` 的规则在 `:199-206`：

~~~cpp
getActionDefinitionsBuilder(G_SELECT)
    .legalFor({{s32, s32}, {v4s32, v4s32}, {v4s32, v4s1}})
    .clampScalar(0, s32, s32)
    .clampScalar(1, s32, s32);
~~~

它故意允许 `v4s1` 条件：

~~~text
%34:_(v4s32) = G_SELECT %6:_(v4s1), %0, %2
~~~

Legalizer 反向处理，可能先看到 `G_SELECT`，再看到定义 `%6` 的 `G_ICMP`。
此时 `G_SELECT` 已经是 legal；随后 `G_ICMP` 的 custom 规则把 `%6` 改成 `v4s32`，并更新 `G_SELECT` 的使用。

### 4.6 调用链

~~~text
LegalizerPass
  -> legalizeInstrStep(G_SELECT)      // 先看到，v4s1 条件 legal
  -> legalizeInstrStep(G_ICMP)
      -> getAction -> apply
      -> Custom
      -> ToyCPULegalizerInfo::legalizeCustom
          -> MIRBuilder.buildICmp(V4S32)
          -> MRI.replaceRegWith
          -> MI.eraseFromParent
  -> observer 把新 G_ICMP 放回 InstList
  -> 新 G_ICMP v4s32 查询为 Legal
~~~

**教学点：**

> `Custom` 是逃生舱；它可以看到使用者、改使用者、删除旧指令。
> 但它必须自己维护 SSA/使用关系，框架不会替你做。

---

## 5. 走读二：`G_ADD s64` —— NarrowScalar 与进位链

### 5.1 规则表

`ToyCPULegalizerInfo.cpp:61-63`：

~~~cpp
getActionDefinitionsBuilder({G_ADD, G_SUB, G_AND, G_OR, G_XOR})
    .legalFor({s32, v4s32})
    .clampScalar(0, s32, s32);
~~~

`s64` 不在 `legalFor` 中；`clampScalar(0, s32, s32)` 的 `maxScalar` 规则匹配 `s64 > s32`：

~~~text
Action = NarrowScalar
TypeIdx = 0
NewType = s32
~~~

### 5.2 解释器分派

~~~cpp
case NarrowScalar:
  return narrowScalar(MI, Step.TypeIdx, Step.NewType);
~~~

`LegalizerHelper::narrowScalar` 根据 opcode 进入 `narrowScalarAddSub`：

~~~cpp
case TargetOpcode::G_ADD:
  ...
  return narrowScalarAddSub(MI, TypeIdx, NarrowTy);
~~~

源码：`LegalizerHelper.cpp:1679`。

### 5.3 `narrowScalarAddSub`

`LegalizerHelper.cpp:7370-7463`：

~~~cpp
if (TypeIdx != 0)
  return UnableToLegalize;

Register DstReg = MI.getOperand(0).getReg();
LLT DstType = MRI.getType(DstReg);
if (DstType.isVector())
  return UnableToLegalize;

// 选择 add/sub 的 carry 形式
switch (Opcode) {
case G_ADD:
  OpO = G_UADDO;
  OpE = G_UADDE;
  OpF = G_UADDE;
  break;
...
}

// 把 s64 拆成两个 s32
extractParts(Src1, RegTy, NarrowTy, ...);
extractParts(Src2, RegTy, NarrowTy, ...);

for (int i = 0; i < Parts; ++i) {
  // 低 limb: G_UADDO
  // 高 limb: G_UADDE + carry
}

// 把两个 s32 合回 s64
insertParts(...);
MI.eraseFromParent();
return Legalized;
~~~

### 5.4 生成的 MIR

~~~text
%13:_(s32), %14:_(s32) = G_UNMERGE_VALUES %9:_(s64)
%15:_(s32), %16:_(s32) = G_UNMERGE_VALUES %8:_(s64)
%17:_(s32), %18:_(s1)  = G_UADDO %13, %15
%19:_(s32), %20:_(s1)  = G_UADDE %14, %16, %18
%10:_(s64)             = G_MERGE_VALUES %17, %19
~~~

这是一条 `s64 add` 被展开成：

~~~text
unmerge + 低 limb 加法 + 高 limb 带进位加法 + merge
~~~

### 5.5 为什么 artifact 要特殊处理

`G_UNMERGE_VALUES` / `G_MERGE_VALUES` 是 legalizer 自己产生的 artifact。
它们不会立刻要求 legalize，而是进入 `ArtifactList`：

~~~text
ArtifactList
   -> ArtifactCombiner 尝试折叠
   -> 折叠不掉，再回到 InstList
~~~

这就是 `LegalizerPass` 的两级工作队列：

| 队列 | 内容 | 处理方式 |
|---|---|---|
| `InstList` | 普通 generic MI | 直接 `legalizeInstrStep` |
| `ArtifactList` | merge/unmerge/extend/trunc 等 | 先尝试 combine |
| `RetryList` | 暂时无法合法化的 artifact | 等新 artifact 出现后重试 |

### 5.6 调用链

~~~text
LegalizerPass
  -> legalizeInstrStep(G_ADD s64)
      -> getAction -> apply -> NarrowScalar(s32)
      -> narrowScalar
          -> narrowScalarAddSub
              -> extractParts -> G_UNMERGE_VALUES
              -> G_UADDO / G_UADDE
              -> insertParts -> G_MERGE_VALUES
              -> MI.eraseFromParent
  -> observer 把新 MI 分到 InstList / ArtifactList
  -> ArtifactCombiner 尝试折叠 merge/unmerge
  -> 直到不动点
~~~

**教学点：**

> `NarrowScalar` 不是“把类型改小”，而是一套按 opcode 实现的多指令展开算法。
> 你写的规则只指定“窄到 s32”，具体怎么拆由 helper 决定。

---

## 6. 走读三：`G_PHI s1` —— WidenScalar 与 CFG 边

### 6.1 规则表

`ToyCPULegalizerInfo.cpp:164-166`：

~~~cpp
getActionDefinitionsBuilder(G_PHI)
    .legalFor({s32, p0, v4s32, v2s32})
    .widenScalarToNextMultipleOf(0, 32);
~~~

ToyCPU 的布尔值放在 32 位 GPR 里，所以 `s1` PHI 不能留到寄存器分配。

### 6.2 查询

~~~text
%phi:_(s1) = G_PHI %a:_(s1), %bb1, %b:_(s1), %bb2
~~~

`Query.Types[0] = s1`。
`legalFor` 不匹配；`widenScalarToNextMultipleOf(0, 32)` 匹配，得到：

~~~text
Action = WidenScalar
TypeIdx = 0
NewType = s32
~~~

### 6.3 `LegalizerHelper::widenScalar` 的 PHI 特例

`LegalizerHelper.cpp:3370-3384`：

~~~cpp
case TargetOpcode::G_PHI: {
  assert(TypeIdx == 0 && "Expecting only Idx 0");

  Observer.changingInstr(MI);

  // 每个 incoming 的 terminator 之前插 G_ANYEXT
  for (unsigned I = 1; I < MI.getNumOperands(); I += 2) {
    MachineBasicBlock &OpMBB = *MI.getOperand(I + 1).getMBB();
    MIRBuilder.setInsertPt(OpMBB, OpMBB.getFirstTerminatorForward());
    widenScalarSrc(MI, WideTy, I, TargetOpcode::G_ANYEXT);
  }

  // PHI 本身改成 s32
  MachineBasicBlock &MBB = *MI.getParent();
  MIRBuilder.setInsertPt(MBB, --MBB.getFirstNonPHI());
  widenScalarDst(MI, WideTy);

  Observer.changedInstr(MI);
  return Legalized;
}
~~~

### 6.4 为什么这不是局部改写

普通指令只改自己所在的 block。
PHI 的 incoming 值来自前驱 block，所以必须在**每个前驱的 terminator 之前**插入 `G_ANYEXT`，再改 PHI 自身的类型。

~~~text
bb1:
  ...
  G_ANYEXT s1 -> s32
  G_BR bb3

bb2:
  ...
  G_ANYEXT s1 -> s32
  G_BR bb3

bb3:
  %phi:_(s32) = G_PHI %ext1, %bb1, %ext2, %bb2
~~~

### 6.5 调用链

~~~text
LegalizerPass
  -> legalizeInstrStep(G_PHI s1)
      -> getAction -> apply -> WidenScalar(s32)
      -> widenScalar
          -> G_PHI case
              -> 遍历 incoming
              -> 前驱 terminator 前插 G_ANYEXT
              -> 改 PHI 类型
              -> Observer.changedInstr
  -> 新 G_ANYEXT 进入 InstList / ArtifactList
  -> ArtifactCombiner 可能折叠
~~~

**教学点：**

> `Action` 不只是“改哪条指令”，它还决定“在哪个位置改”。
> PHI 的 helper 是 CFG-aware 的；只看规则表看不出这一点，必须跟到 helper。

---

## 7. 走读四：`G_SDIV s64` —— Libcall 与 ABI

### 7.1 规则表

`ToyCPULegalizerInfo.cpp:87-89`：

~~~cpp
getActionDefinitionsBuilder({G_SDIV, G_UDIV, G_SREM, G_UREM})
    .libcallFor({s32, s64})
    .clampScalar(0, s32, s32);
~~~

`s64` 命中 `libcallFor`：

~~~text
Action = Libcall
~~~

### 7.2 解释器分派

~~~cpp
case Libcall:
  return libcall(MI, LocObserver);
~~~

`LegalizerHelper::libcall`（`:1326-1345`）对 `G_SDIV/G_UDIV/G_SREM/G_UREM` 走：

~~~cpp
LLT LLTy = MRI.getType(MI.getOperand(0).getReg());
unsigned Size = LLTy.getSizeInBits();
Type *HLTy = IntegerType::get(Ctx, Size);
auto Status = simpleLibcall(MI, MIRBuilder, Size, HLTy, LocObserver);
~~~

### 7.3 `simpleLibcall` 到 `createLibcall`

`simpleLibcall`（`:659-670`）：

~~~cpp
auto Libcall = getRTLibDesc(MI.getOpcode(), Size);
SmallVector<CallLowering::ArgInfo, 3> Args;
for (const MachineOperand &MO : llvm::drop_begin(MI.operands()))
  Args.push_back({MO.getReg(), OpType, 0});
return createLibcall(Libcall, {MI.getOperand(0).getReg(), OpType, 0},
                     Args, LocObserver, &MI);
~~~

`createLibcall`（`:594-640`）真正调用目标的 `CallLowering`：

~~~cpp
auto &CLI = *MIRBuilder.getMF().getSubtarget().getCallLowering();

CallLowering::CallLoweringInfo Info;
Info.CallConv = CC;
Info.Callee = MachineOperand::CreateES(Name);
Info.OrigRet = Result;
Info.OrigArgs.append(Args);

if (!CLI.lowerCall(MIRBuilder, Info))
  return UnableToLegalize;
~~~

### 7.4 生成什么 MIR

i64 参数先被拆成两个 s32 limb，再按 ToyCPU ABI 放入 r3..r6，调用 `__divdi3` / `__moddi3` 等：

~~~text
G_UNMERGE_VALUES %a -> lo, hi
G_UNMERGE_VALUES %b -> lo, hi
$r3 = COPY lo
$r4 = COPY hi
$r5 = COPY lo
$r6 = COPY hi
JAL &__divdi3
lo = COPY $r3
hi = COPY $r4
G_MERGE_VALUES lo, hi -> s64 result
~~~

### 7.5 调用链

~~~text
LegalizerPass
  -> legalizeInstrStep(G_SDIV s64)
      -> getAction -> apply -> Libcall
      -> libcall
          -> simpleLibcall
              -> getRTLibDesc
              -> createLibcall
                  -> CallLowering::lowerCall
                      -> ToyCPU ABI 落位
          -> 生成 ADJCALLSTACKDOWN / COPY / JAL / ADJCALLSTACKUP
  -> observer 回收新 MI
~~~

**教学点：**

> `Libcall` 这个 DSL action 不知道 ABI。
> 它把 ABI 交给 `CallLowering`；这也是为什么 `Legalizer` 和 `CallLowering` 必须一起看。

---

## 8. 走读五：`G_SHUFFLE_VECTOR` —— Custom + Unsupported 的规则顺序

### 8.1 规则表

`ToyCPULegalizerInfo.cpp:324-327`：

~~~cpp
getActionDefinitionsBuilder(G_SHUFFLE_VECTOR)
    .customIf(all(typeInSet(0, {v4s32, v2s32}),
                  typeInSet(1, {v4s32, v2s32})))
    .unsupported();
~~~

按 first-match 读：

~~~text
1. 目的/源是 v4s32 或 v2s32 -> Custom
2. 其它类型                  -> Unsupported
~~~

### 8.2 `unsupported()` 不是 `Custom` 的 fallback

这是一个关键语义：

- 如果 Query **匹配** `customIf`，返回 `Custom`；
- `legalizeCustom` 返回 `false` 时，`legalizeInstrStep` 返回 `UnableToLegalize`；
- 它**不会**回头再尝试后面的 `.unsupported()`。

也就是说：

~~~text
customIf 匹配 -> Custom -> legalizeCustom 失败 -> UnableToLegalize
customIf 不匹配 -> unsupported -> UnableToLegalize
~~~

两者最终都可能失败，但路径不同。
这决定了你调试时应该看 `legalizeCustom` 内部的哪个 `return false`。

### 8.3 Custom 的实现

`ToyCPULegalizerInfo.cpp:505-553`：

~~~cpp
case TargetOpcode::G_SHUFFLE_VECTOR: {
  if (MI.getNumOperands() != 4)
    return false;

  LLT DstTy = MRI.getType(MI.getOperand(0).getReg());
  LLT SrcTy = MRI.getType(MI.getOperand(1).getReg());
  LLT Src2Ty = MRI.getType(MI.getOperand(2).getReg());
  ...
  ArrayRef<int> Mask = MI.getOperand(3).getShuffleMask();
  ...
  // 把两个源向量写到 scratch stack slot
  MIRBuilder.buildStore(MI.getOperand(1), Base1, ...);
  MIRBuilder.buildStore(MI.getOperand(2), Base2, ...);

  for (unsigned I = 0; I != DstLanes; ++I) {
    // mask < 0 -> 0
    // 否则按 concatenated index 从两个源向量取 lane
    // 写到输出 scratch slot
  }

  MIRBuilder.buildLoad(MI.getOperand(0), BaseOut, ...);
  MI.eraseFromParent();
  return true;
}
~~~

这是一个“用栈做万能 shuffle buffer”的实现：

~~~text
store src1 -> stack slot 1
store src2 -> stack slot 2
for each output lane:
    load selected 32-bit lane
    store to output slot
load output slot -> destination vector
~~~

### 8.4 调用链

~~~text
LegalizerPass
  -> legalizeInstrStep(G_SHUFFLE_VECTOR)
      -> getAction -> apply
          -> customIf 匹配 -> Custom
      -> legalizeCustom
          -> 检查 operands/types/mask
          -> 创建 3 个 stack slot
          -> store / load / store
          -> MI.eraseFromParent
  -> 新 load/store/ptr_add 进入 InstList
  -> 后续 legalize/select
~~~

**教学点：**

> `.unsupported()` 是规则表的兜底，不是 `Custom` 失败后的重试。
> `Custom` 失败就是失败；它不会自动降级到后面的规则。

---

## 9. 一张表看懂五种 action 的走法

| Action | 规则表写法 | 解释器入口 | helper 里发生什么 | 你要重点看 |
|---|---|---|---|---|
| `Legal` | `.legalFor(...)` | 直接返回 `AlreadyLegal` | 不改写 | 选择器是否真能选 |
| `NarrowScalar` | `.clampScalar` / `.maxScalar` | `narrowScalar` | 拆 limb、进位链、merge/unmerge | `narrowScalarAddSub` 等 opcode 分支 |
| `WidenScalar` | `.widenScalarToNextMultipleOf` | `widenScalar` | 插 extend/trunc，改类型 | PHI 的前驱边、G_ICMP 的源扩展 |
| `Libcall` | `.libcallFor(...)` | `libcall` | 生成 call、参数落位、返回值 | `CallLowering` 与 ABI |
| `Custom` | `.customFor(...)` / `.customIf(...)` | `legalizeCustom` | 任意 target C++ 改写 | 使用者更新、旧 MI 删除 |

---

## 10. 快速掌握这套规则的六步法

### 第 1 步：从一条 MI 开始

不要从整个 `ToyCPULegalizerInfo.cpp` 开始。
先拿一条具体 MIR：

~~~text
%10:_(s64) = G_ADD %9:_, %8:_
~~~

### 第 2 步：找 opcode 的 RuleSet

~~~cpp
getActionDefinitionsBuilder(G_ADD)
    .legalFor({s32, v4s32})
    .clampScalar(0, s32, s32);
~~~

### 第 3 步：确定 type index

查 `GenericOpcodes.td` 或 MCID：

- `G_ADD` 的 dst/src 共用 `type0`；
- `G_ICMP` 的 dst 是 `type0`，src 是 `type1`；
- `G_SELECT` 的 condition 是 `type1`；
- `G_LOAD` 的 ptr 是 `type1`。

### 第 4 步：按顺序模拟 first-match

把规则抄成 `if/elif`：

~~~text
if type0 in {s32, v4s32} -> Legal
elif type0 > s32        -> NarrowScalar(s32)
~~~

### 第 5 步：跟 action 到 helper

~~~text
NarrowScalar -> narrowScalar -> narrowScalarAddSub
WidenScalar  -> widenScalar  -> G_PHI / G_ICMP 特例
Libcall      -> libcall      -> createLibcall -> CallLowering
Custom       -> legalizeCustom -> target 的 switch
~~~

### 第 6 步：看新 MI 如何回队列

helper 里每一条 `MIRBuilder.build...` 都会经过 observer：

- 普通 generic MI -> `InstList`
- artifact -> `ArtifactList`
- artifact 先尝试 combine，失败再回 `InstList`

所以 legalization 不是一次改写，而是**反复执行直到不动点**。

---

## 11. 调试命令

~~~powershell
$llc = 'F:\llvm\build-toycpu\bin\llc.exe'

& $llc -march=toycpu -mtriple=toycpu-unknown-elf `
    -global-isel -global-isel-abort=1 -verify-machineinstrs `
    -stop-after=legalizer -simplify-mir -o - probe.ll

& $llc -march=toycpu -mtriple=toycpu-unknown-elf `
    -global-isel -debug-only=legalizer `
    -stop-after=legalizer -simplify-mir -o NUL probe.ll 2>&1
~~~

看三样东西：

1. `Legalizing:` 当前 MI；
2. `.. Already legal` / `.. Narrow scalar` / `.. Custom legalization` / `.. Convert to libcall`；
3. `.. .. New MI:` 生成了什么。

如果只想看选择失败：

~~~powershell
& $llc -march=toycpu -mtriple=toycpu-unknown-elf -global-isel -o - probe.ll 2>&1 |
    Select-String 'Cannot select|unable to legalize|unable to map'
~~~

---

## 12. 常见坑

| 坑 | 说明 |
|---|---|
| **type index 当 operand index** | `G_ICMP` 的 predicate 不是 type index；`G_SELECT` 的条件是 type1 |
| **忽略规则顺序** | first-match-wins；宽泛规则写在前面会截获特例 |
| **多 opcode builder 后单独定制** | 多 opcode builder 是别名共享，不能再给其中一个加规则 |
| **把 `.unsupported()` 当 Custom 的 fallback** | Custom 返回 false 不会回头匹配后面的规则 |
| **Custom 忘记删除旧 MI** | 旧 MI 不删会留下重复定义/死代码 |
| **Custom 忘记更新使用者** | 改了定义但使用者还指向旧 vreg，SSA 断裂 |
| **把 `NarrowScalar` 当万能** | helper 对 opcode 有 switch，没实现就 `UnableToLegalize` |
| **把 `WidenScalar` 当只改类型** | 它会插 `G_ANYEXT/G_ZEXT/G_SEXT/G_TRUNC`，PHI 还会改前驱边 |
| **忽略 artifact** | merge/unmerge/extend 先走 ArtifactCombiner，不是直接 legalize |
| **以为 `Legal` 等于语义正确** | `Legal` 只表示 selector 能处理，不代表语义一定对 |

---

## 13. 练习

### 练习 A：`G_UMIN s32`

1. 先判断 ToyCPU 的选择器是否能直接选 `G_UMIN`。
2. 如果能，规则应该是什么？
3. 如果不能，应该用 `Custom` 还是 `Lower`？
4. 写出规则，并预测 legalizer 后的 MIR。

### 练习 B：`G_CTPOP s32`

1. 目标没有 popcount 指令。
2. 选择 `Custom` 还是 `Lower`？
3. 如果 `Custom`，写出 `legalizeCustom` 中的展开算法。
4. 用 `-debug-only=legalizer` 检查是否出现 `Custom legalization`。

### 练习 C：`G_SMAX v4s32`

1. 查 `G_SMAX` 的 type index。
2. 查 ToyCPU 是否已有向量比较/选择规则。
3. 判断应该复用 `G_ICMP + G_SELECT`，还是写 `Custom`。
4. 写出规则顺序，并解释为什么不能把 `legalFor` 写在 `customFor` 前面。

---

## 14. 结论

> **学 Legalizer，不要背 API；要把它当成一门小语言：规则是程序，Query 是输入，apply 是求值，legalizeInstrStep 是解释器，helper 是操作语义，工作队列是不动点运行时。**

一句话记忆：

> **先看 first-match，再跟 action，最后看 helper 生成的新 MI 怎么回队列。**