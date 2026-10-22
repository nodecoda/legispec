# Legalize 的认识模型：pass、策略、机制与外部 DSL

Date: 2026-10-21
Status: 认识整理（不是新的证明切片）

## 0. 一句话

GlobalISel 的 legalize 不是“把 IR 翻译成硬件指令”，而是
**在保持语义不变的前提下，把 generic MIR 改写成 target-legal MIR，
使下游 RegBankSelect / InstructionSelect 能接住**。
它由三层组成：driver（Legalizer pass）、policy（LegalizerInfo）、
mechanism（LegalizerHelper）。我们的外部 DSL 只外部化了 policy 层。

## 1. 位置与三层

```text
LLVM IR
  -> IRTranslator        generic MIR（G_* 指令）
  -> Legalizer           target-legal MIR        <- 本文
  -> RegBankSelect
  -> InstructionSelect
  -> RegAlloc / ...
```

| 层 | 负责 | 主要代码 | 外部 DSL |
|---|---|---|---|
| driver | 遍历、worklist、迭代、observer、artifact | `Legalizer.cpp` | 不覆盖 |
| policy | `getAction(q) -> {action, typeIdx, newType}` | `LegalizerInfo.cpp` + `lib/Target/*/GISel/*LegalizerInfo.cpp` | 覆盖（声明域 D 内） |
| mechanism | `legalizeInstrStep` 改写 MIR | `LegalizerHelper.cpp` + target hooks | 不覆盖；custom 只到接口 |

关键分界：

```text
getAction(q)            决策函数：下一步做什么
legalizeInstrStep(...)  改写函数：具体怎么改 MIR
custom                  两者的分界线
```

`getAction` 是纯决策；`legalizeInstrStep` 才动 MIR。
`custom` 表示“规则表把控制权交给 target 的 C++ 代码”，
DSL 能证明“交给谁”，不能证明“交出去之后生成了什么”。

## 2. 缺口不是“IR vs 硬件”，是“generic MIR vs 下游契约”

三个容易混淆的点：

1. **输入已经是 MIR，不是 LLVM IR。**
   IRTranslator 之后，类型已经是 LLT，指令已经是 `G_*`。
   legalize 补的是 generic MIR 与 target-legal MIR 之间的缺口。

2. **“legal”的最终判据是下游能接住，不是硬件数据手册。**
   一个类型 legal，可能只是因为它能被 selector 匹配、能被某个寄存器类承载，
   而不是因为硬件真的有一条“1 bit 寄存器”。
   例：AArch64 的 `s1` 可以 legal，靠的是 mask 语义与下游模式，不是 1-bit 物理寄存器。

3. **“硬件”是一个配置族，不是一个点。**
   同一个 target 的合法性表随 subtarget/feature 改变：
   `HasFP16`、`+cssc`、`+lse`、`+outline-atomics`、SVE。
   所以 `LegalizerInfo` 更像“这个配置点下、对下游的契约”，
   而不是“这块芯片的完整能力模型”。

## 3. 多维缺口的正确分层

| 维度 | 缺口 | 典型动作 | 例子 |
|---|---|---|---|
| 表示 | LLT 的 kind / width / addrspace / scalable | widen / narrow / bitcast / changeElementTo | `s24 -> s32`，`p0:32 -> p0:64` |
| 形状 | 向量 lane 数、元素宽度、结果-操作数类型关系 | more / fewer / clamp / bitcast | `G_CONCAT_VECTORS`，`G_FCMP` mask |
| 操作 | opcode 没有原生实现 | lower / libcall / custom / unsupported | `G_CTPOP`，atomics |
| 配置 | feature 改变同一 opcode 的表 | `legalFor(feature, ...)` | `+cssc`、`+lse`、`HasFP16` |
| ABI | 调用、返回、varargs 的约定 | CallLowering（独立组件） | 不在 legality table 内 |

**语义不是第五个“缺口”，而是所有改写的不变式。**
widen/narrow 可能依赖 no-wrap，bitcast 依赖 same size，
原子依赖 ordering。legalize 可以改表示、改形状、改实现方式，
但不能改可观察行为。把它当成一个并列维度，会看不清
“合法性”和“正确性”的区别：合法性是 downstream contract，
正确性是 rewrite 的语义不变式。

