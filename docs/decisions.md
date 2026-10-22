# 裁决记录（Divergence Log）

> 规则：分歧只允许三类 —— `spec-wrong` / `cxx-wrong` / `unspecified`。没有第四类。
> 每条记录必须能复查：观测、期望、裁决、处置、受影响文件。

---

## 索引

| ID | 日期 | 类型 | 一句话 |
|---|---|---|---|
| D-0001 | 2026-10-19 | spec-wrong | `types` 是去重后的 generic type index 数组，不是操作数数组 |
| D-0002 | 2026-10-19 | cxx-wrong（良性） | `G_UADDO` rule 3 与 `G_CONSTANT` rule 1 在 D 上不可达 |
| D-0003 | 2026-10-19 | unspecified | `G_INTRINSIC*` 的 arity 由 intrinsic 签名决定 |
| D-0004 | 2026-10-19 | spec-wrong | `v1` 不是可表示的 LLT，从 D 中移除 |
| D-0005 | 2026-10-21 | unspecified | 拆包：证据外置，身份用内容哈希，路径只作同名判定 |

---

## D-0001 `types` 的索引语义

```text
kind      = spec-wrong
observed  = (G_UNMERGE_VALUES, [s32, s32, s64]) -> Unsupported
expected  = Legal
context   = witness/unmerge_s32_s64_legal 与 unmerge_s32_s128_legal
```

**原因**：我把 `types` 当成了操作数数组。实际上：

- `GenericOpcodes.td` 用 `typeN` / `ptypeN` 编号；
- `LegalizerInfo.cpp:347-369` 用 `SeenTypes` 去重，每个 type index 只记录一次；
- `G_UNMERGE_VALUES` 的 `(outs type0, variable_ops), (ins type1)` 对应 `types = [t0, t1]`。

**处置**：

1. `domain.md` §4.1 明确该语义；
2. 按 `GenericOpcodes.td` 机械校准全部 witness 的 arity（74 条 query 被裁剪）；
3. `G_UNMERGE_VALUES` 的两条证人手工修正为 `[s32, s64]` / `[s32, s128]`（src 在末位，不能从前截断）。

**验证**：204 条证人重跑，0 失败。

---

## D-0002 两条不可达规则

```text
kind      = cxx-wrong (benign dead rule)
observed  = G_UADDO rule 3:  predicate = scalar(t1) and bits(t1) < 1
            G_CONSTANT rule 1: predicate = scalar(t0) and bits(t0) < 1
expected  = 无合法 LLT 满足 bits < 1
context   = ToyCPULegalizerInfo.cpp:96-100, 107-109
```

**原因**：C++ 的 `clampScalar(0, s1, s32)` 与 `clampScalar(1, s1, s1)` 会生成一条“下限为 s1”的 widen 规则，其守卫是 `bits < 1`。LLVM 没有 `s0`，因此该规则永远不可能命中。

**影响**：行为上无害（死规则），但会污染规则计数与可达性报告。

**处置**：

1. 在 `witness/toycpu.witness` 中以 `unreachable = N` 显式记录；
2. Phase 3 的 `R001` 检查必须对这两条给出 `unreachable` 但已确认的结论，而不是 ERROR；
3. 不修改 C++（参考实现保持原样），只记录。

---

## D-0003 `G_INTRINSIC*` 的 arity

```text
kind      = unspecified
observed  = GenericOpcodes.td 中 G_INTRINSIC 没有固定 type index
expected  = 无法静态确定
context   = G_INTRINSIC, G_INTRINSIC_W_SIDE_EFFECTS
```

**处置**：

- 规则只检查 `t0`，因此 D 中取 `0..3` 个类型槽的样本；
- 报告中标 `UNKNOWN`，不纳入“穷尽”结论；
- 若后续需要完整覆盖，必须引入 intrinsic 签名表，单独立项。

---

## D-0004 `v1` 不是可表示的 LLT

```text
kind      = spec-wrong
observed  = v1s1/v1s8/v1s16/v1s32/v1s64 in D
expected  = LLT::vector(1, elem) 不存在
context   = llvm/include/llvm/CodeGenTypes/LowLevelType.h:123-130
            ElementCount::getFixed(1).isScalar() == true
```

**原因**：LLVM 的 `ElementCount::getFixed(1)` 被判定为 scalar，而
`LLT::vector` 的第一条断言是 `!EC.isScalar() && "invalid number of vector elements"`。
因此 `v1sN` 在 LLVM 的 LLT 语义中不是“1 元素向量”，而是标量；把它放进 D
会让启用断言的 oracle 在构造 query 时崩溃。

**处置**：

1. `model.LLT.vector` 拒绝 `lanes == 1`，稳定诊断码 `T011`；
2. 从 `rules/toycpu.lspec` 的 `domain.types` 删除全部 `v1*`；
3. `docs/legispec-v1.0.md` §2.7 写明结论约束；
4. D 的规模从 33 个 LLT 降为 28 个，conformance query 从 381,944 降为 243,714；
5. 保留 `v2/v4/v8`，它们仍是合法 LLT；
6. C++ oracle 的 JSONL 解析在构造 LLT 前拒绝 `lanes < 2`，返回稳定 error，而不是触发断言。

**验证**：101 tests；`check --strict` 0 ERROR；witness 205 条 0 失败；
手写 oracle 与生成规则 oracle 各 243,714 条 query、0 分歧、0 oracle error。

---

## D-0005 拆包为独立仓库：证据外置、身份用哈希

kind      = unspecified（工程决策，不是 spec 与 C++ 的分歧）
observed  = 报告里写着产出机的绝对路径；oracle 是 20–32 MB 的 Windows 可执行文件
expected  = 仓库可以独立 clone、独立构建、独立复核，但不吞二进制
context   = out/conformance-report.json:inputs.rules / reference.oracle
cause     = 报告与 oracle 都是构建产物；把它们入库会让仓库变成二进制仓库，
            且证据会随平台与 LLVM 构建目录过期
action    = 1. out/ 不入库，claims.json 钉 rules_sha256 / oracle_sha256；
            2. claims.audit 的路径判定降级为"同名文件"，身份由内容哈希承担；
            3. scripts/collect_evidence.py 按账本取回并校验；
            4. scripts/reproduce_claims.py 按每条 claim 的 config 重跑 oracle；
            5. 缺证据时单元测试 skip，claims verify 明确 FAIL，不伪装通过。
verify    = 43/43 PROVEN；aarch64-scalar 与 riscv32-scalar 就地重跑 0 分歧；
            rules/riscv-conditional-gconstant.lspec 的 EOL 规范化触发 STALE 后，
            重跑 2 条 claim 并 --write，重新 43/43。

---

## 记录模板

```text
## D-XXXX <title>

kind      = spec-wrong | cxx-wrong | unspecified
observed  = <query> -> <actual>
expected  = <expected>
context   = <file:line / witness name>
cause     = <analysis>
action    = <what changed>
verify    = <how re-checked>
```