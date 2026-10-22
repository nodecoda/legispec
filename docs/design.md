# Legalizer 规则 DSL：语法设计与 Python 实现

> 目标：把 LLVM GlobalISel 的 legality 规则从 C++ builder 调用里抽出来，
> 用一门自由格式的小语言描述，并用 Python 解释执行。
> 范围：只建模 `LegalizerInfo::getAction` + `LegalizeRuleSet::apply`，
> 不建模 `LegalizerHelper` 的 MIR 改写。
> 代码：`src/legispec/`
> 规则：`rules/toycpu.lspec`

相关文档：

- [LegiSpec 与复杂规则走读](walkthrough.md) —— 五条真实规则的调用链
- [LegalizerInfo 规则表与算法](legalizerinfo-legalizeinstrstep.md) —— 完整数据模型

---

## 0. 一句话

> **这门 DSL 不是“另一个编译器”，而是把 `LegalizeRule` 的三个字段
> （predicate / action / mutation）变成可读、可执行、可追踪的文本；
> `apply` 仍然是 first-match，宏只是 C++ builder 的展开规则。**

换句话说：

~~~text
C++:  getActionDefinitionsBuilder(G_ADD).legalFor({s32}).clampScalar(0, s32, s32)
DSL:  ruleset G_ADD { legal_for {s32}; clamp_scalar 0, s32, s32; }
语义: 有序 Rule 列表 + first-match + mutation
~~~

---

## 1. 为什么要重新设计

LLVM 的 C++ builder 本身已经是一门“内嵌 DSL”，但它把四件关键事实藏起来了：

| 被隐藏的事实 | C++ 里的表现 | 学习时的后果 |
|---|---|---|
| **first-match** | 链式调用顺序就是规则顺序 | 以为规则是集合，实际是列表 |
| **mutation 的语义** | `clampScalar` 内部展开成 min + max 两条 | 不知道哪条先匹配 |
| **alias** | 多 opcode builder 共享 RuleSet | 给其中一个再加规则会断言 |
| **action 之后还有 helper** | 规则只说 `NarrowScalar` | 以为规则自己会拆指令 |

这门 DSL 的设计原则就是：**把这四件事全部显式化**。

---

## 2. 等价边界

### 2.1 等价的部分

| LLVM | DSL |
|---|---|
| `LegalityQuery` | `Query(opcode, types, mems, imms)` |
| `LegalizeRule` | `Rule(pred, action, type_idx, new_type)` |
| `LegalizeRuleSet::apply` | `RuleSet.apply` |
| `LegalizeActionStep` | `Step(action, type_idx, new_type)` |
| `typeInSet` / `typePairInSet` | `t0 in {...}` / `pair(t0,t1) in {...}` |
| `clampScalar` | `clamp_scalar`（展开成两条 Rule） |
| `widenScalarToNextPow2` | `widen_to_next_pow2` |
| `widenScalarOrEltToNextPow2OrMinSize` | `widen_to_next_pow2_or_min` |
| `scalarSameSizeAs` / `changeElementSizeTo(i,j)` | `scalar_bits(tA) cmp scalar_bits(tB)` + `same_size_as(tB)` |
| `legalForTypesWithMemDesc` | `legal_for_mem` |

### 2.2 不等价的部分

以下内容**故意不在 DSL 里**：

- `LegalizerHelper::narrowScalarAddSub` 等 MIR 改写；
- `LegalizerPass` 的工作队列与不动点迭代；
- `ArtifactCombiner`；
- `CallLowering` / ABI；
- 指令选择器（InstructionSelect）；
- 寄存器银行选择（RegBankSelect）。

因此，DSL 的输出是：

~~~text
Step(Action, TypeIdx, NewType)
~~~

而不是一段新的 MIR。它回答的是：

> “这条指令下一步应该做什么？”

而不是：

> “具体怎么改写这条指令？”

这正是学习规则表时最需要先分离的两层。