## 4. “C++ 内置 DSL”到底是什么

它是 **embedded DSL（fluent builder）**，不是 parser，也不是宏语言：

```cpp
getActionDefinitionsBuilder(G_ADD)
    .legalFor({{s32, s32}})
    .widenScalarToNextPow2(0)
    .clampScalar(0, s32, s64);
```

构建期：每个组合子向 `LegalizeRuleSet` 追加一条或多条
`LegalizeRule{Action, Predicate, Mutation, TypeIdx}`。
谓词和变异都是 `std::function`，在运行期调用。

运行期：`LegalizeRuleSet::apply` 顺序扫描，first match wins，
命中后立即求值 mutation，返回 `{action, typeIdx, newType}`。
debug 构建里有 `mutationIsSane` 之类的断言，但没有静态语义分析；
大量正确性依赖约定。

“代码散落”要分两层看：

- **policy 表面**散落在各 target 的 `*LegalizerInfo.cpp`；
- **engine**（`apply`、`LegalizePredicates`、`LegalizeMutations`）是集中的。

组合子（`clampScalar`、`widenScalarOrEltToNextPow2`、`scalarizeIf` 等）
在构建期展开成多条规则；它们的语义写在 C++ 里，不在语法里。
这就是外部 DSL 只能覆盖 policy 的根本原因：
语法之外还有 helper 的 operational semantics。

## 5. 外部 DSL 是什么

```text
rules/*.lspec + domain(D, filter, Q0) + capabilities + witness
        |
        v
parser / model  ->  checker  ->  interp (apply + fixpoint)
        |                              |
        v                              v
codegen.py -> C++ builder      conformance.py -> oracle 差分
        |                              |
        +---------- claims.py ---------+
```

- 输入：可执行的策略规范（规则表 + 声明域 + 能力清单 + 见证）。
- 语义：Python 解释器复刻 `LegalizeRuleSet::apply` 与一步/不动点行为。
- 生成：`codegen.py` 生成 C++ builder，用真实 LLVM 编译。
- 验证：`conformance.py` 在 D ∪ R 上与 oracle 逐查询对比。
- 审计：`claims.py` 钉住 rules SHA、oracle SHA、查询数、规则数。

claim 的精确形状：

```text
for every q in D union R:
    DSL.getAction(q) == LegalizerInfo::getAction(q)
```

只覆盖一步 `getAction`；不含 `LegalizerHelper` 的 MIR 改写、
迭代 Legalizer pass、指令选择、寄存器分配与 codegen。

它真正做的事：把“散落在 C++ 里的约定”变成
**可执行、可对拍、可审计、可生成**的规范。

## 6. 外部 DSL 不是什么

- 不是 legalizer 的实现，只是 policy 的规范；
- 不是完整语义规范：custom body、libcall ABI、helper 改写都不在；
- 不是全类型覆盖：D 是声明的有限域，不是所有 LLVM 类型；
- 不是“LLVM 正确性”的证明：oracle 是**实现**，不是**规范**；
- 它的边界本身也需要审计：`domain.filter`、`T017`、`OutOfDomain`
  都是元层的“逃逸设施”，和 `custom` 在对象层的作用同构。

## 7. 对照表：C++ EDSL vs 外部 DSL

| 维度 | C++ 内置 EDSL | 外部 DSL |
|---|---|---|
| 语法 | fluent API | `.lspec` |
| 语义检查 | 运行期断言 + 约定 | 静态 checker + 有限域枚举 |
| 执行 | C++ `apply` | Python `interp` |
| 生成 | 手写 | `codegen.py -> C++` |
| 验证 | 单测 / `llc` | oracle 差分 + witness + claim ledger |
| 覆盖 | 全 pipeline | policy only |
| 边界 | custom / libcall / lower | custom body / ABI / 域外后继 |

## 8. 一句话总结

Legalize 是“在保持语义不变的前提下，把 generic MIR 的表示、形状、
操作、配置缺口补到下游能接住”；C++ 用嵌入式 fluent DSL 写 policy、
用 helper 写 mechanism；我们的外部 DSL 把 policy 变成可执行规范，
并用 oracle 在声明域内证伪它。
