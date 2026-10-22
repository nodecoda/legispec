# AArch64 GlobalISel 原子合法性实验

Date: 2026-10-20
Status: 已证明（generic / +outline-atomics / +lse 三个配置点）

## 1. 范围

`rules/aarch64-atomic.lspec` 和 `rules/aarch64-atomic-outline.lspec` 转写
`llvm/lib/Target/AArch64/GISel/AArch64LegalizerInfo.cpp:1148-1174`
中的原子规则：

```text
G_ATOMIC_CMPXCHG_WITH_SUCCESS
G_ATOMIC_CMPXCHG
G_ATOMICRMW_XCHG / ADD / SUB / AND / OR / XOR
G_ATOMICRMW_MIN / MAX / UMIN / UMAX
```

固定配置：

```text
triple     = aarch64-unknown-linux-gnu
cpu        = generic
code model = small
pointer    = LLT::pointer(0, 64)  -> p0:64
HasCSSC    = false
HasSVE     = false
```

原子规则不依赖内存描述、原子序或对齐谓词；这些属于原子子 pass，不在这张
`LegalizeRuleSet` 表里。

## 2. 配置轴

```cpp
bool UseOutlineAtomics = ST.outlineAtomics() && !ST.hasLSE();
```

| 配置 | `outlineAtomics()` | `hasLSE()` | `UseOutlineAtomics` | 原子动作 |
|---|---:|---:|---:|---|
| generic | false | false | false | legal/custom + clamp |
| `+outline-atomics` | true | false | true | libcall + clamp |
| `+lse` | false | true | false | 与 generic 相同 |

oracle 实测：

```text
generic:
  G_ATOMICRMW_ADD [s8,p0:64]    -> WidenScalar s32
  G_ATOMICRMW_ADD [s32,p0:64]   -> Legal
  G_ATOMIC_CMPXCHG [s128,p0:64] -> Custom

+outline-atomics:
  G_ATOMICRMW_ADD [s8,p0:64]    -> Libcall
  G_ATOMICRMW_ADD [s32,p0:64]   -> Libcall
  G_ATOMICRMW_ADD [s128,p0:64]  -> NarrowScalar s64
  G_ATOMIC_CMPXCHG [s128,p0:64] -> Libcall

+outline-atomics:
  G_ATOMICRMW_MIN [s32,p0:64]   -> Legal
  G_ATOMIC_CMPXCHG_WITH_SUCCESS [s32,s32,p0:64] -> Lower
```

注意：`+outline-atomics` 下 `G_ATOMICRMW_*` 的 libcall 集合不含 `s128`，
所以 `s128` 在 libcall 规则不匹配后，由 `clampScalar` 收窄到 `s64`。
`G_ATOMIC_CMPXCHG` 的 libcall 集合包含 `s128`，所以它直接走 `Libcall`。

## 3. generic 规则表

```text
G_ATOMIC_CMPXCHG_WITH_SUCCESS:
  lower  when typeInSet(0,{s8,s16,s32,s64,s128}) && typeIs(2,p0:64)

G_ATOMIC_CMPXCHG:
  legalFor {s32,p0:64}, {s64,p0:64}
  customFor {s128,p0:64}
  clampScalar(0, s32, s64)

G_ATOMICRMW_{XCHG,ADD,SUB,AND,OR,XOR}:
  legalFor {s32,p0:64}, {s64,p0:64}
  clampScalar(0, s32, s64)

G_ATOMICRMW_{MIN,MAX,UMIN,UMAX}:
  legalIf typeInSet(0,{s32,s64}) && typeIs(1,p0:64)
  clampScalar(0, s32, s64)
```

## 4. +outline-atomics 规则表

```text
G_ATOMIC_CMPXCHG_WITH_SUCCESS:
  lower  when typeInSet(0,{s8,s16,s32,s64,s128}) && typeIs(2,p0:64)

G_ATOMIC_CMPXCHG:
  libcall when typeInSet(0,{s8,s16,s32,s64,s128}) && typeIs(1,p0:64)
  clampScalar(0, s32, s64)

G_ATOMICRMW_{XCHG,ADD,SUB,AND,OR,XOR}:
  libcall when typeInSet(0,{s8,s16,s32,s64}) && typeIs(1,p0:64)
  clampScalar(0, s32, s64)

G_ATOMICRMW_{MIN,MAX,UMIN,UMAX}:
  legalIf typeInSet(0,{s32,s64}) && typeIs(1,p0:64)
  clampScalar(0, s32, s64)
```