---

## 3. 完整语法（EBNF）

### 3.1 词法

- 空白和换行都是分隔符（自由格式）；
- 注释：`# ...` 或 `// ...`；
- 标识符：`[A-Za-z_][A-Za-z0-9_]*`；
- 整数：十进制；
- 标点：`{ } ( ) , ; = . : == != <= >= < >`。

### 3.2 语法

机器可读副本：`docs/grammar.ebnf`。

~~~ebnf
program      = { top_level } EOF ;
top_level    = type_alias | ruleset ;

type_alias   = "type" IDENT "=" type ";" ;

ruleset      = "ruleset" opcode { "," opcode } "{" { statement } "}" ;
opcode       = IDENT ;

statement    = core_rule | macro ";" ;

core_rule    = action [ type_idx "to" mutation ] [ "when" predicate ] ";" ;

action       = "legal" | "custom" | "unsupported" | "libcall" | "lower"
             | "narrow" | "widen" | "bitcast" | "more" | "fewer" ;

macro        = "legal_for" type_set
             | "legal_for_pairs" pair_set
             | "custom_for_pairs" pair_set
             | "libcall_for" type_set
             | "clamp_scalar" type_idx "," type "," type
             | "widen_to_next_pow2" type_idx "," INT
             | "widen_to_next_pow2_or_min" type_idx "," INT
             | "widen_to_next_multiple" type_idx "," INT
             | "legal_for_mem" mem_set
             | "always_legal" ;

type_idx     = "t" INT | INT ;

type         = LLT | IDENT ;          (* IDENT 是 type alias *)
LLT          = "s" INT                (* scalar: s1, s32, s64 *)
             | "p" INT [ ":" INT ]    (* pointer: p0, p0:64 *)
             | "v" INT type ;         (* fixed vector: v4s32, v2p0 *)

mutation     = type
             | "same_size_as" "(" type_idx ")"
             | "next_pow2" "(" "bits" "(" type_idx ")" "," INT ")"
             | "next_multiple" "(" "bits" "(" type_idx ")" "," INT ")"
             | "element_of" "(" type_idx ")" ;

type_set     = "{" [ type { "," type } ] "}" ;
pair_set     = "{" [ pair { "," pair } ] "}" ;
pair         = "(" type "," type ")" ;
mem_set      = "{" [ mem_entry { "," mem_entry } ] "}" ;
mem_entry    = "(" type "," type "," type "," INT ")" ;

predicate    = or_expr ;
or_expr      = and_expr { "or" and_expr } ;
and_expr     = not_expr { "and" not_expr } ;
not_expr     = "not" not_expr
             | "(" predicate ")"
             | atom ;

atom         = "always" | "true"
             | "scalar" "(" type_idx ")"
             | "vector" "(" type_idx ")"
             | "pointer" "(" type_idx ")"
             | "same_size" "(" type_idx "," type_idx ")"
             | "scalar_bits" "(" type_idx ")" cmp "scalar_bits" "(" type_idx ")"
             | "is_pow2" "(" "bits" "(" type_idx ")" ")"
             | "is_multiple" "(" "bits" "(" type_idx ")" "," INT ")"
             | ( "bits" | "elem_bits" | "size" ) "(" type_idx ")" cmp INT
             | "lanes" "(" type_idx ")" cmp INT
             | "mem" INT "." "type" ( "in" type_set | cmp type )
             | "mem" INT "." "align" cmp INT
             | "imm" INT cmp INT
             | "pair" "(" type_idx "," type_idx ")" "in" pair_set
             | type_idx ( "in" type_set | ( "==" | "!=" ) type ) ;

cmp          = "==" | "!=" | "<" | "<=" | ">" | ">=" ;
~~~

实现中有一个词法约定：LLT 是**一个 token**。也就是说 `v4s32` 不能写成
`v4 s32`，`p0:64` 不能写成 `p0 : 64`。这样规则文件更紧凑，也避免和
type index 的冒号语法混淆。


