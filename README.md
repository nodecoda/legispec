# Legalizer DSL

把 LLVM GlobalISel 的 **`LegalizerInfo` 规则表**从 C++ 内嵌 DSL 里抽出来，用一种外部声明式语言（LDSL）重写；
提供严格检查器、C++ 回生成器，以及对着真实 target oracle 做差分的证明工具链。

一句话：**它是规则表的规范与证伪工具，不是另一个 legalizer。**

## 1. 唯一命题

```text
DSL.getAction(q) == LegalizerInfo::getAction(q)
q ∈ D ∪ R
D = 规则文件声明的有限域（经 domain/filter 过滤）
R = closure(Q0) 在 D 内的一步合法化后继
```

边界（完整版本见 [`docs/CLOSURE.md`](docs/CLOSURE.md)）：

- 只比较 `LegalizerInfo::getAction(LegalityQuery)` 的**一步决策**；
- 不含 `LegalizerHelper::legalizeInstrStep` 的 MIR 改写，不含迭代 Legalizer pass、ISel、寄存器分配；
- 域外后继记为 boundary，不算证据；
- `custom` / `libcall` / `lower` 只证明“策略层把活交给谁”，不证明 handler 生成的 MIR。

**因此：LDSL 是决策函数的完整规格，是改写函数的接口契约，不是完整语义规范。**

## 2. 不是什么

| 不是 | 原因 |
|---|---|
| 通用编译器前端 | 语法原语只覆盖 `LegalizerInfo` builder 能表达的东西 |
| TableGen 替代品 | 输入是规则语义，不是 record；产出是 C++ builder 调用 |
| LLVM 官方项目 | 下游工具，与 LLVM 项目无隶属关系（见 [`GOVERNANCE.md`](GOVERNANCE.md)） |
| 免证据的正确性声明 | 无报告 + oracle 哈希，`claims verify` 不会给你 PROVEN |

## 3. 快速开始

```powershell
python -m pip install -e .
legalizer-dsl --rules rules/toycpu.ldsl check --strict
python -m unittest discover -s tests
```

`check --strict` 会做语法、语义、capability 契约、可达性/遮蔽全套检查；
`--strict` 缺 `capabilities/<rules>-capabilities.json` 即失败，不允许“静默放行 custom”。

生成 C++：

```powershell
legalizer-dsl --rules rules/aarch64-vector.ldsl codegen --out-dir out/codegen
```

生成器契约检查（22 份规则、所有 config 变体、双次生成确定性、manifest provenance）：

```powershell
python scripts/check_codegen_contract.py
```

集成与覆盖率检查：

```powershell
python scripts/integration_smoke.py --target toycpu --limit 0
python scripts/integration_smoke.py --target aarch64-vector --limit 2000 --out out/integration-aarch64-report.json
python scripts/check_integration_status.py --require-pass out/integration-report.json out/integration-aarch64-report.json
python scripts/coverage_report.py --corpus examples --out out/coverage-report.json
```

## 4. 仓库结构

```text
src/legalizer_dsl/        实现：parser / checker / interp / codegen / conformance / claims
  checker/                规则级静态检查（wellformed / conflict / reachability / termination）
rules/                    22 份 .ldsl 规则转写（ToyCPU、X86-64、AArch64、RISC-V）
witness/                  9 份 witness：固定顺序依赖与边界
capabilities/             22 份 provider 契约（custom / libcall / lower 谁能接）
claims/                   claims.json（43 条断言与哈希钉）、generated-evidence.json（generated/hand-only 分类）、claim-ledger.md、构造覆盖
docs/                     命题、语法、模式、配置、决策记录；兼容性见 COMPATIBILITY.md
examples/                 真实 MIR / LLVM IR 语料
tests/                    377 个单元测试
scripts/                  证据收集与批处理
out/                      本地证据缓存（不入库，见第 6 节）
```

## 5. 已证数据（pinned）

```text
claims=43            # 每条都钉住 rules 内容哈希 + report 哈希 + oracle 哈希
hand-claims=27
generated-claims=16
generated-covered=16
hand-only=6
rules-files=22
hand-queries=321627
constructs=63/63     # LLVM builder 方法 → DSL 原语，全部归类
tests=377
```

这些数字由 `python -m legalizer_dsl.closure check` 从产物重新计算并比对；
`generated-covered=16` 表示 GeneratedCpp 等价只对 16 条 generated claim 成立；
其余 6 条规则在 `claims/generated-evidence.json` 中显式登记为 hand-only，
只证明 hand-evaluator 等价。closure 会拒绝既无 generated 证据、又无 hand-only 登记的新规则。
文档数字漂移会让门禁直接失败，避免“文档比代码乐观”。

证据状态必须分清：

- `STRUCTURAL_ONLY`：`ldsl-closure check --skip-evidence`，只检查结构、契约和 pinned 数字，不证明报告/oracle 字节；
- `EVIDENCE_VERIFIED`：`ldsl-claims verify --verify-corpus` 加完整 `ldsl-closure check`，43/43 claim 逐份审计；
- `INTEGRATION_VERIFIED`：`integration_smoke.py` 状态为 `PASS`，生成 C++ 真实编译、链接、运行并与可用 oracle 对齐；
- `UNKNOWN` / boundary 永远不算通过。

版本矩阵、产物失效规则和 LLVM 升级 playbook 见
[`docs/COMPATIBILITY.md`](docs/COMPATIBILITY.md)。

## 6. 证据是外置的

报告与 oracle 可执行文件是构建产物（Windows x64、内嵌本机 LLVM 构建路径），**不入库**：

```powershell
python scripts/collect_evidence.py --from <evidence-dir>   # 按 claims.json 的钉取回并校验 sha256
python -m legalizer_dsl.claims verify --verify-corpus      # 43/43 PROVEN，并重新枚举 D∪R
python scripts/refresh_evidence.py --all --require-all     # 规则/评估器变化后重跑全部报告
python scripts/refresh_evidence.py --all --write-ledger    # 接受刚重跑出的新钉号
python -m legalizer_dsl.closure check                      # 完整收口
```

没有证据目录时，`claims verify` 会明确报 `report missing` / `oracle missing`，
相关单元测试标记为 skip —— **不会伪装成通过**。

从零重建证据（编译 LLVM、生成 oracle、重跑差分）见 [`docs/EVIDENCE.md`](docs/EVIDENCE.md)。

## 7. 许可证

[Apache License v2.0 with LLVM Exceptions](LICENSE.TXT)，与 LLVM 项目使用的许可证**逐字相同**。
每个源文件带 SPDX 头：

```text
SPDX-License-Identifier: Apache-2.0 WITH LLVM-exception
```

## 8. 治理

- [`CONTRIBUTING.md`](CONTRIBUTING.md) —— 提交规则、证据规则、review 规则
- [`GOVERNANCE.md`](GOVERNANCE.md) —— 维护者模型、RFC 流程、版本与兼容策略
- [`CODE_OF_CONDUCT.md`](CODE_OF_CONDUCT.md) —— 行为准则
- [`SECURITY.md`](SECURITY.md) —— 漏洞与证据篡改的报告路径
