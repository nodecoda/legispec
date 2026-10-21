# AArch64 GlobalISel G_SHL / G_ASHR / G_LSHR 双槽位纵向切片

Date: 2026-10-20
Status: 已证明（hand + generated：270 查询，0 分歧，19/21 规则）

## 1. 范围

`rules/aarch64-vector-shift.ldsl` 转写
`llvm/lib/Target/AArch64/GISel/AArch64LegalizerInfo.cpp`
（LLVM `d855d9e48067026317998cbff734004f328f833d`）234-265 行：

```text
getActionDefinitionsBuilder({G_SHL, G_ASHR, G_LSHR})
    ...
    .maxScalarEltSameAsIf(isVector(0), 1, 0);
```

固定配置：

```text
triple      = aarch64-unknown-linux-gnu
cpu         = generic
HasSVE      = false
```

三个 opcode 共享同一个 builder，因此共享一个 ruleset。它们没有 `HasSVE`
的 `legalFor` 分支；这不是省略，是源码事实。

这是**第一个约束两个类型槽位关系的切片**。此前的 scalar、pointer、atomic、
G_ADD 家族、G_MUL 都只约束 `Types[0]`（或分别独立约束），规则之间不读对方的
形状。这里 `Types[1]`（移位量）必须是源类型的函数，规则顺序开始承载语义。

## 2. 为什么单独成片

`G_SHL` 家族的规则链在结构上与前几片不同：

1. `legalFor` 是**成对类型**，不是单类型集合；
2. 两条 `widenScalarToNextPow2` 分别作用于槽 1 和槽 0，顺序不能交换；
3. `minScalarSameAs` 用**标量宽度**与**总宽度**比较，是不对称谓词；
4. `min/maxScalarEltSameAsIf` 会把源类型整体复制到槽 1，可能把标量变成向量；
5. 类内出现两个真实二周期（见第 4 节）。

因此它必须独立成片，且必须扩展 DSL 的类型系统，而不能复用已有的
`min_scalar_or_elt_if` / `same_size_as`。

## 3. 规则展开

C++ builder 展开为 21 条规则，索引与源码顺序一致：

```text
 0  customIf(!vector(t0) && bits(t0)==32 && bits(t1)==32)
 1  legalFor 10 对
 2  widenScalarToNextPow2(1)
 3  widenScalarToNextPow2(0)
 4  clampScalar(1, s32, s64) min
 5  clampScalar(1, s32, s64) max
 6  clampScalar(0, s32, s64) min
 7  clampScalar(0, s32, s64) max
 8  clampNumElements(0, v8s8, v16s8) min
 9  clampNumElements(0, v8s8, v16s8) max
10  clampNumElements(0, v4s16, v8s16) min
11  clampNumElements(0, v4s16, v8s16) max
12  clampNumElements(0, v2s32, v4s32) min
13  clampNumElements(0, v2s32, v4s32) max
14  clampNumElements(0, v2s64, v2s64) min
15  clampNumElements(0, v2s64, v2s64) max
16  moreElementsToNextPow2(0)
17  minScalarSameAs(1, 0)
18  scalarizeIf(scalarOrEltWiderThan(0, 64), 0)
19  minScalarEltSameAsIf(isVector(0), 1, 0)
20  maxScalarEltSameAsIf(isVector(0), 1, 0)
```

规则 0 在 `legalFor` 之前，所以 `(s32, s32)` 的答案是 `Custom`，尽管
legalFor 集合里也包含 `(s32, s32)`。这种遮蔽是上游的真实行为，由一条负向
witness 钉住。

规则 12 和 14 结构上不可达：它们的 min 半需要少于 2 lane 的固定向量，而
LLVM 把 `v1` 表示为 scalar。两条都在
`witness/aarch64-vector-shift.witness` 中显式声明为 `unreachable`，不是静默丢弃。

## 4. 三个真实上游发现（本片核心产物）

全部用真实 AArch64 oracle 单条验证得到。

### 4.1 两个槽位之间存在真实二周期

```text
(v2s128, s64)   -> WidenScalar   t1 -> s128
(v2s128, s128)  -> NarrowScalar  t1 -> s64
```

路径是：

- 规则 17 `minScalarSameAs(1, 0)`：`scalar_bits(t0)=128 > bits(t1)=64`，
  于是把槽 1 加宽到 s128；
- 新查询落入规则 5 `clampScalar(1, s32, s64)` 的 max 半，
  又把槽 1 缩回 s64。

这不是 DSL 转写错误，也不是上游 bug：它是两条规则叠加后的真实行为。
按“构造 + 证伪”的纪律，**不修改规则顺序、不删除规则**去消除它；记录为
上游状态。(v2s128, s64) 已在域外，因为 `same_shape` 要求形状一致。

### 4.2 非契约形状会触发 `mutationIsSane` 断言

```text
(v8s8, s32)   -> assertion failure
(v8s8, v3s16) -> assertion failure
```

断言位置：`llvm/lib/CodeGen/GlobalISel/LegalizerInfo.cpp:204`。

- `(v8s8, s32)`：规则 20 `maxScalarEltSameAsIf` 把整个向量源类型写给标量
  amount，`NarrowScalar` 结果变成“标量 -> 向量”，`mutationIsSane` 拒绝。
