# Conformance Declaration — LegiSpec

> 本文件冻结“与参考实现一致”的**定义**：参考版本、观测函数、定义域、等价关系、排除项。
> 没有这份文件，“与 C++ 一致”只是一句无法复查的话。

---

## 0. 当前状态

| 项 | 状态 |
|---|---|
| Spec 冻结 | v1.0（`docs/legispec-v1.0.md`，含 `T011` v1 修正） |
| 域定义 | `spec/domain.md`（28 LLTs） |
| Witness 语料 | 205 条（114 正例 / 89 负例 / 2 unreachable） |
| Python 解释器对 witness | **0 失败**（2026-10-19） |
| Phase 1 AST + diagnostics | ✅ complete（统一 code + span） |
| Phase 2 domain + coverage | ✅ 28 LLTs / 12 imms / 11 MemDesc / 47 Q0 |
| Phase 3 静态检查器 | ✅ `check --strict`: 0 ERROR / 46 WARN / 0 UNKNOWN |
| Phase 4 fixpoint 解释器 | ✅ 146,974 条 checker query 全部终止，max steps = 4 |
| Phase 5 C++ oracle | ✅ 手写规则 oracle + 生成规则 oracle |
| Phase 5 差分 | ✅ 243,714 条 query × 2，**0 分歧**，0 oracle error |
| Phase 6 codegen | ✅ `toycpu_rules_gen.{h,cpp}` + manifest；能力清单检查 |
| Phase 7 门禁 | ✅ `scripts/check-legispec.ps1`（含重建 oracle 36.9 s） |
| 规则覆盖 | 87 / 89；未命中的 2 条是已登记的死规则（`bits < 1`） |
| 结论 | **声明 conformance（双 oracle，范围 = D ∪ R，排除项见 §6）** |

`D ∪ R` 的输入 SHA-256：

```text
8a8c9c9f26b6b4de954201bed878bb8b89b562549f070e1b4719cd7ca02b6cb2
```

---

## 1. 参考实现

```text
repository  = F:\llvm\llvm-project
commit      = d855d9e48067026317998cbff734004f328f833d
subject     = toycpu: v1.1 M1 encode ecall/mret and CSR instructions
build       = F:\llvm\build-toycpu
assertions  = ON
libs        = LLVMToyCPUCodeGen.lib (+ LLVMExports.cmake imported targets)

compiler    = F:\llvm\install\bin\clang++.exe (clang 24.0.0git)
linker      = F:\llvm\install\bin\lld-link.exe
visual st.  = D:\VStudio\2022\Enterprise
windows sdk = 10.0.22621.0 (x64 import libraries)
```

观察入口：

```cpp
const LegalizerInfo *LI = ST->getLegalizerInfo();
LegalizeActionStep S = LI->getAction(LegalityQuery(Opcode, Types, MemDescs, Imms));
```

参考位置：`llvm/include/llvm/CodeGen/GlobalISel/LegalizerInfo.h:1381`，
`llvm/lib/Target/ToyCPU/ToyCPUSubtarget.h:58`。

oracle 只做参数适配和观测，不复制任何 legality policy：

```text
toycpu\tools\oracle\main.cpp
toycpu\tools\oracle\CMakeLists.txt
toycpu\tools\oracle\build.ps1
```

---

## 2. 观测函数

```text
observe(step) = (Action, TypeIdx, NewType)
```

**不包含** `rule_index`：

- C++ 的 `LegalizeActionStep` 只有 `Action / TypeIdx / NewType`（`LegalizerInfo.h:144-160`）；
- `rule_index` 是 Python 侧的 trace 信息，用于定位与调试，不参与等价判定。

`NewType` 的比较使用 `LLT::operator==`（标量比位宽、指针比地址空间与位宽、向量比 lane 数与元素）。
JSONL 中的 `NewType` 统一写成 DSL spelling：`s32`、`p0`、`p0:64`、`v4s32`。

---

## 3. 定义域

```text
D  = 抽象类型域 × 立即数域 × 内存描述域 × arity（见 spec/domain.md）
Q0 = W ∪ B ∪ I
R  = closure(Q0) under legalize
```

实际规模：

| 集合 | 规模 | 说明 |
|---|---:|---|
| `D` | 243,714 | 80 个 opcode（含 ruleset 内全部 alias） |
| checker 的 D | 146,974 | 每个 ruleset 只枚举代表 opcode |
| `Q0` | 47 | 每个 ruleset 一个代表 query |
| `R` | 47 | 当前 Q0 全部直接终止，闭包不扩张 |
| `D ∪ R` | 243,714 | `R ⊆ D` |

所有 query 以 JSONL 输出，字段顺序固定为 `id, opcode, types, mems, imms`；
报告包含输入 SHA-256，runner 使用 `tempfile` 生成输入，不把大文件写入仓库。

