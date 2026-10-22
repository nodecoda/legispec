# LegiSpec 命名规范（Brand Governance）

> 本文件是**命名事实源**。文档、CLI、包元数据、证据 schema 的任何命名变更，先改这里，再改实现。

- 状态：0.3.0 起生效
- 适用范围：本仓库对外表达、命令行入口、发行元数据
- 不适用范围：`docs/design-history.md` 等历史记录

---

## 1. 一句话定位

**中文**：LegiSpec 把 LLVM GlobalISel 的 legalizer 规则表变成可检查、可回生成、可对拍的规范。

**English**：LegiSpec turns LLVM GlobalISel legalizer rule tables into a checkable,
regenerable, and falsifiable specification.

**Tagline**：Legality, specified. Equivalence, proven.

**边界不变**：LegiSpec 是规则表的规范与证伪工具，不是另一个 legalizer。

---

## 2. Canonical 词汇表

| 概念 | Canonical | Legacy（仅迁移语境） | 硬切版本 |
|---|---|---|---|
| 产品名 | `LegiSpec` | `Legalizer DSL` | 0.3.0（文档） |
| 语言名 | `LegiSpec DSL`，缩写 `LDSL` | `Legalizer Rule DSL` | 0.3.0（文档） |
| 规则文件后缀 | `.lspec` | `.ldsl` | 0.2.0 已完成 |
| 主命令行 | `legispec` | `legalizer-dsl` | 0.3.0 别名 / 1.0 移除 |
| 子命令行 | `legispec-claims`、`legispec-closure`、`legispec-constructs`、`legispec-diff-oracles` | `ldsl-*` | 0.3.0 别名 / 1.0 移除 |
| Python 分发包 | `legispec` | `legalizer-dsl` | 1.0 |
| Python import | `legispec` | `legalizer_dsl` | 1.0 |
| 数据目录 | `share/legispec` | `share/legalizer-dsl` | 1.0 |
| 环境变量 | `LEGISPEC_*` | `LEGALIZER_DSL_*` | 1.0 |
| CMake 变量 | `LEGISPEC_*` | `LDSL_*` | 1.0 |
| 证据 schema id | `legispec-*-v1` | `legalizer-dsl-*-v1` | 1.0（读写双兼容） |

**拼写规则**

1. 产品名只有一种写法：`LegiSpec`。不得写作 `Legispec`、`LEGISPEC`、`Legi Spec`。
2. `LDSL` 只作为**语言缩写**，不再作为产品名或仓库名。
3. 命令行一律小写连字符：`legispec-claims`，不使用下划线。
4. 文件后缀一律小写、带点：`.lspec`。

---

## 3. 旧名 allowlist

以下位置允许出现 `Legalizer DSL` / `.ldsl` / `legalizer-dsl`，其余位置出现即视为品牌回退：

| 位置 | 原因 |
|---|---|
| `docs/BRAND.md` | 本文件需要举例说明旧名 |
| `docs/design-history.md` | 历史记录，不可改写 |
| `docs/ldsl-v0.1.md` | 规范稳定标识；**文件名不得修改**（见 `GOVERNANCE.md` §5） |
| 所有源文件的 SPDX 许可证头 | `Part of the Legalizer DSL project` 属于法律/来源声明，冻结至 1.0 |
| `claims/claim-schema.json` 的 `title` | 证据契约文本，改动会改动 ledger 结构 |
| `src/legalizer_dsl/checker/__init__.py:9` | 参与求值器指纹，改注释会让 43 条 claim 失效 |
| `src/legalizer_dsl/codegen.py:9` | 参与 codegen 指纹 |
| `src/legalizer_dsl/conformance.py:10` | 参与求值器指纹 |
| 生成物里的 `# generator: legalizer-dsl <ver>` 横幅 | 随 codegen 指纹冻结，1.0 与版本号一起改 |

> 源文件头不在 0.3.0 改动，是因为这些文件参与 `fingerprint.HAND_EVALUATOR_FILES` /
> `CODEGEN_FILES` 指纹；改一行注释会让 43 条 claim 全部失效。1.0 与包名迁移一起做。

---

## 4. 弃用策略

- 0.3.0 起，`legispec*` 为推荐入口；旧入口保留，功能完全一致。
- 旧入口的 `--help` 必须包含一行：`deprecated: use legispec`。
- 1.0 移除旧入口；移除前至少保留一个小版本周期。
- 任何入口改名都必须同时更新：`pyproject.toml`、`README.md`、`.github/workflows/ci.yml`、
  `scripts/wheel_smoke.py`、`tests/test_brand.py`。

---

## 5. 1.0 硬切清单（不在 0.3.0 范围）

1. PyPI 分发包 `legalizer-dsl` → `legispec`，旧包转为依赖新包的元包。
2. `src/legalizer_dsl` → `src/legispec`，并保留一个版本的 `legalizer_dsl` shim。
3. 环境变量双读：`LEGISPEC_*` 优先，`LEGALIZER_DSL_*` 回退并告警。
4. CMake 变量双读：`LEGISPEC_*` 优先，`LDSL_*` 回退并告警。
5. schema id 双读：读端同时接受 `legalizer-dsl-*-v1` 与 `legispec-*-v1`，写端切新 id。
6. 数据目录迁移：安装时写 `share/legispec`，保留 `share/legalizer-dsl` 一版。
7. 源文件 SPDX 头统一为 `Part of the LegiSpec project`。

每一项都必须：先改 `docs/decisions.md` 记录迁移方式，再改实现，最后重跑完整证据链。

---

## 6. 守卫

`tests/test_brand.py` 锁定三条不变量：

1. `README.md` 首行是 `# LegiSpec`。
2. `docs/BRAND.md` 的 canonical 词汇表包含 product / language / file / cli 四项。
3. `Legalizer DSL` 只出现在本文件 §3 的 allowlist 中。

守卫失败时，优先修文档，不要放宽 allowlist。