### 3.3 设计说明

**为什么核心规则不需要 `rule` 关键字？**

语句的第一个 token 就是 action，解析器可以无歧义地区分：

~~~text
legal when ...;       # 核心规则
legal_for {...};      # 宏
~~~

**为什么宏语句也必须以 `;` 结尾？**

统一语句边界，允许自由换行；不依赖缩进，也不依赖行尾。

**为什么 `more` / `fewer` 的 mutation 既可以是向量类型，也可以是 `element_of`？**

- `more t0 to v4s32`：把 lane 数改成 4；
- `fewer t0 to element_of(t0)`：scalarize，把向量降成元素类型。

两者都对应 LLVM 的 `changeElementCountTo` / `scalarize`。

---

## 4. 语义

### 4.1 Query

~~~text
Query = (opcode, types[], mems[], imms[])
~~~

其中：

| 字段 | 含义 | 例子 |
|---|---|---|
| `types` | 按 type index 排列的 LLT | `[v4s1, v4s32]` |
| `mems` | 每个 MMO 的 memory type + 对齐（bit） | `s8/8b` |
| `imms` | generic immediate | shuffle mask 等 |

### 4.2 Rule

一条规则是三元组：

~~~text
Rule = (Predicate, Action, Mutation)
~~~

- `Predicate`：是否匹配；
- `Action`：下一步做什么；
- `Mutation`：改哪个 type index、改成什么类型。

### 4.3 first-match

`RuleSet.apply` 的伪代码：

~~~text
for i, rule in enumerate(rules):
    if rule.pred.eval(query):
        return Step(rule.action, rule.type_idx, rule.mutation.eval(query)), i
return Step(Unsupported, 0, None), None
~~~

**第一条匹配的规则赢，不回溯。**

这条语义直接对应 `LegalizeRuleSet::apply`（`LegalizerInfo.cpp:194-212`）。

### 4.4 alias

~~~text
ruleset G_ADD, G_SUB, G_AND { ... }
~~~

三个 opcode 共享**同一个 RuleSet 对象**。因此：

- 规则只写一份；
- 后续不能再单独给 `G_SUB` 定义规则；
- 解析器会报错：

~~~text
opcode G_SUB already has a rule set; opcodes aliased in one ruleset cannot be redefined
~~~

这对应 LLVM 的断言：多 opcode builder 是别名共享。

### 4.5 action / mutation

| Action | 需要 mutation | 语义 |
|---|---|---|
| `Legal` | 否 | 已合法 |
| `Custom` | 否 | 交给 target 的 `legalizeCustom` |
| `Unsupported` | 否 | 无法合法化 |
| `Libcall` | 否 | 交给 `CallLowering` 发运行时调用 |
| `Lower` | 否 | 降成更基础的 generic 指令 |
| `NarrowScalar` | 是 | 缩窄元素位宽 |
| `WidenScalar` | 是 | 扩宽元素位宽 |
| `Bitcast` | 是 | 位模式不变，改类型 |
| `MoreElements` | 是 | 增加 lane 数 |
| `FewerElements` | 是 | 减少 lane 数 / scalarize |

---

## 5. 宏展开规则

宏不是运行时函数，而是**解析期展开**成核心规则。展开后的规则可以用
`explain` 命令查看。

### 5.1 `legal_for`

~~~text
legal_for {T}
=>
legal when t0 in {T}
~~~

对应 `LegalizeRuleSet::legalFor({T})`，只看 type index 0。

### 5.2 `legal_for_pairs`

~~~text
legal_for_pairs {(A, B), ...}
=>
legal when pair(t0, t1) in {(A, B), ...}
~~~

对应 `legalFor({{A,B}, ...})`，同时看 type0 / type1。

### 5.3 `custom_for_pairs`

