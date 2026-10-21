# Legalizer DSL — 项目收口

Date: 2026-10-21
Status: **CLOSED**（范围冻结；新增能力必须走 §8 playbook，不得修改既有证明）
LLVM: `F:\llvm\llvm-project` @ `d855d9e48067026317998cbff734004f328f833d`
Build: `F:\llvm\build`（AArch64 / RISC-V / X86），`F:\llvm\build-toycpu`（ToyCPU）

## 0. 一句话

把 LLVM GlobalISel 的 **LegalizerInfo 规则表**从 C++ 内嵌 DSL 里抽出来，
用外部 DSL 重写、严格检查、生成回 C++，再用真实 target oracle 做差分，
在声明域内证明：

```text
DSL.getAction(q) == LegalizerInfo::getAction(q)
```

它不是“再写一个 legalizer”，而是**规则表的规范与证伪工具**。

## 1. 唯一命题与证明边界

命题：

```text
DSL.getAction(q) == LegalizerInfo::getAction(q)
q ∈ D ∪ R
D = domain 块声明的有限域（经 filter 过滤）
R = closure(Q0) 在 D 内的一步 legalize 后继
```

边界（写进 `claims.json` 的 `boundary` 字段）：

- 只比较 `LegalizerInfo::getAction(LegalityQuery)` 一步；
- 不含 `LegalizerHelper::legalizeInstrStep` 的 MIR 改写；
- 不含迭代 Legalizer pass、ISel、寄存器分配、codegen；
- 域外后继记为 boundary，不是证据；
- `custom` 只证明“策略层交给谁”，不证明 handler 生成的 MIR；
- `libcall` / `lower` 只证明策略动作，不证明调用体/展开体。

一句话：**DSL = 决策函数的完整规范 + 改写函数的目标侧接口契约；DSL ≠ 完整语义规范。**

## 2. 资产地图

| 文件 | 职责 | 什么时候看它 |
|---|---|---|
| `grammar.ebnf` | LDSL 语法权威 | 想知道“这个写法合不合法” |
| `model.py` | LLT / Query / Action / 谓词 / mutation / Domain AST 与求值 | 加原语、改语义 |
| `parser.py` | 手写递归下降解析 + 诊断 | 语法错误、宏展开、config guard |
| `codegen.py` | AST → C++ `getActionDefinitionsBuilder` | 生成端对不上手写端 |
| `conformance.py` | 枚举 D∪R，跑 oracle 差分，写报告 | 证明等价 |
| `interp.py` | 一步 legalize 闭包、`OutOfDomain` | 看后继为什么出域 |
| `coverage.py` | 规则可达性 / 重叠 / 遮蔽 | 解释 K001/K002/不可达 |
| `constructs.py` | C++ builder 方法 → DSL 原语覆盖 | 判断“还缺哪个原语” |
| `claims.py` | 哈希账本、STALE 棘轮、`--write` | 规则/报告/oracle 变了 |
| `witness.py` | 正例/负例/顺序 witness | 固定顺序依赖与边界 |
| `diagnostics.py` | T/K/C 诊断码 | 读懂报错 |
| `measure_concat_shapes.py` | 真实 MIR 形状审计 | 域是否覆盖真实查询 |
| `closure.py` | 收口审计：pinned 数字 vs 产物 | 文档数字或资产清单漂移时 |
| `cli.py` / `legalizer-dsl` | `check` / `codegen` / `conform` / `witness` | 日常入口 |
| `rules/*.ldsl` | 22 份规则转写 | 证明对象 |
| `tests/` | 377 个单元测试 | 改任何实现前先跑 |
| `witness/` | 每片的固定证据 | 回归 |
| `*capabilities.json` | custom/libcall/lower 的 provider 契约 | strict check |

## 3. 终态数据（pinned）

<!-- closure:pinned -->
claims=43
hand-claims=27
generated-claims=16
generated-covered=16
hand-only=6
rules-files=22
hand-queries=321627
constructs=63
tests=377
<!-- /closure:pinned -->

这些数字由 `closure.py check` 从 `claims.json`、`rules/`、`construct-coverage.json`、
`tests/` 重新计算并比对。文档数字漂移会让门禁失败。

`rules_sha256` / `oracle_sha256` 仍由 `claims verify` 逐条校验；报告与 oracle 是构建产物，
放在 `out/`（不入库），取回与重建方式见 [`EVIDENCE.md`](EVIDENCE.md)。

## 4. 覆盖清单

GeneratedCpp 证据只覆盖下列 16 条 generated claim 对应的规则；另外 6 条规则是
显式 hand-only：`aarch64-pointer.ldsl`、`riscv-conditional-gconstant.ldsl`、
`riscv-scalar-conditional.ldsl`、`riscv32-mzb-scalar.ldsl`、
`riscv32-pointer.ldsl`、`riscv64-pointer.ldsl`。原因与允许范围记录在
`claims/generated-evidence.json`。

