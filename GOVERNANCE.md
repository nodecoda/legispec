# 治理

## 1. 与 LLVM 的关系

本项目是 **LLVM 生态的下游工具**：

- 使用 Apache License 2.0（`Apache-2.0`），
  这**不表示**本项目是 LLVM 官方项目，也不表示 LLVM 基金会背书；
- 引用 LLVM 源码位置（`file:line`）与 commit 作为证据，不改动 LLVM 源码；
- LLVM 上游行为变化时，本项目跟随，不反向要求上游对齐。

## 2. 维护者模型

当前为 **单一维护者 + 公开 RFC**：

| 角色 | 职责 |
|---|---|
| Maintainer | 合并权、发布权、证据账本签署（`claims verify --write`） |
| Reviewer | 按 `CONTRIBUTING.md` 第 6 节三项标准 review，无合并权 |
| Contributor | 提交 patch、规则、证据重跑结果 |

出现第二位长期贡献者后，改为 **两人 lazy consensus**：非争议改动 72 小时内
一个 maintainer 的 approve 即可合并；争议改动需要两人一致。

当前维护者与代码所有者见 [`.github/CODEOWNERS`](.github/CODEOWNERS)；安全报告渠道见
[`SECURITY.md`](SECURITY.md)。

## 3. 变更分级

| 级别 | 例子 | 要求 |
|---|---|---|
| P0 文档/测试 | 注释、错别字、新增 witness | 1 review |
| P1 实现 | parser/checker 修 bug，不改语言 | 1 review + 全绿门禁 |
| P2 规则 | 新增/修改 `.lspec` | RFC issue + 重跑证据 + 账本 `--write` |
| P3 语言语义 | 新原语、新诊断码、domain 语义 | RFC issue + 两人一致 + 版本号 minor |
| P4 证据模型 | 改动 `claims.py` 的身份判定、哈希口径 | RFC issue + 两人一致 + 重新证明全部 43 条 |

P2 以上的 PR 必须在描述里贴出：

```text
LLVM commit:
受影响 claim:
重跑命令与结果（claims=43 proven=? failed=?）
```

## 4. RFC 流程

1. 开 issue，标题 `RFC: <一句话>`；
2. 写清：要解决什么、不解决什么、会影响哪些 claim、如何证伪；
3. 至少 7 天评论期（P4 为 14 天）；
4. 合并时把结论写进 `docs/decisions.md`，带日期与 LLVM commit。

## 5. 版本与兼容

- 包版本（`pyproject.toml`）与 LegiSpec 语言版本（`docs/legispec-v1.0.md`）**分开**；
- `1.0.x` 是维护分支：只接受 bug fix、证据重跑、文档与 CI 修复；不新增规则、claim 或 DSL 原语；
- `1.x` 内保持 `.lspec` 语法、诊断码、CLI 与证据 schema 的向后兼容；
- 破坏性 DSL 变化进入 `2.0`，必须在 `docs/decisions.md` 记录迁移方式并新增 claim，不得改写 1.0 证明；
- release cadence：patch 按需发布；minor 必须先走 RFC 并完成全量证据刷新；
- 规则文件的兼容性由 `rules/` 内的语法版本声明保证，不靠“记得改”。

## 6. 停止规则（范围冻结）

`docs/CLOSURE.md` 已声明项目 **CLOSED**：新增能力必须走 §8 playbook，
不得修改既有证明。这条规则优先于“再多做一点会更好”的直觉 ——
没有停止规则的工具链会无限膨胀，而膨胀本身不产生证据。

## 7. 移除维护者 / 项目停摆

若维护者连续 90 天不响应 issue 与 PR，任何 contributor 可 fork 并沿用本许可证继续，
本项目不主张名称与商标；`claims/` 与 `docs/decisions.md` 可原样带走。