~~~text
custom_for_pairs {(A, B), ...}
=>
custom when pair(t0, t1) in {(A, B), ...}
~~~

### 5.4 `libcall_for`

~~~text
libcall_for {T}
=>
libcall when t0 in {T}
~~~

### 5.5 `clamp_scalar`：顺序非常重要

~~~text
clamp_scalar i, MIN, MAX
=>
widen  ti to MIN when scalar(ti) and bits(ti) < bits(MIN)
narrow ti to MAX when scalar(ti) and bits(ti) > bits(MAX)
~~~

注意顺序：**先 min，后 max**。这直接对应：

~~~cpp
minScalar(TypeIdx, MinTy).maxScalar(TypeIdx, MaxTy)
~~~

例子：`G_ADD s64`

~~~text
[0] legal  when t0 in {s32, v4s32}          -> no match
[1] widen  when scalar(t0) and bits(t0) < 32 -> no match
[2] narrow when scalar(t0) and bits(t0) > 32 -> MATCH
Result: NarrowScalar t0 -> s32
~~~

例子：`G_ADD s16`

~~~text
[1] widen ... bits(t0) < 32 -> MATCH
Result: WidenScalar t0 -> s32
~~~

### 5.6 `widen_to_next_pow2`：注意 predicate 不检查 MinSize

~~~text
widen_to_next_pow2 i, MIN
=>
widen ti to next_pow2(bits(ti), MIN)
  when scalar(ti) and not is_pow2(bits(ti))
~~~

这对应 LLVM 的：

~~~cpp
actionIf(WidenScalar, sizeNotPow2(typeIdx(TypeIdx)),
         widenScalarOrEltToNextPow2(TypeIdx, MinSize))
~~~

关键点：

- predicate 只要求 **不是 2 的幂**；
- `MIN` 只影响 mutation 的结果，不参与 predicate。

所以 `s1` **不会**匹配 `widen_to_next_pow2 0, 32`，因为 `s1` 已经是 2 的幂。
`G_ICMP s1` 是被后面的 `clamp_scalar` 的 min 规则扩到 `s32` 的。

这是一个非常容易误解的点，也是 DSL 显式展开的价值。

### 5.6.1 `widen_to_next_pow2_or_min`

~~~text
widen_to_next_pow2_or_min i, MIN
=>
widen ti to next_pow2(bits(ti), MIN)
  when scalar_bits(ti) < MIN or not is_pow2(scalar_bits(ti))
~~~

对应 LLVM 的：

~~~cpp
actionIf(WidenScalar, sizeNotPow2OrSmallerThan(TypeIdx, MinSize),
         widenScalarOrEltToNextPow2(TypeIdx, MinSize))
~~~

和 5.6 的区别只在 predicate：`s1/s2/s4` 虽然已经是 2 的幂，但只要小于
`MIN` 仍然会匹配。RISC-V 的 `G_ICMP` 正是用它把 `t1` 至少抬到 `s8`，
再 clamp 到 `s64`；普通的 `widen_to_next_pow2` 会跳过 `s1`，直接由
`clamp_scalar` 抬到 `s64`。

### 5.7 `widen_to_next_multiple`

~~~text
widen_to_next_multiple i, SIZE
=>
widen ti to next_multiple(bits(ti), SIZE)
  when scalar(ti) and not is_multiple(bits(ti), SIZE)
~~~

`next_multiple` 的计算是 `alignTo(bits, SIZE)`：

~~~text
s1  -> s32
s8  -> s32
s16 -> s32
s48 -> s64
~~~

ToyCPU 的 `G_PHI s1` 就是这条规则。

### 5.7.1 `same_size_as` 与 `scalar_bits`

LLVM 的 `scalarSameSizeAs(i, j)` 不是常量 mutation：它把 `ti` 改成 `tj`
的标量宽度。它由两条规则组成：

