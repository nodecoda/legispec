# 正确性契约

> 本文件定义 Legalizer DSL **在什么条件下可以声称正确**。
> 它是规范性说明；机器可读形态见 `claims/claim-schema.json`，可执行检查见
> `legalizer_dsl.claims.validate_contract` 与 `legalizer_dsl.closure`。
> 与代码冲突时，以代码和实际证据为准，并应修正本文件或代码中的一方。

## 0. 一句话

**正确性不是“跑通了”，而是在明确参考、观测、域、配置和证据的前提下，
`D ∪ R` 上的条件观测等价。**

差分测试证明的是 conformance（与参考实现一致），不是 semantic validity
（参考实现本身是否符合目标 ISA 的语义）。

## 1. 命题

每个 claim 由以下元组定义：

```text
Claim = (P, G, Ref, k, D, R, U, Obs, ≈, E)
```

| 符号 | 含义 |
|---|---|
| `P` | LDSL 程序：规则 + `domain` + config |
| `G` | 由 `P` 生成的 C++（codegen 产物） |
| `Ref` | 固定 LLVM commit / build / target 的参考实现 |
| `k` | triple、CPU、features、指针宽度、`is64bit`、`sXLen` 等配置 |
| `D` | 规则文件声明的有限查询域 |
| `R` | `Q0` 在 `D` 内的合法化后继闭包 |
| `U` | 显式登记的 UNKNOWN 集合 |
| `Obs(q)` | `(Action, TypeIdx, NewType)`，不含 Python 侧 `rule_index` |
| `≈` | 逐分量相等；LLT 使用 LLVM 的 `LLT::operator==` |
| `E` | rules/report/oracle/corpus/evaluator/witness 的哈希与复现信息 |

核心命题（按 claim 分别成立）：

```text
对每个 claim k：
∀ q ∈ (D ∪ R) \ U :
    Obs(I_P(q)) ≈ Obs(Ref_k(q))

对每个 generated claim k（存在由 G_k 构建的 oracle）：
∀ q ∈ (D ∪ R) \ U :
    Obs(G_k(q)) ≈ Obs(Ref_k(q))
```

GeneratedCpp 命题**不是**对全部规则无条件成立。当前 43 条 claim 中有 16 条
generated claim；另外 6 条规则在 `claims/generated-evidence.json` 中显式登记为
hand-only，只支持第一条（hand-evaluator）等价，不支持第二条（GeneratedCpp）等价。
`closure check` 强制每条 `rules/*.lspec` 要么有 generated claim，要么有带原因的
hand-only 登记；新增规则不能静默绕过这个分类。

旁条件：

```text
WF(P)                 -- 语法、arity、类型、mutation 合法
Det(P)                -- first-match 确定
Term(P)               -- 闭包有限步终止
Elab(P) = P_hat       -- 宏、别名、config guard 展开忠实
Struct(G, P)          -- 生成代码与规则列表结构对应
Compile(G, Ref@c, k)  -- 真实编译、链接成功
Fresh(E)              -- 证据仍对应当前 P/G/Ref/k
Adequate(D)           -- 域覆盖真实查询形状
GeneratedCoverage     -- 每条规则有 generated 证据或显式 hand-only 登记
NoSilentSkip          -- 缺证据时失败或显式 UNKNOWN
```

## 2. 观测与等价

观测函数固定为：

```text
observe(step) = (Action, TypeIdx, NewType)
```

- 不比较 `rule_index`：它不是 `LegalizeActionStep` 的字段，只是 Python trace 信息。
- `NewType` 统一写成 DSL 拼写：`s32`、`p0`、`p0:64`、`v4s32`。
- 比较使用 `LLT::operator==` 语义：标量比位宽，指针比地址空间与位宽，向量比 lane 数与元素。
- 报告中的 `config` 必须与 claim 的 `config` 相等；不同配置是不同 claim。

## 3. 域、闭包与边界

```text
D  = 抽象类型域 × 立即数域 × 内存描述域 × arity
Q0 = 初始查询集
R  = closure(Q0) under legalize, taken inside D
U  = 显式登记的 UNKNOWN
```

规则：

