# AArch64 GlobalISel G_MUL 向量纵切实战

Date: 2026-10-20
Status: 已证明（hand + generated；24 查询，0 分歧）

## 1. 范围

`rules/aarch64-vector-mul.lspec` 转写
`llvm/lib/Target/AArch64/GISel/AArch64LegalizerInfo.cpp` 的 208-235 行：

```text
getActionDefinitionsBuilder(G_MUL)
    ...
    .moreElementsToNextPow2(0);
```

固定配置：

```text
triple      = aarch64-unknown-linux-gnu
cpu         = generic
HasSVE      = false
```

`G_MUL` 没有 `HasSVE` 的 `legalFor` 分支。这不是省略，而是源码事实：
C++ 里只有 `G_ADD` 家族写了第二组 `legalFor(HasSVE, ...)`。

## 2. 为什么单独成片

这是本轮唯一需要先决定的建模问题。`G_MUL` 与 `G_ADD` 家族的规则链
除 `HasSVE` 分支外完全同形，看起来可以并入
`aarch64-vector-arith.lspec` 做一次复用。最终选择独立 ruleset，理由是：

1. C++ 里是两个独立的 `getActionDefinitionsBuilder` 调用，ruleset 边界
   是源码结构的一部分；
2. 若并入 arith 家族，`ruleset G_ADD, G_SUB, G_AND, G_OR, G_XOR` 会变成
   六个 opcode 的并集，witness 与 claim 的“这一片证明了什么”会变模糊；
3. 独立文件不增加成本：15 条规则原样复用既有 DSL 原语，不需要新增
   `model.py` / `parser.py` / `codegen.py` 能力。

结论：**同形不等于同一 ruleset**。复用规则文本，不复用证明边界。

## 3. 规则展开

`G_MUL` 的 builder 展开为 15 条规则，顺序与 C++ 源码一致：

```text
 1  legalFor({s32,s64,v8s8,v16s8,v4s16,v8s16,v2s32,v4s32,v2s64})
 2  widenScalarToNextPow2(0)
 3  clampScalar(0,s32,s64) min
 4  clampScalar(0,s32,s64) max
 5  clampMaxNumElements(0,s8,16)
 6  clampMaxNumElements(0,s16,8)
 7  clampNumElements(0,v2s32,v4s32) min
 8  clampNumElements(0,v2s32,v4s32) max
 9  clampNumElements(0,v2s64,v2s64) min
10  clampNumElements(0,v2s64,v2s64) max
11  minScalarOrEltIf(lanes<=2,  0, s32)
12  minScalarOrEltIf(lanes<=4,  0, s16)
13  minScalarOrEltIf(lanes<=16, 0, s8)
14  scalarizeIf(scalarOrEltWiderThan(0,64), 0)
15  moreElementsToNextPow2(0)
```

其中规则 2 对向量结构性不可达（`sizeNotPow2` 只对 scalar 成立）；
规则 7-min 与 9-min 需要 `v1`，而 LLVM 的 `ElementCount::getFixed(1)`
是 scalar，因此不可达。这三条都保留并与 C++ 序号对齐，其中后两条在
witness 里显式声明为 `unreachable`。

## 4. 结果

```text
domain types = 24（6 个 scalar + 18 个固定向量）
opcodes      = 1（G_MUL）
queries      = 24（1 × 24）
rules        = 15
matched      = 13
unreachable  = 2
divergences  = 0（hand vs real AArch64；generated vs real AArch64）
```

代表性一步结果：

```text
G_MUL [s24]    -> WidenScalar     s32      (next-pow2 scalar)
G_MUL [s8]     -> WidenScalar     s32      (clampScalar min)
G_MUL [s128]   -> NarrowScalar    s64      (clampScalar max)
G_MUL [v2s8]   -> WidenScalar     v2s32    (lanes <= 2)
G_MUL [v4s8]   -> WidenScalar     v4s16    (lanes <= 4)
G_MUL [v8s4]   -> WidenScalar     v8s8     (lanes <= 16)
G_MUL [v2s128] -> FewerElements   s128     (scalarizeIf)
G_MUL [v6s8]   -> MoreElements    v8s8     (lane round-up)
G_MUL [v32s8]  -> FewerElements   v16s8    (clampMaxNumElements s8)
G_MUL [v16s16] -> FewerElements   v8s16    (clampMaxNumElements s16)
G_MUL [v8s32]  -> FewerElements   v4s32    (clampNumElements s32)
G_MUL [v4s64]  -> FewerElements   v2s64    (clampNumElements s64)
```

## 5. 本片新增

```text
rules/aarch64-vector-mul.lspec
aarch64-vector-mul-capabilities.json
witness/aarch64-vector-mul.witness
out/codegen-aarch64-vector-mul/（AArch64VectorMulGen）
scripts/check-aarch64-vector-mul.ps1
```

DSL 实现零改动。这本身是一个结果：`G_MUL` 没有暴露新的构造，
说明上一片补齐的 `min_scalar_or_elt_if` / `more_elements_to_next_pow2`
与 `scalarizeIf` 的 core 表达已经覆盖了这条规则形状。

## 6. 边界

- 只比较 `LegalizerInfo::getAction` 一步；
- 不包含 `LegalizerHelper` 的 MIR 改写与迭代 Legalizer pass；
- 不包含指令选择、寄存器分配与 codegen；
- 不覆盖 scalable vector（SVE）、FP、内存描述、原子与 custom handler。

## 7. 复现

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File `
  F:\llvm-study\toycpu\scripts\check-aarch64-vector-mul.ps1
```

或先重建 oracle：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File `
  F:\llvm-study\toycpu\scripts\check-aarch64-vector-mul.ps1 -BuildOracles
```
