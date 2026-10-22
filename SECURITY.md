# 安全策略

本项目是开发期工具，不处理不可信输入的生产流量，但有两类问题按安全事件处理。

## 1. 报告渠道

**不要开公开 issue。** 使用 GitHub 的私密漏洞报告入口：

```text
https://github.com/nodecoda/legispec/security/advisories/new
```

仓库设置中的 *Private vulnerability reporting* 必须开启。若该入口暂不可用，请开一个
不含漏洞细节的 issue，请维护者提供私密渠道。首次响应目标 7 天内。

## 2. 关注的两类问题

### 证据链绕过

比“代码执行”更严重的是“证明失真”：

- 让 `claims verify` 在 report/oracle 缺失或被替换时仍然返回 PROVEN；
- 让 `closure check` 接受与产物不符的 pinned 数字；
- 让 `--strict` 在缺 capability provider 时静默放行 `custom`；
- 让 `reproduce_claims.py` 用错误的 `--define` 复现出“零分歧”。

### 生成代码注入

`codegen` 会把规则里的标识符写进 C++。命名空间/标识符校验（`not a C++ identifier`）
是安全边界，任何绕过都按漏洞处理。

## 3. 不在范围内

- 需要本机已运行恶意代码才能触发的问题；
- LLVM 自身的漏洞（请报给 LLVM 项目）；
- 仅影响 `docs/` 渲染的问题。
