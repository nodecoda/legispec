# 贡献指南

本项目按 LLVM 的方式治理：同一份许可证、同样的“证据优先”文化、同样的
“改动必须自带测试与复核”的要求。

## 1. 许可

**提交即表示你同意以 [Apache License v2.0](LICENSE.TXT) 授权你的贡献**，
无需签署额外的 CLA。请用 `git commit -s` 添加 `Signed-off-by` 行：

```text
Signed-off-by: Your Name <you@example.com>
```

新增源文件必须带 SPDX 头：

```python
# ===----------------------------------------------------------------------===##
#
# Part of the LegiSpec project, under the Apache License v2.0.
# See LICENSE.TXT for license information.
# SPDX-License-Identifier: Apache-2.0
#
# ===----------------------------------------------------------------------===##
```

## 2. 三条硬规则

### 规则一：改规则，必须重跑证据

任何 `rules/*.lspec` 的内容改动都会让既有断言的 `rules_sha256` 失效。PR 必须包含：

```powershell
python scripts/reproduce_claims.py --only <受影响的 claim>
python -m legispec.claims verify --write
python -m legispec.closure check
```

只改 `claims.json` 里的数字、不重跑证据的 PR **一律拒绝**。
账本不是宣言，是审计结果。

### 规则二：不许为了变绿而改语义

如果 oracle 与 DSL 不一致：

1. 先判定是 **spec-wrong**（我们写错）还是 **cxx-wrong**（LLVM 行为与文档不符）；
2. 记进 `docs/decisions.md`，写清证据（文件:行、MIR、LLVM commit）；
3. 再改代码。

禁止：

- 把失败的查询从 domain 里删掉；
- 把 `divergence` 改成 `unspecified` 了事；
- 放宽 `--strict` 的 capability 检查来绕过缺 provider。

### 规则三：新原语要有分类

`constructs check` 要求 `LegalizerInfo.h` 里每个 public builder 方法都被归类为
`core` / `guard` / `macro` / `none` / `partial`。加新原语时同步更新
`claims/construct-coverage.json`，否则门禁失败。

## 3. 提交前自检

```powershell
python -m unittest discover -s tests          # 219 tests
python -m legispec.claims verify          # 需要 out/ 证据
python -m legispec.constructs check       # 需要 LEGISPEC_LLVM_SRC
python -m legispec.closure check          # 文档 pinned 数字 vs 产物
```

四条全绿才算可提交。缺证据时 `claims verify` 会明确失败，这是设计而非故障，
见 [`docs/EVIDENCE.md`](docs/EVIDENCE.md)。

## 4. 代码风格

- Python 3.10+，**只用标准库**，不引入运行时依赖；
- `src/legispec/` 内不做 I/O 副作用，CLI 层负责路径；
- 文件编码 UTF-8 无 BOM；`.py/.lspec/.json` 用 LF，`.md/.ps1` 用 CRLF（见 `.gitattributes`）；
- 诊断码 `P/T/K/C` 分段使用，新增诊断必须在 `docs/grammar.ebnf` 或 `diagnostics.py` 注册；
- 注释写“为什么”，不写“是什么”；不写与代码无关的规划性注释。

## 5. 提交信息

```text
<area>: <imperative summary>

<body: what changed, why, which claims were re-proven>

Signed-off-by: ...
```

`area` ∈ `parser` `checker` `codegen` `conformance` `claims` `rules` `docs` `ci`。

## 6. Review 标准

Reviewer 只看三件事：

1. 命题边界有没有被悄悄放宽（domain 缩小、excludes 变多、query 数变少）；
2. 证据能不能复现（命令、哈希、LLVM commit 是否一致）；
3. 新代码有没有把“约定”藏在注释里，而不是放进检查器。
