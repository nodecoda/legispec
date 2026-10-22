# AArch64 GlobalISel G_ADD 家族向量纵切实战

Date: 2026-10-20
Status: 已证明（hand + generated，120 查询，0 分歧）

## 1. 范围

`rules/aarch64-vector-arith.lspec` 转写
`llvm/lib/Target/AArch64/GISel/AArch64LegalizerInfo.cpp` 的 181-206 行：

```text
getActionDefinitionsBuilder({G_ADD, G_SUB, G_AND, G_OR, G_XOR})
```

固定配置：

```text
triple      = aarch64-unknown-linux-gnu
cpu         = generic
HasSVE      = false
HasCSSC     = false
```

`HasSVE` 的 `legalFor` 分支在构造期为假。LLVM 的
`LegalizeRuleSet::legalFor(bool Pred, ...)` 直接 `return *this`，不生成规则。
因此本切片不把 scalable vector 写进 DSL 域，也不写一条“恒假规则”。

## 2. 为什么选这一片

上一片 `aarch64-vector.lspec` 只用了 `clampNumElements`，规则形状是
“合法集合 + lane clamp + lower”。这一片第一次同时打开三个元素级策略：

```text
minScalarOrEltIf(lanes <= 2,  0, s32);
minScalarOrEltIf(lanes <= 4,  0, s16);
minScalarOrEltIf(lanes <= 16, 0, s8);
scalarizeIf(scalarOrEltWiderThan(0, 64), 0);
moreElementsToNextPow2(0);
```

它们分别覆盖：

- 短向量把元素位宽抬到最小值；
- 元素位宽超过 64 时标量化；
- lane 数不是 2 的幂时向上取整。

三者与前面的 `legalFor`、`clampScalar`、`clampMaxNumElements`、
`clampNumElements` 共享一张 first-match 表，所以能验证“规则顺序”本身，
而不只是单条规则。

## 3. 语义核实

实现前先读 LLVM 源码，确认了三个容易凭直觉写错的地方。

### 3.1 `widenScalarToNextPow2` 对向量永不命中

`LegalityPredicates.cpp`：

```cpp
LegalityPredicate LegalityPredicates::sizeNotPow2(unsigned TypeIdx) {
  return [=](const LegalityQuery &Query) {
    const LLT QueryTy = Query.Types[TypeIdx];
    return QueryTy.isScalar() &&
           !llvm::has_single_bit<uint32_t>(QueryTy.getSizeInBits());
  };
}
```

所以 `.widenScalarToNextPow2(0)` 不是“把 lane 数变成 2 的幂”，也不是
“把向量总位数变成 2 的幂”。它只处理非 2 的幂的 **scalar**。DSL 保留
`widen_to_next_pow2 0, 0;`，让规则索引与 C++ 对齐；在向量域它是一条第
2 条结构性空规则。

### 3.2 `minScalarOrEltIf` 的谓词

`LegalizerInfo.h`：

```cpp
actionIf(WidenScalar,
         all(Predicate, scalarOrEltNarrowerThan(TypeIdx, Ty.getScalarSizeInBits())),
         changeElementSizeTo(...))
```

其中：

```cpp
QueryTy.getScalarSizeInBits() < Size
```

所以本切片的三条规则可以严格写成：

```text
lanes(t0) <= N and elem_bits(t0) < target
```

DSL 新增：

```text
min_scalar_or_elt_if 0, lanes <= 2, s32;
```

### 3.3 `scalarizeIf` 的谓词

```cpp
scalarizeIf(scalarOrEltWiderThan(0, 64), 0)
```

展开为：

```text
FewerElements when vector(t0) and elem_bits(t0) > 64 -> element_of(t0)
```

DSL 不需要新宏，直接用 core rule 表达：

```text
fewer t0 to element_of(t0) when vector(t0) and elem_bits(t0) > 64;
```

### 3.4 `moreElementsToNextPow2`

`LegalityPredicates::numElementsNotPow2` 要求 `isFixedVector()` 且 lane 数
不是 2 的幂。`LegalizeMutations::moreElementsToNextPow2` 计算
`1 << Log2_32_Ceil(numElements)`，再把元素个数改成这个值。

DSL 新增：

```text
more_elements_to_next_pow2 0;
```

对应 `NumElementsNotPow2` 谓词和 `NextPow2Lanes` mutation。

## 4. 完整规则表