| Target | 切片 | 代表查询数 | 规则命中 |
|---|---|---|---|
| ToyCPU | scalar + pointer + atomic + vector + calls（全策略层） | 243,714 | 87/89（2 条声明不可达） |
| X86-64 | integer + pointer shapes | 7,591 | 69/69 |
| AArch64 | scalar | 8,161 | 91/91 |
| AArch64 | scalar + CSSC | 8,161 | 95/95 |
| AArch64 | pointer | 924 | 17/17 |
| AArch64 | atomic generic / outline / LSE | 612 | 11/11、10/10、11/11 |
| AArch64 | fixed-vector | 176 | 10/11 |
| AArch64 | G_ADD family vector | 120 | 13/15 |
| AArch64 | G_MUL vector | 24 | 13/15 |
| AArch64 | G_SHL/G_ASHR/G_LSHR | 270 | 19/21 |
| AArch64 | G_ICMP result/operand | 173 | 19/23 |
| AArch64 | G_FCMP float | 185 | 13/14 |
| AArch64 | G_CONCAT_VECTORS custom/bitcast | 28 | 3/3 |
| RISC-V RV64 | scalar / +m,+zmmul,+zbb / pointer | 7,861 / 7,861 / 770 | 103/103、117/118、32/32 |
| RISC-V RV32 | scalar / +m,+zmmul,+zbb / pointer | 7,861 / 7,861 / 770 | 100/100、111/115、31/31 |
| RISC-V | conditional G_CONSTANT / scalar（RV32/RV64 单源） | 9 / 7,861 | 4/4、108/108、100/101 |

“未命中”的原因分三类，全部可解释：声明不可达（永远被前面的规则截走）、
域外（不在 D）、以及 DSL 明确不建模的形状（见 §11）。

## 5. 证据链

```text
rules/*.ldsl
  -> parser.py            语法 / 宏 / config guard
  -> model.py             语义求值
  -> check --strict       K001 重叠、K002 顺序敏感、C004 custom 覆盖、可达性
  -> codegen.py           生成 C++ builder
  -> oracle build         真编译（暴露 API 误用，例如 isFixedVector 不存在）
  -> conformance.py       D∪R 枚举 + 真 target 差分（0 divergence）
  -> witness.py           正/负/顺序 witness
  -> claims.py            rules/report/oracle 三重 sha256 + STALE 棘轮
  -> gate                 单切片 stop rules
  -> suite                11 个 gate + 最终账本
```

每一环抓不同的错：

- 静态检查抓“规则表内部矛盾”；
- 编译抓“生成代码 API 用错”；
- 差分抓“语义与 LLVM 不一致”；
- witness 抓“顺序依赖被重构掉”；
- 账本抓“证明过期仍被引用”。

## 6. 四个反自欺设计

1. **域显式，且 filter 参与证明。**
   域外不是“反例”，是“未声明”。因此边界必须来自 opcode 契约，不能来自实现习惯。
   `G_CONCAT_VECTORS` 从 k=2 扩到 k≥2 就是一次覆盖债清偿：依据是
   `MachineVerifier.cpp:1611-1631`，并用真实 MIR 测到 k=4/k=8。
2. **hand == generated 双 oracle（仅对 generated claim）。**
   对 16 条 generated claim，手写规则和生成规则分别编译成 oracle，二者都必须与
   LLVM 零分歧；这同时证明“转写正确”和“codegen 正确”。
   其余 6 条规则在 `claims/generated-evidence.json` 中显式登记为 hand-only，
   只证明 hand-evaluator 等价，**不**证明 GeneratedCpp 等价。
   `closure check` 会强制每条规则要么有 generated claim，要么有带原因的 hand-only 登记，
   防止以后新增规则时静默扩大 GeneratedCpp 命题。
3. **哈希账本 + 只减不增的棘轮。**
   规则内容、报告、oracle 二进制任一变化都会让 claim STALE。
   想恢复“已证明”，只能重跑，不能改账本。
4. **custom 明确排除。**
   custom 是“规则表把控制权交给 C++”的点，不是漏掉的规则。
   capabilities 只验证 provider 存在，不验证 handler 语义。

## 7. 能做什么 / 不能做什么

能：

- 给一个新的 target/opcode 切片，在有限域内证明策略与 LLVM 等价；
- 快速回答“这个 `(opcode, types)` LLVM 会返回什么 Action”；
- 从 LDSL 生成可编译的 `LegalizerInfo` 子集；
- 用 witness 固定顺序依赖、重叠、遮蔽；
- 用 `measure_concat_shapes.py` 把“域”从论证变成测量。

不能：

- 证明 `LegalizerHelper` 的 MIR 改写正确；
- 证明 custom handler / libcall / lower body；
- 证明 ISel、regalloc、codegen；
- 覆盖 scalable vector（SVE）、完整 ABI、内存描述符全集；
- 替代 LLVM 自己的测试套件。

## 8. 新增一片的 playbook

