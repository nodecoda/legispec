# LegiSpec DSL 规范 v1.0

> 地位：本文件是**规范文本（spec）**。`ToyCPULegalizerInfo.cpp` 是参考实现（reference implementation），不是规范来源。
> 方法论：立法 / 派生 / 证伪。
> 适用范围：GlobalISel Legalizer 的 **policy 层**，即 `LegalityQuery -> LegalizeActionStep`。
> 明确出界：`LegalizerHelper` 的 MIR 重写、硬件语义、真实 LLT 全域。
>
> 状态标记：
> - `[impl]` 现有 Python 实现已覆盖；
> - `[P2]` 计划在 Phase 2 落地；
> - `[P3]` 计划在 Phase 3 落地；
> - `[P5]` 由 C++ oracle 对拍验证。

---

## 0. 规范的地位与效力

1. 本文件是唯一规范文本。出现分歧时，**不得**以 C++ 代码自动改写本文件。
2. 分歧必须归入三类之一：`spec-wrong` / `cxx-wrong` / `unspecified`。没有第四类。
3. 检查结果三值：`PASS` / `FAIL` / `UNKNOWN`。`UNKNOWN` 不视为通过。
4. 每条规范条款必须可证伪：能回答“如果这条写错了，哪个观测会变”。

---

## 1. 词法

### 1.1 字符与空白

- 源文件为 UTF-8；有效字符为 ASCII 可打印字符与换行。
- 文件开头的 UTF-8 BOM（`U+FEFF`）被忽略，且不计入列号。
- 空白：空格、制表符、`\r`、`\n`，仅用于分隔 token。
- 注释：`#` 或 `//` 到行尾；注释是空白，不进入 AST。
- 语句以 `;` 终止；`ruleset` 与 `domain` 块用 `{ }`。

### 1.2 Token 类别

| 类别 | 形式 | 例 |
|---|---|---|
| `IDENT` | `[A-Za-z_][A-Za-z0-9_]*` | `G_ADD`, `ruleset`, `t0` |
| `INT` | `-?[0-9]+` | `32`, `0`, `-1` |
| `LLT` | 见 §2.7 | `s32`, `p0`, `p0:64`, `v4s32` |
| 标点 | `{ } ( ) [ ] , ; = : .` | |
| 运算符 | `== != < <= > >=` | |
| 关键字 | §2 中出现的固定标识符 | `ruleset`, `when`, `to`, `in`, ... |

> `[impl]` 词法在 `src/legispec/parser.py:100` 实现；`Token` 携带 `line` / `col`（`parser.py:78`）。

### 1.3 位置

每个 token 携带 `(line, col)`，从 1 开始。`[P2]` 每个 AST 节点携带 `Span(start, end)`。

---

## 2. 语法

### 2.1 程序

```ebnf
program     = { top_level } EOF ;
top_level   = type_alias | domain | ruleset ;
```

`[impl]` `parse_program()` 接受 `type`、`domain` 与 `ruleset`（`parser.py:225`）。一个文件最多一个 `domain`；重复声明报 `P020`。

### 2.2 类型别名 `[impl]`

```ebnf
type_alias = "type" IDENT "=" type ";" ;
```

约束：

- `W010`：别名不得遮蔽内建 LLT 拼写（`parser.py:228`）；
- `W011`：别名不得重复定义（`parser.py:233`）。

### 2.3 Domain 声明 `[P2]`

```ebnf
domain      = "domain" "{" { domain_item } "}" ;
domain_item = types_decl | imms_decl | mems_decl | q0_decl ;
types_decl  = "types" "{" [ type { "," type } ] "}" ";" ;
imms_decl   = "imms" "{" [ INT { "," INT } ] "}" ";" ;
mems_decl   = "mems" "{" [ mem { "," mem } ] "}" ";" ;
q0_decl     = "q0" "{" { query_decl } "}" ;
query_decl  = "(" opcode "," "[" [ type { "," type } ] "]"
              [ "," "mem" "=" "[" [ mem { "," mem } ] "]" ]
              [ "," "imm" "=" "[" [ INT { "," INT } ] "]" ] ")" ";" ;
```

语义：`domain` 声明**抽象域 D** 与**初始查询集 Q0**。没有 `domain` 时，覆盖与穷尽检查无定义，`check --strict` 必须报 `FAIL`（`W014`，覆盖内部记为 `C000`）。

