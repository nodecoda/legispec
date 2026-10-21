# AArch64 GlobalISel G_CONCAT_VECTORS：custom 逃逸点与 concat bitcast

Date: 2026-10-21
Status: 已证明（hand + generated：28 查询，0 分歧，3/3 规则；custom body 未证明）

## 1. 范围

`rules/aarch64-concat-vectors.ldsl` 转写
`llvm/lib/Target/AArch64/GISel/AArch64LegalizerInfo.cpp`
（LLVM `d855d9e48067026317998cbff734004f328f833d`）1333-1357 行：

```text
getActionDefinitionsBuilder(G_CONCAT_VECTORS)
    .legalFor({{v16s8, v8s8}, {v8s16, v4s16}, {v4s32, v2s32}})
    .customIf([=](const LegalityQuery &Query) {
      return Query.Types[0].isFixedVector() &&
             Query.Types[0].getScalarSizeInBits() < 8;
    })
    .bitcastIf(
        [=](const LegalityQuery &Query) {
          return Query.Types[0].isFixedVector() &&
                 Query.Types[1].isFixedVector() &&
                 Query.Types[0].getScalarSizeInBits() >= 8 &&
                 isPowerOf2_64(Query.Types[0].getScalarSizeInBits()) &&
                 Query.Types[0].getSizeInBits() <= 128 &&
                 Query.Types[1].getSizeInBits() <= 64;
        },
        [=](const LegalityQuery &Query) {
          const LLT DstTy = Query.Types[0];
          const LLT SrcTy = Query.Types[1];
          return std::pair(
              0, DstTy.changeElementSize(SrcTy.getSizeInBits())
                     .changeElementCount(
                         DstTy.getElementCount().divideCoefficientBy(
                             SrcTy.getNumElements())));
        });
```

固定配置：

```text
triple      = aarch64-unknown-linux-gnu
cpu         = generic
features    = none（无 +sve，无 +fullfp16）
pointer     = LLT::pointer(0, 64)，写作 p0:64
extended    = LLT::setUseExtended(true)   （AArch64TargetMachine.cpp:417）
```

槽位语义：

```text
Types[0] = 拼接结果向量
Types[1] = 一个源向量
```

`G_CONCAT_VECTORS` 的源操作数个数是可变的，但所有源操作数同型；合法性查询只投影两个槽。
opcode 契约由 `MachineVerifier.cpp:1611-1631` 定义：源和目的都必须是向量，至少两个源，
所有源同型，且

```text
lanes(t0) == k * lanes(t1)，k >= 2
element_type(t0) == element_type(t1)
```

两个 type slot 只能恢复出 `k = lanes(t0) / lanes(t1)`；`k == 2` 是两源形式的特例，
不是 opcode 契约本身。这一契约被写进 `domain.filter concat_shape_any(t0, t1)`，
而不是写进每条规则。它把“不是 G_CONCAT_VECTORS 形状”的探针排除在证据之外。

查询之所以只投影两个槽，是因为 `GenericOpcodes.td:1609-1614` 只声明了
`type0:$dst` 与 `type1:$src0`，`variable_ops` 不产生新的 TypeIdx
（`LegalizerInfo.cpp:347-376` 按 TypeIdx 去重记录）。

## 2. 规则展开

C++ builder 在这个配置下展开为 3 条规则：

```text
 0  legalFor({(v16s8, v8s8), (v8s16, v4s16), (v4s32, v2s32)})
 1  customIf(t0 fixed vector && t0 element < 8)
 2  bitcastIf(t0/t1 fixed vectors && t0 element >= 8 && pow2 &&
              bits(t0) <= 128 && bits(t1) <= 64,
              concat_bitcast(t1))
```

### 2.1 规则 0：显式合法对

三条 pair 是 128 位、64 位和 32 位的原生拼接形状：

```text
(v16s8, v8s8)      128 位结果 / 64 位源
(v8s16, v4s16)     128 位结果 / 64 位源
(v4s32, v2s32)     128 位结果 / 64 位源
```

### 2.2 规则 1：sub-byte 元素的 custom 逃逸点

谓词只要求结果是固定向量，且元素宽度小于 8 位。它不检查 t1：在真实 MIR 里
t1 已经被 opcode 契约约束，域过滤器保证探针也满足这个契约。

这一条是 **custom escape hatch**。DSL 能证明“规则表把这个查询交给 custom”，
不能证明 `legalizeConcatVectors` 之后生成什么 MIR。capabilities 里声明：

```text
custom.opcodes = ["G_CONCAT_VECTORS"]
custom.symbol  = "AArch64LegalizerInfo::legalizeCustom"
```

