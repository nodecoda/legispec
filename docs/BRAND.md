# LegiSpec 命名规范（Brand Governance）

> 本文是命名事实源。文档、包元数据、命令行、数据目录、生成代码与证据 schema
> 的任何命名变更，先改这里，再改实现。

- 状态：1.0.0 起生效
- 适用范围：本仓库对外表达、命令行入口、发行元数据、生成物与证据格式
- 历史例外：仅两份冻结存档（由守卫测试的 `ARCHIVES` 显式列出）

---

## 1. 一句话定位

**中文**：LegiSpec 把 LLVM GlobalISel 的 legalizer 规则表变成可检查、可回生成、
可对拍的规范。

**English**：LegiSpec turns LLVM GlobalISel legalizer rule tables into a checkable,
regenerable, and falsifiable specification.

**Tagline**：Legality, specified. Equivalence, proven.

**边界不变**：LegiSpec 是规则表的规范与证伪工具，不是另一个 legalizer。

---

## 2. Canonical 词汇表

| 概念 | Canonical 值 |
|---|---|
| 产品名 | `LegiSpec` |
| 语言名 | `LegiSpec DSL` |
| 规则文件后缀 | `.lspec` |
| 主命令行 | `legispec` |
| 子命令行 | `legispec-claims`、`legispec-closure`、`legispec-constructs`、`legispec-diff-oracles` |
| Python 分发包 | `legispec` |
| Python import | `legispec` |
| 数据目录 | `share/legispec` |
| 环境变量 | `LEGISPEC_*` |
| CMake 变量 / 生成宏 | `LEGISPEC_*` |
| 证据 schema id | `legispec-*-v1` / `legispec-*-v2` |
| oracle 文件名前缀 | `legispec-oracle` |
| 生成 C++ target 前缀 | `legispec_` |
| Generator banner | `generator: legispec <version>` |

**拼写规则**

1. 产品名只有一种写法：`LegiSpec`。不得写作 `Legispec`、`LEGISPEC`、`Legi Spec`。
2. 命令行一律小写连字符：`legispec-claims`，不使用下划线。
3. 文件后缀一律小写、带点：`.lspec`。
4. Python 包名、import、数据目录一律小写：`legispec`。
5. 环境变量与 C++ 宏一律大写：`LEGISPEC_*`。

---

## 3. 1.0 硬切策略

1.0 是一次性硬切，不保留旧标识的兼容层：

- 不保留旧 Python import 路径；
- 不保留旧命令行别名；
- 不保留旧环境变量或 CMake 变量的回退读取；
- 不保留旧数据目录；
- 不保留旧 schema id 的写入路径；
- 不保留旧 oracle 文件名；
- 旧名只存在于两份不可改写的存档中。

这样做是为了避免长期维护两套 import 路径、两套 traceback、两套文档和两套
证据 schema。迁移成本一次性支付，之后不再分裂。

---

## 4. 历史存档

以下两类文件是历史记录，按治理规则保持原样，不参与命名守卫：

- 一份设计历史记录；
- 一份冻结的 v0.1 语言规范存档。

具体文件名由 `tests/test_brand.py` 的 `ARCHIVES` 显式列出。

`docs/legispec-v1.0.md` 是当前规范入口；历史存档只用于追溯，不作为新文档的
链接目标。

---

## 5. 守卫

`tests/test_brand.py` 锁定以下不变量：

1. `README.md` 首行是 `# LegiSpec`；
2. `docs/BRAND.md` 的 canonical 词汇表包含产品、语言、后缀与主命令；
3. 旧名 token 只允许出现在两份历史存档与守卫测试自身；
4. `pyproject.toml` 的分发包名、入口点与 data-files 全部使用 LegiSpec 标识；
5. `src/legispec/__init__.py` 存在，旧包目录不存在。

守卫失败时，优先修实现或文档，不要放宽 allowlist。