约束：

- `types` / `imms` / `mems` / `q0` 每个最多出现一次；重复报 `P021`；未知条目报 `P019`；
- `q0` 的每条 query 以 `;` 终止；`q0` 块本身不带尾随 `;`；
- **arity 不从 C++ 借**：某 ruleset 的 type index 数量由其任一成员的 Q0 query 形状确定。一个 ruleset 若没有任何 Q0 query，则 arity / coverage 为 `UNKNOWN`（`C000`），不得猜；
- Q0 中出现的 opcode 若没有 ruleset，报 `C003` ERROR。

### 2.4 规则集 `[impl]`

```ebnf
ruleset   = "ruleset" opcode { "," opcode } "{" { statement } "}" ;
opcode    = IDENT ;
statement = core_rule | macro ";" ;
```

语义：`ruleset A, B { ... }` 中的 `A`、`B` 共享**同一个** `RuleSet` 对象（`parser.py:237`、`model.py:682`）。因此：

- `W003`：任一 opcode 不得在多个 ruleset 中出现（`model.py:716`）；
- `W004`：别名组内的 opcode 不得再单独定义规则。

### 2.5 核心规则 `[impl]`

```ebnf
core_rule = action [ type_idx "to" mutation ] [ "when" predicate ] ";" ;
action    = "legal" | "custom" | "unsupported" | "libcall" | "lower"
          | "narrow" | "widen" | "bitcast" | "more" | "fewer" ;
type_idx  = "t" INT | INT ;
```

规则语义：`Rule(action, predicate, type_idx, mutation)`，按**书写顺序**成为一个有序表。

### 2.6 宏与展开 `[impl]`

宏在**解析期**展开为一条或多条核心规则。展开是规范的一部分，不是实现细节。

| 宏 | 展开 |
|---|---|
| `legal_for {T...}` | `LEGAL when t0 in {T...}` |
| `libcall_for {T...}` | `LIBCALL when t0 in {T...}` |
| `legal_for_pairs {(a,b)...}` | `LEGAL when pair(t0,t1) in {...}` |
| `custom_for_pairs {(a,b)...}` | `CUSTOM when pair(t0,t1) in {...}` |
| `clamp_scalar i, min, max` | 两条：`WIDEN t_i -> min when scalar(t_i) and bits(t_i) < bits(min)`；`NARROW t_i -> max when scalar(t_i) and bits(t_i) > bits(max)`（**顺序固定：先 min 后 max**） |
| `widen_to_next_pow2 i, n` | `WIDEN t_i -> next_pow2(bits(t_i), n) when scalar(t_i) and not is_pow2(bits(t_i))` |
| `widen_to_next_multiple i, n` | `WIDEN t_i -> next_multiple(bits(t_i), n) when scalar(t_i) and not is_multiple(bits(t_i), n)` |
| `legal_for_mem {(t0,t1,m,align)...}` | `LEGAL when mem_compat(t0,t1,mem0) in {...}` |
| `always_legal` | `LEGAL when true` |

宏展开规则：

- `M000`：展开必须终止、无递归；当前宏集合是有限的，禁止用户定义宏；
- `M008`：`clamp_scalar` 要求 `bits(min) <= bits(max)`；
- 展开后的规则序号（从 0 开始）是 spec 的一部分，witness 可以引用。

### 2.7 类型文法 `[impl]`

```ebnf
type  = LLT | IDENT ;
LLT   = "s" INT
      | "p" INT [ ":" INT ]
      | "v" INT type ;
```

语义：

| 形式 | 含义 | 例 |
|---|---|---|
| `sN` | 标量，位宽 N | `s1`, `s32`, `s64` |
| `pA` | 指针，地址空间 A，位宽取 target（ToyCPU 为 32） | `p0` |
| `pA:B` | 指针，地址空间 A，位宽 B | `p0:64` |
| `vN T` / `vN sB` | 固定向量，N 个元素，元素类型 T | `v4s32`, `v4s1` |

约束：

- `W012`：`N >= 2`；`s` 与 `p` 的位宽必须为正；
- `W013`：向量元素不得为向量（禁止嵌套）；
- `T011`：`v1` 不是可表示的 LLT。LLVM 的 `ElementCount::getFixed(1)` 被判定为 scalar，
  `LLT::vector` 断言 `!EC.isScalar()`（`LowLevelType.h:123-130`）。