```text
1  legal_for {s32,s64,v8s8,v16s8,v4s16,v8s16,v2s32,v4s32,v2s64}
2  widenScalarToNextPow2(0)
3  clampScalar(0,s32,s64) min
4  clampScalar(0,s32,s64) max
5  clampMaxNumElements(0,s8,16)
6  clampMaxNumElements(0,s16,8)
7  clampNumElements(0,v2s32,v4s32) min
8  clampNumElements(0,v2s32,v4s32) max
9  clampNumElements(0,v2s64,v2s64) min
10 clampNumElements(0,v2s64,v2s64) max
11 minScalarOrEltIf(lanes<=2,  0, s32)
12 minScalarOrEltIf(lanes<=4,  0, s16)
13 minScalarOrEltIf(lanes<=16, 0, s8)
14 scalarizeIf(elt > 64, 0)
15 moreElementsToNextPow2(0)
```

第 7 和第 9 条是各自 clamp 的 min 半条，要求 `lanes < 2`。LLVM 的
`ElementCount::getFixed(1)` 是 scalar，`LLT::vector(1, ...)` 不可表示，
所以这两条结构性不可达。保留它们，规则数与序号才和 C++ 一致。

## 5. 域

```text
s8, s16, s24, s32, s64, s128,
v2s8, v4s8, v6s8, v8s4, v8s8, v16s8, v32s8,
v3s16, v4s16, v6s16, v8s16, v16s16,
v2s32, v4s32, v8s32,
v2s64, v4s64,
v2s128
```

24 个类型 × 5 个 opcode = 120 条查询。每个 opcode 一个 type slot。
所有一步改写结果仍在域内，`closure_outside_d_count = 0`。

刻意放进去的边角：

- `s24`：非 2 的幂 scalar，触发规则 2；
- `v2s8` / `v4s8` / `v8s4`：分别触发三条 `minScalarOrEltIf`；
- `v6s8` / `v3s16`：非 2 的幂 lane，触发 lane 向上取整；
- `v2s128`：元素位宽 > 64，触发标量化；
- `v32s8` / `v16s16`：分别触发 s8/s16 的 lane 上限；
- `v8s32` / `v4s64`：分别触发 s32/s64 的 `clampNumElements` 上限。

## 6. 代表结果

```text
G_ADD [s24]    -> WidenScalar    s32
G_ADD [s8]     -> WidenScalar    s32
G_ADD [s128]   -> NarrowScalar   s64
G_ADD [v2s8]   -> WidenScalar    v2s32
G_ADD [v4s8]   -> WidenScalar    v4s16
G_ADD [v8s4]   -> WidenScalar    v8s8
G_ADD [v2s128] -> FewerElements  s128
G_ADD [v6s8]   -> MoreElements   v8s8
G_ADD [v32s8]  -> FewerElements  v16s8
G_ADD [v16s16] -> FewerElements  v8s16
G_ADD [v8s32]  -> FewerElements  v4s32
G_ADD [v4s64]  -> FewerElements  v2s64
```

注意 `v6s8`：它先经过所有标量规则、lane 上限和元素最小值，均不命中，
最后才由 `moreElementsToNextPow2` 处理。这正是本片要验证的“顺序交互”。

## 7. 验证

```text
strict:     errors=0 warnings=9 unknown=0
hand:       queries=120 divergences=0 rules=13/15
generated:  queries=120 divergences=0 rules=13/15
claims:     33 proven, 0 failed
```

命令：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File `
  F:\llvm-study	oycpu\scripts\check-aarch64-vector-arith.ps1
```

generated 路径新增 oracle：

```text
legispec-oracle-aarch64-vector-arith-gen.exe
namespace: AArch64VectorArithGen
```

## 8. 本片新增的 DSL 能力

| 原语 | 状态 | 语义 |
|---|---|---|
| `min_scalar_or_elt_if N, lanes <cmp> K, elt` | macro | `lanes` 谓词 + `elem_bits` 最小值，产生 `WidenScalar` |
| `more_elements_to_next_pow2 N` | macro | fixed vector 且 lane 数非 2 的幂 -> 向上取整 |
| `fewer tN to element_of(tN) when ...` | core | `scalarizeIf` 的直接写法 |
| `NumElementsNotPow2` | predicate | 精确对应 `isFixedVector() && !isPowerOf2_32(lanes)` |
| `NextPow2Lanes` | mutation | 精确对应 `moreElementsToNextPow2(TypeIdx)` |

construct coverage 从 19 个 `none` 降到 15 个；`minScalarOrEltIf`、
`moreElementsToNextPow2` 升为 `macro`，`scalarize` / `scalarizeIf` 升为
`core`。

## 9. 边界

本片不覆盖：

- scalable vector / SVE；
- `G_MUL`（另一个 opcode，规则形状接近但它是独立 ruleset）；
- FP 向量、内存描述、原子、调用；
- `LegalizerHelper` 的 MIR 改写；
- 迭代 Legalizer pass；
- 指令选择与 codegen。

证明仍然只有一条：

```text
DSL.getAction(q) == LegalizerInfo::getAction(q)
q ∈ 声明的 D ∪ R
```