---

## 4. 等价关系

```text
Conformant(spec, cxx) ≡
    ∀ q ∈ (D ∪ R) \ U:
        observe(Python(q)) == observe(Cxx(q))
```

其中 `U` 是显式登记的 `UNKNOWN` 集合（见 §6），必须逐条列出理由。
本次差分 `U = ∅`。

等价是**逐点**的，不是“统计上一致”；不接受“通过率 99%”式结论。

---

## 5. 轨迹等价（由单步推出）

> 若两条实现对 `R` 上每个 query 的单步结果相同，且都终止，则整条 legalize 轨迹相同（对步数归纳）。

因此 oracle 只需对拍单步，不需要驱动完整 pass。终止性由 Phase 4 的 ranking 检查独立负责。

---

## 6. 排除项（Explicit exclusions）

以下内容**不在** conformance 声明范围内：

| ID | 排除项 | 归属 |
|---|---|---|
| E1 | `LegalizerHelper` 的 MIR 重写语义 | helper 契约 |
| E2 | `legalizeCustom` 的具体重写 | 后端实现 |
| E3 | scalable vector / `vscale` | 真实 LLT 全域 |
| E4 | `G_INTRINSIC*` 的完整签名空间 | unspecified（D-0003） |
| E5 | 内存序与原子语义 | ISA |
| E6 | 生成机器码的硬件正确性 | lit / 模拟器 |
| E7 | `v1`（LLVM LLT 不可表示；DSL 的 `T011` 与 oracle 解析均拒绝） | DSL 值域 |

在排除项内，任何“检查通过”都**不得**被表述为“正确”。

---

## 7. 复现步骤

```powershell
# 1. 回归（101 tests）
python -m unittest discover -s F:\llvm-study\toycpu\tools\legispec\tests -v

# 2. 静态检查与 witness
python -m legispec `
  --rules F:\llvm-study\toycpu\tools\legispec\rules\toycpu.lspec check --strict
python -m legispec `
  --rules F:\llvm-study\toycpu\tools\legispec\rules\toycpu.lspec witness `
  F:\llvm-study\toycpu\tools\legispec\witness\toycpu.witness

# 3. 构建 C++ oracle（自动选择 VS2022 / 可用 Windows SDK）
powershell -NoProfile -ExecutionPolicy Bypass -File `
  F:\llvm-study\toycpu\tools\oracle\build.ps1

# 4. 全量差分：手写规则 oracle
python F:\llvm-study\toycpu\tools\legispec\conformance.py `
  --oracle F:\llvm-study\toycpu\out\oracle\legispec-oracle.exe `
  --domain D --closure R `
  --out F:\llvm-study\toycpu\out\conformance-report.json

# 5. 全量差分：由 LegiSpec codegen 生成的规则 oracle
python F:\llvm-study\toycpu\tools\legispec\conformance.py `
  --oracle F:\llvm-study\toycpu\out\oracle\legispec-oracle-gen.exe `
  --domain D --closure R `
  --out F:\llvm-study\toycpu\out\conformance-gen-report.json
```

本次结果：

```text
queries=243714 divergences=0 rules=87/89
closure_outside_d=0
ok=true
# 手写 oracle 与生成规则 oracle 各自 0 分歧，且输入 SHA-256 相同
```

未命中的 2 条规则：

| ruleset | index | 规则 | 原因 |
|---|---:|---|---|
| `G_UADDO` | 3 | `widen t1 to s1 when scalar(t1) and bits(t1) < 1` | LLT 最小标量是 `s1`，下界规则不可达 |
| `G_CONSTANT` | 1 | `widen t0 to s1 when scalar(t0) and bits(t0) < 1` | 同上 |

它们是 C++ `clampScalar` 展开出的保护分支，已由 `witness` 登记为 `unreachable`；
不属于 oracle 分歧。

---

## 8. 分歧政策

任何分歧必须写入 `spec/decisions.md`，并归入：

```text
spec-wrong    -> 升 spec 版本
cxx-wrong     -> 记录为上游缺陷（可 benign）
unspecified   -> 显式登记，纳入 U 集合
```

禁止“跳过”“不重要”“看起来没问题”。

---

## 9. 版本

| 日期 | 版本 | 变更 |
|---|---|---|
| 2026-10-19 | v0.1 | 初始冻结：spec/domain/witness 完成；oracle 尚未构建 |
| 2026-10-19 | v0.2 | Phase 3/4/5 完成；C++ oracle 构建；381,944 条差分 0 分歧；声明 conformance |
| 2026-10-19 | v0.3 | Phase 6/7 完成；`v1` 按 `T011` 移出 D；28 LLTs；手写/生成双 oracle 各 243,714 条 0 分歧 |