~~~cpp
minScalarSameAs(i, j)  // ti 比 tj 窄 -> WidenScalar 到 tj 的宽度
maxScalarSameAs(i, j)  // ti 比 tj 宽 -> NarrowScalar 到 tj 的宽度
~~~

DSL 用显式的跨槽位谓词和 mutation 表达：

~~~text
widen  ti to same_size_as(tj) when scalar_bits(ti) < scalar_bits(tj);
narrow ti to same_size_as(tj) when scalar_bits(ti) > scalar_bits(tj);
~~~

- `scalar_bits(t)` 对应 `getScalarSizeInBits()`；
- `same_size_as(tj)` 对应 `changeElementSizeTo(i, j)`，保留 `ti` 的形状
  （scalar 或 vector 的 lane 数），只替换 element/scalar 宽度。

这是一个跨槽位规则，因此终止性检查必须按整条 `Query` 建状态图；只按
`(槽位, 类型)` 投影会把不同 `tj` 下的边混在一起，产生假循环。RISC-V 的
`G_CTLZ`/`G_CTTZ`/`G_CTLS`/`G_CTPOP` 都依赖这条规则。

### 5.8 `legal_for_mem`

~~~text
legal_for_mem {(T0, T1, MEMTY, ALIGN_BITS), ...}
=>
legal when mem_compat(t0, t1, mem0) in {...}
~~~

匹配条件对应 `TypePairAndMemDesc::isCompatible`：

- `t0 == T0`；
- `t1 == T1`；
- `mem0.type` 与 `MEMTY` 的**总位数相同**；
- `mem0.align >= ALIGN_BITS`。

注意：`ALIGN_BITS` 是**最小对齐**，单位是 bit。ToyCPU 的标量访问写 `1`，
意思是“字节对齐即可”；`v4s32` 写 `128`，意思是必须 16 字节对齐。

### 5.9 `always_legal`

~~~text
always_legal
=>
legal when always
~~~

---

## 6. ToyCPU 规则转写

完整文件：`rules/toycpu.lspec`。

### 6.1 `G_ADD`：clamp 展开

C++（`ToyCPULegalizerInfo.cpp:61-63`）：

~~~cpp
getActionDefinitionsBuilder({G_ADD, G_SUB, G_AND, G_OR, G_XOR})
    .legalFor({s32, v4s32})
    .clampScalar(0, s32, s32);
~~~

DSL：

~~~text
ruleset G_ADD, G_SUB, G_AND, G_OR, G_XOR {
    legal_for {s32, v4s32};
    clamp_scalar 0, s32, s32;
}
~~~

展开后 3 条规则：

~~~text
[0] legal  when t0 in {s32, v4s32}
[1] widen  when scalar(t0) and bits(t0) < 32
[2] narrow when scalar(t0) and bits(t0) > 32
~~~

### 6.2 `G_ICMP`：Custom 与顺序

C++（`:154-159`）：

~~~cpp
getActionDefinitionsBuilder(G_ICMP)
    .legalFor({{s32, s32}, {p0, p0}, {v4s32, v4s32}})
    .customFor({{v4s1, v4s32}})
    .widenScalarToNextPow2(0, 32)
    .clampScalar(0, s32, s32)
    .clampScalar(1, s32, s32);
~~~

DSL：

~~~text
ruleset G_ICMP {
    legal_for_pairs {(s32, s32), (p0, p0), (v4s32, v4s32)};
    custom_for_pairs {(v4s1, v4s32)};
    widen_to_next_pow2 0, 32;
    clamp_scalar 0, s32, s32;
    clamp_scalar 1, s32, s32;
}
~~~

`explain G_ICMP` 的真实输出：