### 2.8 谓词文法与优先级 `[impl]`

```ebnf
predicate = or_expr ;
or_expr   = and_expr { "or" and_expr } ;
and_expr  = not_expr { "and" not_expr } ;
not_expr  = "not" not_expr | "(" predicate ")" | atom ;

atom = "always" | "true"
     | "scalar" "(" type_idx ")"
     | "vector" "(" type_idx ")"
     | "pointer" "(" type_idx ")"
     | "same_size" "(" type_idx "," type_idx ")"
     | "is_pow2" "(" "bits" "(" type_idx ")" ")"
     | "is_multiple" "(" "bits" "(" type_idx ")" "," INT ")"
     | ( "bits" | "elem_bits" | "size" ) "(" type_idx ")" cmp INT
     | "lanes" "(" type_idx ")" cmp INT
     | "mem" INT "." "type" ( "in" type_set | cmp type )
     | "mem" INT "." "align" cmp INT
     | "imm" INT cmp INT
     | "pair" "(" type_idx "," type_idx ")" "in" pair_set
     | type_idx ( "in" type_set | ( "==" | "!=" ) type ) ;
cmp = "==" | "!=" | "<" | "<=" | ">" | ">=" ;
```

优先级（由低到高）：`or` < `and` < `not` < 原子。

谓词求值遇到缺失的操作数（索引超出 query 的 `types` / `mems` / `imms`）时返回 `false`，不抛异常（`model.py:483-486`、`model.py:512-513`）。

### 2.9 变异文法 `[impl]`

```ebnf
mutation = type
         | "next_pow2" "(" "bits" "(" type_idx ")" "," INT ")"
         | "next_multiple" "(" "bits" "(" type_idx ")" "," INT ")"
         | "element_of" "(" type_idx ")" ;
```

语义：

| 变异 | 结果 |
|---|---|
| `type` | 把 `t_i` 的元素位宽改为该类型的元素位宽（向量保留 lane 数） |
| `next_pow2(bits(t_i), n)` | 取不小于当前位宽的最小 2 的幂，再与 `n` 取较大者 |
| `next_multiple(bits(t_i), n)` | 向上取整到 `n` 的倍数 |
| `element_of(t_i)` | 取 `t_i` 的元素类型（标量取自身） |

---

## 3. 静态语义

### 3.1 判断形式

```text
Γ ⊢ ruleset OK
Γ ⊢ rule OK
Γ ⊢ query : Step
```

`Γ` 包含：类型宇宙 `types(D)`、立即数集合 `imms(D)`、内存描述集合 `mems(D)`、opcode 的 arity。

### 3.2 良构性（W）

| ID | 条款 | 严重级 |
|---|---|---|
| W001 | `type_idx` 必须落在 generic operand 范围内 | ERROR |
| W002 | `imm_idx` 必须落在 generic imm 范围内 | ERROR |
| W003 | opcode 不得跨规则集重复定义 | ERROR |
| W004 | 别名组内的 opcode 不得再定义规则 | ERROR |
| W005 | 规则集不得为空 | WARN |
| W006 | 需要 mutation 的 action（narrow/widen/bitcast/more/fewer）必须给出 mutation | ERROR |
| W007 | 不需要 mutation 的 action（legal/custom/unsupported/libcall/lower）不得给出 mutation | ERROR |
| W008 | `Rule.type_idx` 必须与 mutation 作用的索引一致 | ERROR |
| W009 | ruleset 的 opcode 必须存在于 GenericOpcodes 集合 | ERROR |
| W010 | 类型别名不得遮蔽内建拼写 | ERROR |
| W011 | 类型别名不得重复定义 | ERROR |
| W012 | LLT 位宽必须为正；向量 lane 数必须 ≥ 2 | ERROR |
| W013 | 禁止嵌套向量 | ERROR |
| W014 | `domain` 缺失时，覆盖类检查无定义 | FAIL |

`T011` 是 LLT 值层错误，由 `model.LLT.vector` 在解析期抛出；它不进入静态检查器的
W/M/C/R/K/T 分类，也不参与 D 的枚举。

### 3.3 变异健全性（M）