1. `D` 是 claim 的一部分，不是实现细节。域外查询是 **boundary**，不是反例，也不是证据。
2. `R` 的后继一旦离开 `D`，记为 boundary，不进入证明。
3. 没有 `domain` 声明时，`check --strict` 必须失败；覆盖率与穷尽性无定义。
4. `U` 中每一项必须有非空 `reason`；`UNKNOWN` 不得计为通过。
5. 分母为 0 不是“全部通过”；空域、空 claim、空查询集必须报 `UNKNOWN` 或失败。

## 4. 正确性分层

| 层 | 命题 | 主要证据 | 典型失败 |
|---|---|---|---|
| L0 语法 | LDSL 可解析 | parser、EBNF、诊断 | token/宏写错 |
| L1 静态语义 | 规则表自洽 | wellformed/arity/mutation/termination | 负索引、非法 mutation |
| L2 elaboration | 宏/别名/config 展开忠实 | 展开测试、guard 测试 | clamp 少展开一半 |
| L3 codegen 结构 | AST -> builder 调用序列忠实 | structure manifest、golden tests | 顺序/别名错误 |
| L4 编译集成 | 生成物可编译链接 | target build、consumer fixture | 私有/不存在 API |
| L5 行为等价 | `D∪R\U` 观测一致 | 双 oracle 差分 | 语义偏差 |
| L6 顺序/遮蔽 | first-match、重叠、不可达被固定 | witness、K001/K002 | 重构改变顺序 |
| L7 证据完整性 | 证明新鲜、可复现 | 哈希账本、STALE、corpus verify | 旧证据仍被引用 |
| L8 覆盖充分性 | 域覆盖真实查询 | MIR corpus 测量、boundary 统计 | 域缩小消分歧 |
| L9 MIR 语义 | helper 改写正确 | 独立模型/测试 | **当前明确出界** |

`L0`–`L4` 是“生成器正确”；`L5`–`L6` 是“行为正确”；
`L7`–`L8` 是“证据可信”；`L9` 是“语义正确”，不属于当前命题。

## 5. 状态与分歧

每个 claim 的状态只能是：

```text
PROVEN   -- 所有旁条件成立，且 D∪R\U 上零分歧
FAIL     -- 任一可观测条件不成立
UNKNOWN  -- 证据缺失、环境不可用、或存在显式 U
```

任何规则/实现/参考之间的差异必须归入且仅归入：

```text
spec-wrong     -- LDSL 规范或实现写错
cxx-wrong      -- 参考实现/目标后端写错
unspecified    -- 当前规范未定义，登记为 UNKNOWN 或补充规范
```

没有分类的分歧不得通过；不得通过缩小 `D` 或修改规则让分歧“消失”而不记录原因。

## 6. claim 账本契约

`claims/claims.json` 的顶层字段：

| 字段 | 含义 |
|---|---|
| `schema` | 固定为 `legalizer-dsl-claims-v1` |
| `claim_contract_version` | 本契约版本，当前为 `1` |
| `llvm_commit` | 40 位小写 hex 的参考 commit |
| `boundary` | 全局证明边界 |
| `evaluator_sha256` | Python 求值器指纹 |
| `contract` | 观测、等价、域、闭包、UNKNOWN、证据字段名 |
| `claims` | claim 数组 |

每条 claim 的必填字段：

```text
id, rules, report, oracle, scope, config,
queries, rules_total, rules_matched,
status, unknown, llvm_commit,
rules_sha256, report_sha256, corpus_sha256, oracle_sha256
```

可选证据字段：

```text
witness_sha256, evaluator_sha256, codegen_sha256,
report_file_sha256, unmatched, excludes,
declared_unreachable, declared_shadowed
```

校验入口：

```powershell
python -m legalizer_dsl.closure check --skip-evidence  # 结构 + 契约 + pinned 数字
python -m legalizer_dsl.claims verify --verify-corpus   # 完整证据
```

## 7. 明确不证明的内容

- `LegalizerHelper::legalizeInstrStep` 的 MIR 改写；
- 迭代 Legalizer pass、ISel、RegBankSelect、regalloc、codegen；
- `custom` / `libcall` / `lower` 的 handler 函数体；
- scalable vector、SVE、完整 ABI、全部内存描述符；
- 参考实现本身的 ISA 语义正确性；
- `D ∪ R` 之外的查询。

一句话：

> **LDSL 是决策函数的完整规格，是改写函数的接口契约，不是完整语义规范。**