~~~text
RuleSet: G_ICMP (7 rules)
  [0] legal when pair(t0, t1) in {(s32, s32), (p0, p0), (v4s32, v4s32)}  # legal_for_pairs ...
  [1] custom when pair(t0, t1) in {(v4s1, v4s32)}  # custom_for_pairs ...
  [2] widen t0 to next_pow2(bits(t0), 32) when (scalar(t0)) and (not is_pow2(bits(t0)))  # widen_to_next_pow2(0, 32)
  [3] widen t0 to s32 when (scalar(t0)) and (bits(t0) < 32)  # clamp_scalar(0, s32, s32) step 1/2 (min)
  [4] narrow t0 to s32 when (scalar(t0)) and (bits(t0) > 32)  # clamp_scalar(0, s32, s32) step 2/2 (max)
  [5] widen t1 to s32 when (scalar(t1)) and (bits(t1) < 32)  # clamp_scalar(1, s32, s32) step 1/2 (min)
  [6] narrow t1 to s32 when (scalar(t1)) and (bits(t1) > 32)  # clamp_scalar(1, s32, s32) step 2/2 (max)
~~~

### 6.3 `G_PHI`：next multiple

C++（`:164-166`）：

~~~cpp
getActionDefinitionsBuilder(G_PHI)
    .legalFor({s32, p0, v4s32, v2s32})
    .widenScalarToNextMultipleOf(0, 32);
~~~

DSL：

~~~text
ruleset G_PHI {
    legal_for {s32, p0, v4s32, v2s32};
    widen_to_next_multiple 0, 32;
}
~~~

`G_PHI s1` 的 trace：

~~~text
[0] legal when t0 in {s32, p0, v4s32, v2s32} -> no match
[1] widen t0 to next_multiple(bits(t0), 32)
      when scalar(t0) and not is_multiple(bits(t0), 32) -> MATCH
Result: WidenScalar t0 -> s32
~~~

### 6.4 `G_SDIV`：Libcall

C++（`:87-89`）：

~~~cpp
getActionDefinitionsBuilder({G_SDIV, G_UDIV, G_SREM, G_UREM})
    .libcallFor({s32, s64})
    .clampScalar(0, s32, s32);
~~~

DSL：

~~~text
ruleset G_SDIV, G_UDIV, G_SREM, G_UREM {
    libcall_for {s32, s64};
    clamp_scalar 0, s32, s32;
}
~~~

`s64` 命中 `libcall`；`s16` 不命中 libcall，落入 clamp 的 min 规则，
得到 `WidenScalar s32`，下一轮再变成 libcall。这体现了“单步”语义。

### 6.5 `G_SHUFFLE_VECTOR`：Custom + Unsupported

C++（`:324-327`）：

~~~cpp
getActionDefinitionsBuilder(G_SHUFFLE_VECTOR)
    .customIf(all(typeInSet(0, {v4s32, v2s32}),
                  typeInSet(1, {v4s32, v2s32})))
    .unsupported();
~~~

DSL：

~~~text
ruleset G_SHUFFLE_VECTOR {
    custom when t0 in {v4s32, v2s32} and t1 in {v4s32, v2s32};
    unsupported;
}
~~~

`v4s32, v4s32` → `Custom`；`s32, s32` → `Unsupported`。
注意 `.unsupported()` 只在 `custom` predicate 不匹配时才会被看到；
如果 `custom` 匹配但 `legalizeCustom` 返回 false，LLVM 不会回退到它。
这一层属于 helper，DSL 只负责给出 `Custom`。

---

## 7. Python 实现

### 7.1 目录

~~~text
src/legispec/
  rules/               # 规则文件在仓库根 rules/
  __init__.py          # 公共 API
  __main__.py          # python -m legispec
  model.py             # LLT / Query / Pred / TypeExpr / Rule / RuleSet
  parser.py            # lexer + recursive-descent parser + macro expansion
  cli.py               # explain / trace / check / opcodes
  rules/toycpu.lspec    # ToyCPU 规则转写
  tests/test_legispec.py
~~~

### 7.2 调用链

