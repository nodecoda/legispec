# AArch64 GlobalISel 固定向量合法性实验（第一片）

Date: 2026-10-20
Status: 已证明（hand + generated，176 查询，0 分歧）

## 1. 范围

`rules/aarch64-vector.lspec` 转写
`llvm/lib/Target/AArch64/GISel/AArch64LegalizerInfo.cpp` 的三个区域：

```text
303-310  G_SMIN / G_SMAX / G_UMIN / G_UMAX
334-337  G_ABDS / G_ABDU / G_UAVGFLOOR / G_UAVGCEIL / G_SAVGFLOOR / G_SAVGCEIL
431      G_CLMUL
```

固定配置：

```text
triple      = aarch64-unknown-linux-gnu
cpu         = generic
NEON/FPARMv8 enabled
HasCSSC     = false
HasSVE      = false
```

`HasCSSC` 控制的两条 builder 调用在构造期为假，因此不生成规则，和
`aarch64-scalar.lspec` 的处理方式相同。

## 2. 为什么选这一片

它不是最窄的通路（`G_CLMUL` 才是），但它同时满足三个条件：

1. 只依赖一条新的向量原语族：`clampNumElements`；
2. 规则顺序清晰：`legalFor` → 三组 clamp（每组 min 再 max）→ `lower`；
3. 能同时覆盖“合法向量”“增加 lane”“减少 lane”“无规则兜底”四种形态。

`G_CLMUL` 被顺带放进来作为对照：它只有一条 `legalFor`，没有 `lower`，
因此非 `v8s8/v16s8` 查询必须以 `Unsupported` 结束。这验证了 DSL 的
first-match + fallback-to-Unsupported 语义。

## 3. DSL 扩展

### 3.1 谓词

新增 `FixedVectorElemCountCmp`：

```python
is_vector
and ty.elem == elem
and cmp(ty.lanes, op, count)
```

生成到 C++ 时逐字对应源码：

```cpp
Q.Types[i].isFixedVector()
  && Q.Types[i].getElementType() == <LLT>
  && Q.Types[i].getNumElements() <op> <count>
```

三个判断必须放在同一个节点里：

- 先 `isFixedVector()` 才能调用 `getNumElements()`，否则 scalar 会断言；
- 元素类型必须精确相等，不能用位宽近似（`s8` 与 `p0:8` 是不同 LLT）；
- lane 比较只在固定向量上定义。

### 3.2 宏

```text
clamp_num_elements N, MIN_VEC, MAX_VEC;
clamp_min_num_elements N, ELT, COUNT;
clamp_max_num_elements N, ELT, COUNT;
```

`clamp_num_elements 0, v8s8, v16s8;` 展开为两条规则，顺序与 C++ 相同：

```text
MoreElements  when fixed_vector(t0,s8) and lanes(t0) < 8   -> v8s8
FewerElements when fixed_vector(t0,s8) and lanes(t0) > 16  -> v16s8
```

两个边界向量必须元素类型相同、至少 2 lane；`COUNT < 2` 被拒绝，因为
LLVM 的 `v1` 会退化成 scalar，不是可表示的向量 LLT。

### 3.3 生成器

`clamp` 规则生成：

```cpp
.moreElementsIf(predicate, mutation)
.fewerElementsIf(predicate, mutation)
```

第一次构建失败在这里：谓词 lambda 引用了函数内的 `const LLT s8`，但生成的是
无捕获 `[]`。LLVM 自己的 builder 用 `[=]`，修正后通过。回归测试
`test_vector_clamp_predicate_captures_element_llt` 固定了这一点。

## 4. 域

```text
types = s32, s64,
        v2s8, v4s8, v8s8, v16s8, v32s8,
        v2s16, v4s16, v8s16, v16s16,
        v2s32, v4s32, v8s32,
        v2s64, v4s64
```

每个 opcode 一个 type slot（三条 ruleset 都只读 `Types[0]`），共
11 opcodes × 16 types = 176 查询。所有 clamp 结果仍落在域内，所以
`R ⊆ D`，`closure_outside_d_count = 0`。

## 5. 结果

```text
strict:     errors=0 warnings=14 unknown=0
hand:       queries=176 divergences=0 rules=10/11
generated:  queries=176 divergences=0 rules=10/11
claims:     33 proven, 0 failed（本片落地时为 31）
```

代表性观测：

```text
G_SMIN  [v4s8]  -> MoreElements  v8s8    (clamp min s8)
G_SMIN  [v32s8] -> FewerElements v16s8   (clamp max s8)
G_SMIN  [v2s16] -> MoreElements  v4s16   (clamp min s16)
G_SMIN  [v8s32] -> FewerElements v4s32   (clamp max s32)
G_SMIN  [v4s64] -> Lower                 (no s64 clamp)
G_SMIN  [s32]   -> Lower                 (not a fixed vector)
G_ABDS  [v4s32] -> Legal
G_ABDS  [v8s32] -> Lower
G_CLMUL [v8s8]  -> Legal
G_CLMUL [v4s8]  -> Unsupported           (no fallback rule)
```

## 6. 唯一未命中规则

`G_SMIN` 规则 5 是：

```text
clampNumElements(0, v2s32, v4s32) 的 min 半条
MoreElements when fixed_vector(t0,s32) and lanes(t0) < 2
```

LLVM 无法表示 `v1`：`ElementCount::getFixed(1)` 是 scalar，
`LLT::vector` 会断言。因此这条规则在 C++ 表里真实存在，但永远不可能命中。

处理方式不是删掉它，而是：

- 保留它，保证规则数、规则序号、first-match 顺序与 C++ 完全一致；
- 在 `witness/aarch64-vector-unreachable.witness` 中登记 `unreachable = 5`；
- 在 claim 里声明 `declared_unreachable = 1`。

这是“构造 + 证伪”与“攒错误”的区别：不是把一条红规则改成绿，而是把
“LLVM 表里有一条结构死规则”这个事实建模并公开。

## 7. 已知告警

strict check 报 14 条 `K001/K002`：`legalFor` 与 `lower`、clamp 与 `lower`
在同一批查询上重叠，且重叠是顺序敏感的。这是 LLVM 规则表的真实形态：
`lower()` 是兜底，必须排在所有更具体规则之后。告警不是错误，gate 只要求
`errors=0`。

## 8. 命令

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File `
  F:\llvm-study	oycpu\scripts\check-aarch64-vector.ps1

powershell -NoProfile -ExecutionPolicy Bypass -File `
  F:\llvm-study	oycpu\scripts\check-aarch64-vector.ps1 -BuildOracles
```

单独对拍：

```powershell
python -X utf8 F:\llvm-study	oycpu	ools\legalizer_dsl\conformance.py `
  --rules F:\llvm-study	oycpu	ools\legalizer_dsl
ulesarch64-vector.lspec `
  --oracle F:\llvm-study	oycpu\out\oracle-aarch64\legalizer-oracle-aarch64.exe `
  --out F:\llvm-study	oycpu\outarch64-vector-conformance-report.json `
  --llvm-build F:/llvm/build
```

## 9. 边界

本实验不覆盖：

- scalable vector / SVE；
- `moreElementsToNextPow2`、`scalarizeIf`、`minScalarOrEltIf`；
- `G_ADD/G_MUL` 家族的完整向量规则；
- 固定向量 FP、内存描述、原子组合、调用；
- `LegalizerHelper` 的 MIR 改写。

它证明的仍然只有一件事：

```text
DSL.getAction(q) == LegalizerInfo::getAction(q)
q ∈ 声明的 D ∪ R
```