| ID | 条款 |
|---|---|
| M001 | `NARROW` 的新元素位宽必须严格小于旧值 |
| M002 | `WIDEN` 的新元素位宽必须严格大于旧值 |
| M003 | `BITCAST` 必须保持总位宽不变且类型不同 |
| M004 | `MORE` 必须增加 lane 数且元素类型不变 |
| M005 | `FEWER` 必须减少 lane 数且元素类型不变 |
| M006 | 变异动作不得返回 null mutation |
| M007 | 非变异动作不得携带 mutation |
| M008 | `clamp_scalar` 的 min 位宽不得大于 max 位宽 |

这些检查只在规则**实际可能匹配**的 query 上求值；否则报 `UNKNOWN` 而非 `PASS`。

### 3.4 覆盖（C）

| ID | 条款 | 严重级 |
|---|---|---|
| C001 | 某 opcode 的某 type index 在 D 的所有取值都未被任何规则谓词提及 | WARN |
| C002 | 某 imm index 在 D 的所有取值都未被提及 | WARN |
| C003 | Q0 中出现的 opcode 没有 ruleset | ERROR |

实现要求：谓词的 read-set 由结构化 AST 求出（`TypeIn.i`、`BitsCmp.idx`、`And/Or/Not` 并集），**不得**用“标记全部已覆盖”式豁免。

### 3.5 可达（R）

| ID | 条款 | 严重级 |
|---|---|---|
| R001 | 规则在 D 上无任何可满足的 query | WARN（`--strict` 下 ERROR） |
| R002 | 规则被前序规则完全遮蔽（在前序规则命中时不可达） | WARN |

判定方法：区间/集合抽象 + D 上枚举；无法判定报 `UNKNOWN`。

### 3.6 冲突（K）

| ID | 条款 | 严重级 |
|---|---|---|
| K001 | 两条规则在 D 上存在同时匹配的 query | WARN |
| K002 | K001 且两条规则的动作或变异不同（顺序敏感） | WARN |

说明：`first-match-wins` 使重叠**合法**，因此 K 类不升级为 ERROR；但必须显式记录，禁止“沉默的顺序依赖”。

### 3.7 终止（T）

| ID | 条款 | 严重级 |
|---|---|---|
| T001 | 单条规则把 `t_i` 映射为自身（自环） | ERROR |
| T002 | 两条及以上规则构成类型环（如 `s48 -> s64 -> s32 -> s48`） | ERROR |
| T003 | 无法构造 ranking function | UNKNOWN |
| T004 | fixpoint 超过步数预算 | UNKNOWN |

ranking function 要求：存在良基测度 `μ`，每条规则使 `μ` 非增，且至少一条严格下降。

### 3.8 能力（P）

| ID | 条款 | 严重级 |
|---|---|---|
| P001 | `CUSTOM` 未声明后端 handler | ERROR（Phase 6 起） |
| P002 | `LIBCALL` 未声明可用 libcall | ERROR（Phase 6 起） |
| P003 | `LOWER` 的 helper 支持情况未知 | UNKNOWN |

---

## 4. 动态语义

### 4.1 Query

```text
Query = (opcode, types[], mems[], imms[])
MemDesc = (memory_ty, align_bits)
```

缺失的 `types` / `mems` / `imms` 视为“该索引不存在”。

### 4.2 Step

```text
Step = (action, type_idx, new_type, rule_index)
```

- `action ∈ {Legal, NarrowScalar, WidenScalar, Bitcast, MoreElements, FewerElements, Lower, Libcall, Custom, Unsupported}`
- `rule_index = None` 仅当 `action = Unsupported` 且无规则命中。

### 4.3 `apply`（单步）

```text
apply(rs, q):
    for i, r in enumerate(rs.rules):
        if r.pred(q):
            return Step(r.action, r.type_idx, r.mutation(q), i)
    return Step(Unsupported, 0, None, None)
```

**first-match-wins**；顺序是语义的一部分。`[impl]`：`model.py:692-708`。

### 4.4 `legalize`（fixpoint）

```text
legalize(q0, budget):
    q = q0; trace = []
    for step in 0..budget-1:
        s = apply(q)
        trace.push(s)
        if s.action in {Legal, Custom, Unsupported}: return (q, trace)
        q = q.with_type(s.type_idx, s.new_type)
        if q in visited: return UNKNOWN(cycle)
    return UNKNOWN(budget)
```