~~~text
cli.main
  -> parser.parse_file(path)
      -> lex(text)
      -> Parser.parse_program()
          -> LegalizerInfo.add(RuleSet)
              -> 多 opcode 共享同一个 RuleSet
          -> 宏在解析期展开成 Rule
  -> info.action(Query)
      -> RuleSet.apply(query)
          -> Pred.eval(query)
          -> TypeExpr.eval(query)
      -> Step(action, type_idx, new_type)
~~~

### 7.3 关键类

| 类 | 职责 | 对应 LLVM |
|---|---|---|
| `LLT` | 标量/指针/向量类型 | `llvm::LLT` |
| `Query` | 一次 legality 查询 | `LegalityQuery` |
| `Pred` 及其子类 | 规则守卫 | `LegalityPredicate` |
| `TypeExpr` 及其子类 | mutation | `LegalizeMutation` |
| `Rule` | 一条规则 | `LegalizeRule` |
| `RuleSet` | 有序规则 + first-match | `LegalizeRuleSet` |
| `LegalizerInfo` | opcode → RuleSet | `LegalizerInfo` |
| `Step` | 查询结果 | `LegalizeActionStep` |

### 7.4 为什么用递归下降，而不是 parser generator

- 语法小，手写解析器约 500 行，零依赖；
- 可以在解析期直接展开宏，保留 `origin` 便于 `explain`；
- 错误信息能带行列号；
- 后续加 predicate/mutation 只需加一个分支。

---

## 8. 用法

### 8.1 检查规则文件

~~~powershell
python -m legispec `
  --rules <legacy-checkout>\tools\legispec\rules\toycpu.lspec `
  check --verbose
~~~

输出：

~~~text
rulesets=47 opcodes=80 rules=89
  G_ADD, G_SUB, G_AND, G_OR, G_XOR: 3 rule(s)
  ...
~~~

### 8.2 查看展开后的规则

~~~powershell
python -m legispec --rules ...\toycpu.lspec explain G_ICMP
~~~

### 8.3 追踪一次查询

~~~powershell
python -m legispec --rules ...\toycpu.lspec trace G_ADD --type s64
python -m legispec --rules ...\toycpu.lspec trace G_ICMP --type v4s1 --type v4s32
python -m legispec --rules ...\toycpu.lspec trace G_PHI --type s1
python -m legispec --rules ...\toycpu.lspec trace G_SDIV --type s64
python -m legispec --rules ...\toycpu.lspec trace G_SHUFFLE_VECTOR --type v4s32 --type v4s32
python -m legispec --rules ...\toycpu.lspec trace G_LOAD --type s32 --type p0 --mem s8:8
~~~

`G_ADD s64` 的实际输出：

~~~text
Query: G_ADD types=[s64]
RuleSet: G_ADD, G_SUB, G_AND, G_OR, G_XOR (3 rules)
  [0] legal when t0 in {s32, v4s32}  -> no match
  [1] widen t0 to s32 when (scalar(t0)) and (bits(t0) < 32)  -> no match
  [2] narrow t0 to s32 when (scalar(t0)) and (bits(t0) > 32)  -> MATCH
Result: NarrowScalar t0 -> s32
~~~

### 8.4 列出 opcode

~~~powershell
python -m legispec --rules ...\toycpu.lspec opcodes
~~~

### 8.5 运行测试

~~~powershell
python -m unittest discover -s <legacy-checkout>\tools\legispec\tests -v
~~~

---

## 9. 测试与验证

当前 45 个测试全部通过，覆盖：

| 类别 | 例子 |
|---|---|
| 整数 ALU | `G_ADD s32/s16/s48/s64` |
| 规则顺序 | shift 先 clamp amount 再 clamp result |
| ICMP | `v4s1/v4s32` → Custom；`s1` 走 clamp min |
| 单步语义 | `s48` 先 widen 到 `s64`，不是一步到 `s32` |
| PHI | `s1` → `s32` |
| Libcall | `G_SDIV s32/s64`；`s16` 先 widen |
| 向量 | shuffle/concat custom；scalar shuffle unsupported |
| 内存 | 值类型、MMO type、最小对齐 |
| 转换 | ZEXT/SEXT/TRUNC |
| alias | 多 opcode 共享对象；重复定义报错 |
| 宏展开 | clamp 两步、next_pow2、next_multiple、scalarize |
| 语法错误 | 坏类型、缺分号、未知宏、未知 predicate |