门禁会检查 `AArch64LegalizerInfo::legalizeCustom` 中存在
`case TargetOpcode::G_CONCAT_VECTORS:`，但仅此而已。

### 2.3 规则 2：concat bitcast

谓词做五件事：

```text
t0 固定向量
t1 固定向量
t0 元素 >= 8 位且是 2 的幂     <- 与 custom 的 < 8 互补
bits(t0) <= 128
bits(t1) <= 64
```

mutation 不是 `same_size_as`。C++ 的语义是：

```text
DstTy.changeElementSize(SrcTy.getSizeInBits())
     .changeElementCount(
         DstTy.getElementCount().divideCoefficientBy(SrcTy.getNumElements()))
```

即：

1. 把结果元素宽度设成**源的总宽度**（不是源的标量宽度）；
2. 把结果 lane 数除以源的 lane 数（整数除法）。

例：

```text
(v8s8,  v4s8)   -> t0: v8s8  -> changeElementSize(32) -> v8s32 -> lanes 8/4=2 -> v2s32
(v4s16, v2s16)  -> t0: v4s16 -> changeElementSize(32) -> v4s32 -> lanes 4/2=2 -> v2s32
(v4s8,  v2s8)   -> t0: v4s8  -> changeElementSize(16) -> v4s16 -> lanes 4/2=2 -> v2s16
```

三条 bitcast 都保持结果的总位数不变（64 bit、64 bit、32 bit）。

### 2.4 规则 0 与规则 2 的重叠

三条 legal pair 也满足 bitcast 谓词：

```text
(v16s8, v8s8):   elem 8 >= 8，bits(t0)=128 <= 128，bits(t1)=64 <= 64
```

所以静态检查会报：

```text
K001: rules 0/2 both match 3 query/queries in D
K002: overlap is order-sensitive (Legal vs Bitcast)
```

这不是转写错误，而是上游的真实顺序依赖：`legalFor` 在 `bitcastIf` 之前，
命中 legal 后立即短路。witness 用 `negative_legal_is_not_custom` 和
`negative_bitcast_is_not_legal` 把这个顺序固定下来。

规则 1 与规则 2 的谓词不相交（`< 8` vs `>= 8`），所以 custom 不会截走 bitcast。

### 2.5 代表性一步结果

```text
(G_CONCAT_VECTORS, [v16s1,  v8s1])   -> Custom
(G_CONCAT_VECTORS, [v8s4,   v4s4])   -> Custom
(G_CONCAT_VECTORS, [v16s8,  v8s8])   -> Legal
(G_CONCAT_VECTORS, [v8s32,  v4s32])  -> Unsupported（结果 256 位）
(G_CONCAT_VECTORS, [v4s8,   v2s8])   -> Bitcast t0 -> v2s16
(G_CONCAT_VECTORS, [v4s16,  v2s16])  -> Bitcast t0 -> v2s32
(G_CONCAT_VECTORS, [v8s8,   v4s8])   -> Bitcast t0 -> v2s32
(G_CONCAT_VECTORS, [v16s8,  v4s8])   -> Bitcast t0 -> v4s32   (k=4)
(G_CONCAT_VECTORS, [v16s8,  v2s8])   -> Bitcast t0 -> v8s16   (k=8)
(G_CONCAT_VECTORS, [v8s8,   v2s8])   -> Bitcast t0 -> v4s16   (k=4)
```

`unsupported_256_bit_result` 是 size 守卫的结果，不是一条 fallback 规则：
`v8s32/v4s32` 的 `bits(t0) = 256 > 128`，没有规则命中。

## 3. 上游发现

### 3.1 mutation 在 apply 时立即求值

`LegalizeRuleSet::apply` 在谓词命中后立即调用 `Mutation.apply(Query)`，
所以 bitcast 的 lambda 不是“等 LegalizerHelper 有空再算”。这意味着
谓词只保证 `t0/t1` 是向量，不保证 lane 数可整除。`concat_shape_any`
过滤器保证进入 D 的查询满足契约，所以真实路径不会触发；但如果绕过 filter
直接构造非契约形状，lambda 仍会真的算出 `2 / 4 == 0` lane。T017 是模型对
这条边界的防御，而不是对上游行为的修正。

### 3.2 `divideCoefficientBy` 是普通整数除法

`ElementCount::divideCoefficientBy` 的实现是：

```cpp
return LeafTy::get(getKnownMinValue() / RHS, isScalable());
```

没有断言、没有向上取整。0 lane 的 `ElementCount` 不是 scalar，于是
`LLT::scalarOrVector` 会走 `LLT::vector(EC(0), ...)`，得到一个零 lane 的 LLT。
这在 C++ 里“能构造”，但不是任何真实 G_CONCAT_VECTORS 查询的形状。