- `(v8s8, v3s16)`：规则 20 连 lane 数一起复制，`NarrowScalar` 改变了 lane 数，
  `mutationIsSane` 拒绝。

这两条和 4.1 一样，都违反 `G_SHL` 的 shape 契约：
**scalar 源配 scalar 量，或 vector 源配同 lane 数的 vector 量**。
元素宽度可以不同——这正是 `min/maxScalarEltSameAsIf` 要处理的情形。

结论：上游规则不为非契约 shape 做防御；**契约必须由域显式声明**。
本片用域级过滤表达，而不是改规则：

```text
filter same_shape(t0, t1);
```

`same_shape` 的语义被收紧为“同为标量，或同为向量且 lane 数相同”。
早期只判断 scalar/vector 的版本会放过 `(v8s8, v3s16)`，直接撞上断言。

### 4.3 规则的“正确性”边界

这三条发现共同说明：`LegalizerInfo` 的规则集合是**偏函数**，
只在 opcode 的 MIR 契约内定义。DSL 若把域扩张到契约外，
得到的不再是“另一个正确答案”，而是未定义行为。
因此域不是测试便利，而是证明的一部分。

## 5. 结果

```text
domain types = 24（6 scalar + 18 固定向量，两槽位同格）
opcodes      = 3（G_SHL / G_ASHR / G_LSHR）
queries      = 270（90 个 shape-consistent 类型对 × 3）
rules        = 21
matched      = 19
unreachable  = 2（规则 12 min、规则 14 min，需要 v1）
divergences  = 0（hand vs real AArch64；generated vs real AArch64）
```

代表性一步结果：

```text
G_SHL  [s32, s32]     -> Custom        (规则 0 遮蔽 legalFor)
G_SHL  [s64, s64]     -> Legal
G_SHL  [s8, s8]       -> WidenScalar   t0:s32
G_SHL  [s64, s32]     -> WidenScalar   t1:s64
G_SHL  [v2s8, v2s8]   -> NarrowScalar  t0:v2s32
G_SHL  [v6s8, v6s8]   -> MoreElements  t0:v8s8
G_SHL  [v4s64, v4s64] -> NarrowScalar  t0:v2s64
G_SHL  [v2s128,v2s128]-> FewerElements t0:s128
```

## 6. 本片新增的 DSL 能力

`G_SHL` 暴露了此前 27 个 core 构造没有覆盖的三个语义：

| 构造 | 语义 | 对应 C++ |
|---|---|---|
| `ScalarSizeVsSizeCmp` | `scalar_bits(tA) <op> bits(tB)`（标量宽度 vs 总宽度，不对称） | `minScalarSameAs` |
| `CopyType(idx, src)` | 整体复制源 LLT，可把标量 amount 变成向量 | `T = Types[SmallTypeIdx]` |
| `SameShape(i, j)` | 域级过滤：同为标量，或同为向量且 lane 数相同 | `mutationIsSane` 所隐含的契约 |

配套改动：

```text
model.py     ScalarSizeVsSizeCmp / CopyType / SameShape / Domain.filter
parser.py    min_scalar_same_as, min_scalar_elt_same_as_if,
             max_scalar_elt_same_as_if, same_shape(tN,tM), type_of(tN),
             scalar_bits(tX) <cmp> bits(tY), domain filter
codegen.py   两个 mutation 的 C++ 生成
coverage.py  ScalarSizeVsSizeCmp 读集
constructs.py  3 个构造由 none/core 升为 macro
grammar.ebnf 同步全部新产生式
```

`minScalarSameAs` 从 core 降为 macro：它不再是“一个谓词 + 一个 mutation”，
而是 `scalar_bits(t0) > bits(t1)` 这个组合。`constructs.py` 的统计随之变为：

```text
core=27 guard=5 macro=13 none=13 partial=5
```

新增 `tests/test_shift_primitives.py`（8 tests），锁定两个不对称语义和
codegen 产物；unittest 总数 147 -> 155。

## 7. 本片新增文件

```text
rules/aarch64-vector-shift.ldsl
aarch64-vector-shift-capabilities.json
witness/aarch64-vector-shift.witness
tests/test_shift_primitives.py
out/codegen-aarch64-vector-shift/（AArch64VectorShiftGen）
scripts/check-aarch64-vector-shift.ps1
```

capabilities：`custom = {G_SHL, G_ASHR, G_LSHR}`，无 libcall、无 lower。

## 8. 边界

- 只比较 `LegalizerInfo::getAction` 一步；
- 不包含 `LegalizerHelper` 的 MIR 改写与迭代 Legalizer pass；
- 不包含指令选择、寄存器分配与 codegen；
- 不覆盖 scalable vector（SVE）、FP、内存描述、原子与 custom handler body；
- 域被 `same_shape` 过滤，证明只对 shape-consistent 查询成立。

## 9. 复现

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File `
  F:\llvm-study\toycpu\scripts\check-aarch64-vector-shift.ps1
```

或先重建 oracle：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File `
  F:\llvm-study\toycpu\scripts\check-aarch64-vector-shift.ps1 -BuildOracles
```

输出应为：

```text
strict check    errors=0 rules=21
hand vs LLVM    270 queries, 0 divergences, 19/21
gen  vs LLVM    270 queries, 0 divergences, 19/21
claim ledger    claims=37 proven=37
```