`Lower`、`Libcall` 被视为终止动作（由 helper 接管），不再重写 query。

### 4.5 轨迹等价的前提

> 若两条实现对 `R` 上每个 query 的单步结果相同，且都终止，则轨迹相同（对步数归纳）。
> 因此 `[P5]` 的 oracle 只需对拍**单步**，无需对拍整条 pass。

---

## 5. 显式约定（从隐式变显式）

| # | 约定 | 本 spec 的处理 |
|---|---|---|
| 1 | `t0` 通常是结果类型 | 不假设；每条规则显式写出索引 |
| 2 | 规则顺序 = 优先级 | §4.3 明文规定 |
| 3 | `clamp_scalar` = 先 min 后 max | §2.6 明文规定 |
| 4 | 无规则命中 = Unsupported | §4.3 明文规定 |
| 5 | 缺失操作数使谓词为 false | §2.8 明文规定 |
| 6 | 内存兼容按“总位宽相同 + 对齐不小于要求” | §2.6 + `MemCompatInSet` |
| 7 | 宏展开期 vs 运行期 | §2.6：解析期 |
| 8 | `Custom`/`Libcall` 的实际行为 | 明确出界，见 §6 |

---

## 6. Unspecified 清单

以下情形**不在本 spec 的语义范围内**，检查器必须报 `UNKNOWN` 或直接跳过，不得假定为正确：

| ID | 情形 | 归属 |
|---|---|---|
| U1 | `LegalizerHelper` 如何执行 `NarrowScalar` / `WidenScalar` / `Libcall` / `Lower` | helper 契约 |
| U2 | `legalizeCustom` 的具体重写 | 后端实现 |
| U3 | 真实 LLT 全域（scalable vector、vscale、非 32 位指针） | target 事实 |
| U4 | 内存序、原子语义 | ISA |
| U5 | 生成机器码的硬件正确性 | lit / 模拟器 |
| U6 | Q0 未给出任何 query 的 ruleset：arity 无定义 | 检查器报 `C000` UNKNOWN |

---

## 7. 与 C++ 的映射

| LegiSpec | C++ builder | 参考位置 |
|---|---|---|
| `legal_for` | `legalFor` | `LegalizerInfo.h:609` |
| `legal_for_pairs` | `legalFor({pair...})` | `LegalizerInfo.h:619` |
| `custom_for_pairs` | `customFor` / `customIf` | `LegalizerInfo.h:909` |
| `libcall_for` | `libcallFor` | `LegalizerInfo.h:782` |
| `clamp_scalar` | `clampScalar` | `LegalizerInfo.h:1142` |
| `widen_to_next_pow2` | `widenScalarToNextPow2` | `LegalizerInfo.h:974` |
| `widen_to_next_multiple` | `widenScalarToNextMultipleOf` | `LegalizerInfo.h:984` |
| `legal_for_mem` | `legalForTypesWithMemDesc` | `LegalizerInfo.h:649` |
| `always_legal` | `legalIf(always)` | `LegalizerInfo.h:602` |
| `core_rule` | `actionIf / actionFor` | `LegalizerInfo.h:473` |
| `apply` | `LegalizeRuleSet::apply` | `LegalizerInfo.cpp:341-375` |
| `getAction` | `LegalizerInfo::getAction(LegalityQuery)` | `LegalizerInfo.h:1381` |

---

## 8. 版本与变更

- 版本号：`vMAJOR.MINOR`。
- 升版唯一合法理由：出现一个现有 spec 无法裁决的观测（反例）。
- 每次升版必须记录：反例、裁决类别、变更条款、受影响的 witness。
- 检查项预算：总数 ≤ 40；新增一项须删除一项或说明理由。

### 变更记录

| 版本 | 日期 | 变更 |
|---|---|---|
| v1.0 | 2026-10-19 | 初版：词法、语法、W/M/C/R/K/T/P 检查、动态语义、约定与 unspecified |
| v1.0.1 | 2026-10-19 | 反例修正：LLVM LLT 不能表示 `v1`；新增 `T011`，W012 要求 lane ≥ 2；D 移除 `v1*`（文件名 `legispec-v1.0.md` 为稳定标识） |