DSL 的选择是：`ConcatBitcast.eval` 在 lane 数不可整除时抛 `T017`，
并把“域外后继”建模成边界状态，而不是继续喂回规则表。理由见 7.1。

### 3.3 生成端没有 `isFixedVector` helper

`LegalityPredicates` 只有 `isVector(TypeIdx)`，没有 `isFixedVector`。
第一版 codegen 直接发射 `isFixedVector(0)`，oracle 编译失败：

```text
error: use of undeclared identifier 'isFixedVector'
```

修正后 `fixed_vector(tN)` 内联为：

```cpp
LegalityPredicate([](const LegalityQuery &Q) {
  return Q.Types.size() > N && Q.Types[N].isFixedVector();
})
```

这条只有在真正编译生成代码时才会暴露，说明“生成 == 手写”的 oracle 对比
不只是比对策略表，也在编译期检查 codegen 的 API 使用。

### 3.4 k=2 是一个特例，不是 opcode 契约

`MachineVerifier.cpp:1611-1631` 要求 `lanes(dst) == k * lanes(src)` 且
`k >= 2`，`G_BUILD_VECTOR`/多源拼接可以产生 k=4、k=8。第一版过滤器只写了
`k == 2`，把大量合法探针挡在证据集之外。这不是“安全边界”，而是覆盖债：

```text
边界必须来自 opcode 契约，
不能来自“我只想到两源”这个实现习惯。
```

改成 `concat_shape_any` 后域从 15 条增到 28 条，oracle 仍然 0 分歧；
新增的 13 条里有 4 条 Bitcast、9 条 Unsupported，全部与 LLVM 一致。

## 4. 结果

```text
D                     28 查询
R (closure within D)   9 查询（q0 的 9 条）
closure boundary       2 后继（bitcast 投影离开契约）
compared              28
divergences            0
rules                  3/3
静态检查               errors=0 warnings=31 unknown=0
```

28 条 D 查询的分布：

```text
Custom        9   (s1/s4 元素)
Legal         3   (三个 legalFor pair)
Bitcast       7   (s8/s16 元素)
Unsupported   9   (256 位结果或 s64 元素)
```

warnings 31 = K001/K002（规则 0/2 重叠）+ 29 条 C004（AArch64 源里还有
其它 opcode 的 custom case，但不在本片规则内）。C004 是预期噪声，
不是本片的失败。

## 5. 本片新增的 DSL 能力

| DSL | 语义 | C++ |
|---|---|---|
| `fixed_vector(tN)` | 固定向量判定 | `LLT::isFixedVector()` |
| `concat_shape(t0, t1)` | 两源特例：lane 恰好 2 倍 + 元素 LLT 相等 | 无单点 helper，opcode 契约 |
| `is_pow2(elem_bits(tN))` | 元素宽度是 2 的幂 | `isPowerOf2_64(getScalarSizeInBits())` |
| `is_pow2(bits(tN))` | 总宽度是 2 的幂 | `isPowerOf2_64(getSizeInBits())` |
| `concat_bitcast(tN)` | 源总宽 + 结果 lane 整除 | `changeElementSize` + `divideCoefficientBy` |
| `concat_shape_any(t0, t1)` | n 元拼接契约：`lanes(t0) % lanes(t1) == 0 && k >= 2` + 元素 LLT 相等 | `MachineVerifier.cpp:1611-1631` |
| `Domain.accepts(q)` | q 是否在声明的 D 内 | 无（DSL 的证明边界） |

`is_pow2(elem_bits(tN))` 与 `is_pow2(bits(tN))` 的区别在 lane 数不是 2 的幂时显现：

```text
v3s8:  elem 8 是 2 的幂，total 24 不是
v4s8:  两者都是
v3s24: 两者都不是
```

本片必须用 elem 版本，因为 C++ 检查的是 `getScalarSizeInBits()`。

覆盖分类：

```text
methods=63 classified=63 unclassified=0
```

没有新增未分类构造；四个新原语都落在既有 core/macro 分类下。

## 6. 本片新增文件

```text
rules/aarch64-concat-vectors.ldsl
aarch64-concat-vectors-capabilities.json
witness/aarch64-concat-vectors.witness（12 条）
tests/test_concat_primitives.py（16 个测试）
out/codegen-aarch64-concat-vectors/（AArch64ConcatVectorsGen）
scripts/check-aarch64-concat-vectors.ps1
```

capabilities：

```text
custom = {G_CONCAT_VECTORS}   symbol = AArch64LegalizerInfo::legalizeCustom
libcall = {}
lower   = {}
```

## 7. 边界