测试中的期望值来自 LLVM 源码，而不是实现本身：

- `ToyCPULegalizerInfo.cpp`
- `LegalizerInfo.cpp`
- `LegalityPredicates.cpp`
- `LegalizeMutations.cpp`

---

## 10. 用它学习 C++ 规则的六个动作

### 动作 1：把 MIR 变成 Query

~~~text
%6:_(v4s1) = G_ICMP intpred(slt), %0:_(v4s32), %2:_(v4s32)
=> Query("G_ICMP", [v4s1, v4s32])
~~~

### 动作 2：找 RuleSet

~~~powershell
python -m legispec --rules ... explain G_ICMP
~~~

### 动作 3：确认 type index

看 `GenericOpcodes.td`，不要数 operand。

### 动作 4：跑 trace

~~~powershell
python ... trace G_ICMP --type v4s1 --type v4s32
~~~

你会直接看到哪一条规则命中，以及为什么前面的规则没命中。

### 动作 5：读 action/mutation

`Result: Custom` / `NarrowScalar t0 -> s32`。

### 动作 6：回到 C++ 看 helper

~~~text
NarrowScalar -> narrowScalar -> narrowScalarAddSub
WidenScalar  -> widenScalar  -> G_PHI 特例
Libcall      -> libcall      -> createLibcall -> CallLowering
Custom       -> legalizeCustom -> target switch
~~~

DSL 负责“规则层”，helper 负责“操作语义层”。两层分开看，才不会混。

---

## 11. 边界与后续扩展

### 11.1 当前边界

- **只做单步**：不实现 `LegalizerPass` 的不动点循环；
- **不做 MIR 改写**：不生成 `G_UADDO/G_UADDE/G_MERGE_VALUES`；
- **不做 coverage 检查**：LLVM 的 `verifyTypeIdxsCoverage` 未建模；
- **不做 atomic ordering predicate**：`MemDesc.ordering` 字段已保留，但规则语言还没有谓词；
- **不做 selector / regbank**；
- **不做 libcall 名称选择**：`libcall` 只是 action，不查 `getRTLibDesc`。

### 11.2 扩展点

- 新 predicate：在 `model.py` 加一个 `Pred` 子类，在 `parser.parse_atom` 加分支；
- 新 mutation：在 `model.py` 加一个 `TypeExpr` 子类；
- 新 action：在 `Action` 与 `ACTION_VERB` 里加枚举；
- 新宏：在 `parser.parse_macro` 里按 LLVM builder 的展开顺序追加 Rule；
- 想验证“展开是否等价”：用 `explain` 对比 LLVM 的 `-debug-only=legalizer` 输出。

### 11.3 下一步可做的验证

1. 用 `llc -stop-after=legalizer -debug-only=legalizer` 抓真实 ToyCPU 的规则匹配日志；
2. 对同一批 Query 比较 Python `Step` 与 LLVM 日志中的 action；
3. 把差异归类为：
   - DSL 语法缺失；
   - 宏展开顺序错误；
   - LLVM 版本差异；
   - helper 层行为（不在本 DSL 范围）。

---

## 12. 结论

> **C++ 的 builder 是“写规则的程序”，这门 DSL 是“读规则的程序”。**
> 两者共享同一套语义：有序规则、first-match、action + mutation。
> 宏只是把 builder 的展开过程显式化；helper 仍然是另一层。

一句话记忆：

> **先用 DSL 看清“下一步做什么”，再回 C++ 看“这一步怎么做”。**