`G_ATOMIC_CMPXCHG_WITH_SUCCESS` 和 min/max 家族不随
`UseOutlineAtomics` 改变。

## 5. +lse：策略不变性

`+lse` 使 `UseOutlineAtomics` 仍为 `false`，所以可以直接复用
`aarch64-atomic.lspec`。这不是“省文件”，而是一个更强的断言：

```text
同一份 DSL 规则表
  vs generic oracle -> 0 divergence
  vs +lse oracle    -> 0 divergence
```

generic 与 `+lse` 的 oracle 差分：

```text
queries=612 differences=0 (0.00%)
```

它说明 LSE 改变的是 `legalizeAtomicCmpxchg128` 等 rewrite 路径，而不是
`getAction` 的策略表。这个边界很重要：当前 claim 不覆盖 rewrite。

作为对照，generic 与 `+outline-atomics` 的差分是：

```text
queries=612 differences=29 (4.74%)
by_opcode:
  G_ATOMIC_CMPXCHG   5
  G_ATOMICRMW_ADD    4
  G_ATOMICRMW_AND    4
  G_ATOMICRMW_OR     4
  G_ATOMICRMW_SUB    4
  G_ATOMICRMW_XCHG   4
  G_ATOMICRMW_XOR    4
```

## 6. 顺序敏感的重叠

generic 的 `G_ATOMIC_CMPXCHG`：

```text
customFor {s128,p0:64}
clampScalar(0,s32,s64) -> narrow(t0,s64)
```

两者都匹配 `[s128,p0:64]`，first-match-wins 让 `Custom` 获胜。

`+outline-atomics` 下 `libcall` 与 `widen/narrow` 也有重叠：

```text
libcall(s8/s16) 先于 widen(s32)
libcall(s8..s128) 先于 CMPXCHG 的 narrow
```

strict check 会报告 K001/K002 overlap warning；oracle 对拍仍为 0 divergence。
这不是 DSL 的错误，而是 C++ 表本身具有的有意顺序依赖。

另外 `G_ATOMIC_CMPXCHG_WITH_SUCCESS` 的 `t1` 在 C++ 谓词里也没有约束，
所以 strict check 会报告 C001；DSL 忠实保留了这一点，没有擅自收紧。

## 7. 证据

```text
generic hand:        queries=612 divergences=0 rules=11/11
generic generated:   queries=612 divergences=0 rules=11/11
outline hand:        queries=612 divergences=0 rules=10/10
outline generated:   queries=612 divergences=0 rules=10/10
lse hand:            queries=612 divergences=0 rules=11/11
generic vs lse:      queries=612 differences=0
closure:              generic 22, outline 19, outside D = 0
```

报告：

```text
out/aarch64-atomic-conformance-report.json
out/aarch64-atomic-gen-conformance-report.json
out/aarch64-atomic-outline-conformance-report.json
out/aarch64-atomic-outline-gen-conformance-report.json
out/aarch64-atomic-lse-conformance-report.json
out/aarch64-atomic-generic-vs-lse.json
out/aarch64-atomic-generic-vs-outline.json
```

claim：

```text
aarch64-atomic
aarch64-atomic-generated
aarch64-atomic-outline
aarch64-atomic-outline-generated
aarch64-atomic-lse
```

生成 oracle：

```text
AArch64AtomicGen         -> generic 规则
AArch64AtomicOutlineGen  -> +outline-atomics 规则
```

`+lse` 没有单独的生成 oracle：它复用 generic 的同一份规则文件，生成路径已经由
`aarch64-atomic-generated` 证明。LSE 的差异在 rewrite，不在 policy 表。

## 8. 边界

已证明：

- generic / `+outline-atomics` / `+lse` 三个配置点在声明的 `D union R` 上
  逐点等于 `AArch64LegalizerInfo::getAction`；
- generic 与 `+outline-atomics` 的 DSL 生成 C++ 与解释器逐点相等；
- `+lse` 与 generic 的 policy 表逐点相等。

未证明：

- 原子序、volatile、对齐和内存描述；
- `LegalizerHelper` 的实际 MIR 改写；
- libcall 的实际调用约定和符号选择；
- 原子指令选择与代码生成；
- `+outline-atomics` 与 `+lse` 同时打开的组合点。