- 只比较 `LegalizerInfo::getAction` 一步；
- 不包含 `LegalizerHelper::legalizeInstrStep` 的 MIR 改写与迭代 Legalizer pass；
- 不包含指令选择、寄存器分配与 codegen；
- 不覆盖 scalable vector（SVE）、内存描述符、原子、call/ABI；
- **不证明 custom handler body**：规则 1 只证明“策略层交给 custom”，
  `legalizeConcatVectors` 生成的 MIR 不在本片内；
- 域为 `concat_shape_any(t0, t1)`：`k >= 2` 且 lane 整除；域外探针不是证据。

### 7.1 custom 是决策函数与改写函数的分界线

这一片把 G_FCMP 的方法论结论落到了 custom 密集的 opcode 上：

```text
getAction(q)            决策函数：下一步做什么
legalizeInstrStep(...)  改写函数：具体怎么改 MIR
custom                  两者的分界线
```

DSL 能完整描述 `getAction`，也能描述“哪些 opcode 会走到 custom”这个
**目标侧接口契约**；但它不能描述 custom handler 内部生成了什么指令。
所以正确的定位仍是：

```text
DSL = 决策函数的完整规范 + 改写函数的目标侧接口契约
DSL != 完整语义规范
```

`custom` 的逃逸设施正是 DSL 不能单独作为规范的原因。这不是缺陷，而是
刻意的证明边界：custom 不是“漏掉的规则”，而是“规则表把控制权交给 C++”
的那个点。capabilities 层能验证“每个 custom opcode 是否有处理体”，
但不能验证“处理体是否语义正确”；后者需要另一套针对 MIR 改写的规范与测试。

### 7.2 bitcast 后继为什么是域外状态

以 `(v8s8, v4s8)` 为例，规则 2 把 t0 改成 `v2s32`，但 t1 仍是 `v4s8`。
新状态不再满足 `lanes(t0) == k * lanes(t1)`：

```text
2 != 2 * 4，也不是任何 k >= 2
```

k=4 的例子更能说明问题：`(v16s8, v4s8) -> v4s32`，t1 仍是 `v4s8`，
`lanes(t0) / lanes(t1) == 1`，掉出了 `k >= 2`。

这是一个**策略投影的产物**，不是真实 MIR：真实 legalizer 会由 helper 重建
整条指令，源操作数也会被 bitcast。DSL 只改了规则指定的 type index，
所以后继落在 opcode 契约之外。

处理方式：

```text
ConcatBitcast.eval          lane 不可整除 -> T017
Domain.accepts(q)           filter 不成立 -> q 不在 D
interp.legalize             后继出域 -> 状态 OutOfDomain，停止推进
conformance.closure_queries 后继出域 -> 记入 boundary，不加入 R
```

于是：

```text
D 内 28 条查询         全部可判定
q0 闭包 9 条           全部在 D 内
2 条 bitcast 后继       记为 boundary，不计入 R
```

这不是放宽 T017，而是把“T017 是本模型的定义域边界”写清楚：
C++ 会算出零 lane 的 LLT，DSL 拒绝为这个非查询形状提供语义。

## 8. 复现

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File `
  F:\llvm-study\toycpu\scripts\check-aarch64-concat-vectors.ps1
```

或先重建 oracle（会重新生成 `out/codegen-aarch64-concat-vectors/`）：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File `
  F:\llvm-study\toycpu\scripts\check-aarch64-concat-vectors.ps1 -BuildOracles
```

输出应为：

```text
strict check    errors=0 warnings=31 unknown=0 rules=3
hand vs LLVM    28 queries, 0 divergences, 3/3
gen  vs LLVM    28 queries, 0 divergences, 3/3
claim ledger    claims=43 proven=43
real MIR shapes  events=11 unique=11 k=2:9 k=4:1 k=8:1
                 contract_violations=0 action_mismatches=0
```

### 8.1 真实 MIR 审计

`notes/aarch64-concat-real-mir.md` 记录了配套的 in-vivo 审计：用
`llc -debug-only=legalizer` 抓取真正进入 `getAction` 的 concat 查询。
结果是 11 条查询（k=2:9 / k=4:1 / k=8:1），全部在 D 内，0 契约违约，
0 策略分歧。k=4/8 来自手写 MIR，证明放宽后的域不是假想探针。

审计还固定了一个流水线事实：IRTranslator 对 shufflevector 发射
`G_SHUFFLE_VECTOR`，`G_CONCAT_VECTORS` 由 pre-legalizer combiner 生成。
所以“IRTranslator 的输出”不等于“legalizer 看到的 opcode 形状”。

整套门禁：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File `
  F:\llvm-study\toycpu\scripts\check-legalizer-suite.ps1
```