1. **选点**：一个 opcode + 一个固定配置（triple/cpu/features/pointer）。
2. **读 C++**：把 builder 展开成有序规则，记录文件与行号。
3. **写 domain**：types/imms/mems/q0 + filter；filter 的依据必须是 opcode 契约。
4. **写 LDSL**：`rules/<target>-<slice>.ldsl`，跑 `check --strict`。
5. **写 capabilities**：custom/libcall/lower 的 symbol 与 opcode。
6. **写 witness**：正例、负例、顺序敏感例。
7. **生成 + 建 oracle + 差分**：`codegen` → `build.ps1` → `conformance.py`。
8. **入账 + 门禁 + 文档**：`claims.py --write`、新增 `check-<slice>.ps1`、挂进 suite。

禁止：为了让新片通过而修改已有 rules 的语义。上游变了就重跑并接受失败。

## 9. 命令手册

```powershell
# 在仓库根目录执行。全量单元测试与结构门禁
python -X utf8 -m unittest discover -s tests -v
python scripts/check_all_rules.py
python -X utf8 -m legalizer_dsl.closure check --skip-evidence

# 生成器契约（22 份规则、所有 config 变体、双次确定性）
python scripts/check_codegen_contract.py

# 集成烟测（需要 LLVM 构建；没有环境会明确 SKIP: environment）
python scripts/integration_smoke.py --target toycpu --limit 0
python scripts/integration_smoke.py --target aarch64-vector --limit 2000 --out out/integration-aarch64-report.json
python scripts/check_integration_status.py --require-pass out/integration-report.json out/integration-aarch64-report.json

# 语料覆盖率棘轮
python scripts/coverage_report.py --corpus examples --out out/coverage-report.json

# 证据：完整审计、重跑、写回账本、收口
python -X utf8 -m legalizer_dsl.claims verify --verify-corpus
python scripts/refresh_evidence.py --all --require-all
python scripts/refresh_evidence.py --all --write-ledger
python -X utf8 -m legalizer_dsl.closure check

# 单切片语法/静态检查与代码生成
python -X utf8 -m legalizer_dsl --rules rules/aarch64-concat-vectors.ldsl check --strict
python -X utf8 -m legalizer_dsl --rules rules/aarch64-concat-vectors.ldsl codegen --out-dir out/codegen
```

版本矩阵和 LLVM 升级顺序见 [`COMPATIBILITY.md`](COMPATIBILITY.md)。

## 10. 阅读顺序（新人 2 小时）

1. 本文 §0–§6；
2. `grammar.ebnf`（只看 program/ruleset/rule/domain 四段）；
3. `rules/toycpu.ldsl`（最简单的完整规则表）；
4. `rules/aarch64-concat-vectors.ldsl`（custom + bitcast + 域过滤）；
5. `rules/riscv-scalar-conditional.ldsl`（config guard）；
6. `model.py` 的 `Action`/`Query`/`Domain`，再 `parser.py` 的 `parse_rule`；
7. `codegen.py` 的 `emit_ruleset`；
8. `conformance.py` 的 `iter_d_queries`/`closure_queries`；
9. `claims.py` 的 `verify`/`--write`；
10. `notes/legalize-mental-model.md` 与 `notes/target-subset-coverage.md`。

## 11. 已知债（明确不还）

- `constructs.py` 中 11 个 builder 方法仍为 `none`：
  `actionForTypeWithAnyImm`、`legalForTypeWithAnyImm`、`alignNumElementsTo`、
  `clampMaxNumElementsStrict`、`clampScalarOrElt`、`maxScalarOrElt`、
  `lowerIfMemSizeNotByteSizePow2`、`lowerIfMemSizeNotPow2`、
  `unsupportedIfMemSizeNotPow2`、`widenScalarOrEltToNextPow2OrMinSize`、
  `widenVectorEltsToVectorMinSize`。
  它们要么是 imm/mem 专用谓词，要么是 element-wise 变体，当前切片不需要。
- 5 个 `CartesianProduct` 方法只支持两槽（`partial`）：三槽以上乘积不在 DSL 内。
- 域的枚举是有限样本，不是全类型空间；证明是“样本内等价”。
- 真实 MIR 审计是 corpus 级测量，不是穷举。
- `-debug-only=legalizer` 依赖 assertions build。
- 生成的 C++ 只覆盖规则，不覆盖 capabilities / witness。

## 12. 交付判定

收口完成当且仅当：

```text
claims=43 proven=43 failed=0
377 tests OK（1 个环境相关 skip）
strict rules sweep 22/22
codegen contract PASS（22 份规则、24 个 config 变体）
integration smoke PASS（ToyCPU 全 D、AArch64 vector；SKIP 不算通过）
coverage ratchet PASS（不低于 coverage/coverage-baseline.json）
constructs 63/63 classified
closure.py check PASS（0 problems；pinned 数字与产物一致）
```

停止规则：

- 没有真实 target 需求，不再新增切片；
- 没有“C++ 构造无法表达”的证据，不再新增原语；
- 任何规则变更都必须重新走 §5 的证据链，不允许“只改账本”。