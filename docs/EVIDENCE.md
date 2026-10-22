# 证据：报告与 oracle

## Evidence verification in CI

Normal CI runs the strict rules sweep (`python scripts/check_all_rules.py`) and structural closure (`legispec-closure check --skip-evidence`). Structural mode still fails on a ledger entry marked `ok=false` or with non-zero divergences, but it cannot prove the artifacts. Full evidence verification is manual: trigger `workflow_dispatch`, set `evidence_dir` to a directory on a self-hosted runner labelled by `evidence_runner`, and the workflow uploads that directory as the `legalizer-evidence` artifact. A hosted `windows-latest` job downloads the artifact, copies it with `scripts/collect_evidence.py`, then runs `legispec-claims verify --verify-corpus` (re-enumerates D union R and compares each report's corpus hash) and `legispec-closure check`.

`claims/claims.json` 里 43 条断言，每一条都钉住三样东西：

```text
rules/<name>.lspec          内容哈希（rules_sha256）—— 入库
out/<report>.json          内容哈希（report 由 rules+oracle 产出）—— 不入库
out/<oracle>.exe           内容哈希（oracle_sha256）—— 不入库
```

规则入库，报告与 oracle 外置。原因是它们都是**构建产物**：

- oracle 是 Windows x64 可执行文件，每个约 20–32 MB；
- 报告里写着产它的绝对路径、临时 jsonl 路径、本机 LLVM 构建目录；
- 把它们提交进 git，会让仓库变成二进制仓库，且证据随平台过期。

## 1. 路径

证据目录默认是仓库根下的 `out/`，可用环境变量改：

```powershell
$env:LEGALIZER_DSL_OUT = 'D:\evidence\legalizer-dsl'
```

同一套变量作用于 `claims` / `conformance` / `closure` / `codegen`：

| 变量 | 作用 |
|---|---|
| `LEGALIZER_DSL_OUT` | 报告与 oracle 所在目录 |
| `LEGALIZER_DSL_LLVM_SRC` | LLVM 源码树（`constructs check` 读 `LegalizerInfo.h`） |
| `LEGALIZER_DSL_LLVM_BUILD` | LLVM 构建目录（写进报告元数据、claims 期望值） |
| `LEGALIZER_DSL_TOYCPU_BUILD` | ToyCPU 构建目录（toycpu 断言的期望值） |
| `LEGALIZER_DSL_LLC` | `llc` 路径（MIR 形状审计） |

## 2. 三种拿到证据的方式

### A. 取回既有证据（最快）

```powershell
python scripts/collect_evidence.py --from <含 report 与 oracle 的 out 目录>
python -m legalizer_dsl.claims verify
```

脚本只复制 `claims.json` 点名的文件，并且在复制前逐份校验 `oracle_sha256`；
任何一份哈希不符都会拒绝并列出问题，不会半信半疑地放行。

### B. 就地重跑（有 oracle，无 LLVM 构建）

```powershell
python scripts/reproduce_claims.py --only aarch64-scalar
python scripts/reproduce_claims.py                       # 全部 43 条
python -m legalizer_dsl.claims verify                    # 断言仍然成立
```

每条断言用自己的 `config`（`is64bit` / `sXLen` / `sDoubleXLen` …）作 `--define`，
用 `rules/` 当前内容重新枚举 `D ∪ R`、重跑 oracle 差分，并原地重写报告。
**这一步会重新产生分歧计数** —— 它是证明，不是复述账本。

### C. 从零重建 oracle（换机器、换平台）

1. 构建 LLVM（需要 `llc` 与 GlobalISel）：

   ```powershell
   cmake -S F:/llvm/llvm-project/llvm -B F:/llvm/build -G Ninja `
     -DCMAKE_BUILD_TYPE=Release -DLLVM_ENABLE_ASSERTIONS=ON `
     -DLLVM_TARGETS_TO_BUILD="AArch64;RISCV;X86"
   ninja -C F:/llvm/build llc
   ```

2. 构建 oracle：oracle 是“把 rules 交给真实的 `LegalizerInfo`”的最小观察适配器，
   不含任何合法性策略。它的契约是逐行 JSONL：

   ```text
   输入  {"id":0,"opcode":"G_ADD","types":["s32"],"mems":[],"imms":[]}
   输出  {"id":0,"action":"Legal","type_idx":0,"new_type":null}
   ```

   适配器必须链接**某个 target 的 LegalizerInfo**（例如 `LLVMToyCPUCodeGen`），
   所以它注定跟随 target 后端，不在本仓库内：

   ```powershell
   legispec --rules rules/aarch64-vector.lspec codegen --out-dir out/codegen
   # out/codegen/*.cpp + <target>/tools/oracle/main.cpp + 该 target 的 CodeGen 库
   #   -> out/oracle-aarch64/legalizer-oracle-aarch64.exe
   ```

   本仓库负责规则、证明与账本；target 后端与 oracle 构建属于外部依赖，
   这也是证据按 `(target, 配置)` 分片命名的原因。

3. 用新 oracle 重跑：

   ```powershell
   python scripts/reproduce_claims.py --llvm-build <你的构建目录>
   python -m legalizer_dsl.claims verify --llvm-build <build-dir> --write --allow-report-change
   ```

   `--write` 会把新的 `oracle_sha256` / `oracle_size` / `rules_sha256` 写回账本。
   **换 oracle 必须走这一步**：账本不会自动接受新二进制。

## 3. 没有证据时会发生什么

不会静默变绿，也不会伪装成“通过”：

```text
$ python -m legalizer_dsl.claims verify
toycpu   FAIL   ! report missing: out/conformance-report.json
claims=43 proven=0 failed=43
```

单元测试里依赖证据的用例标记为 `skip`，并给出获取路径的提示；
`python -m unittest discover -s tests` 在干净 clone 上仍然可跑、仍然有意义。

## 4. 证据的可移植性

报告里的路径是产出时的绝对路径（`F:\llvm-study\toycpu\out\...`）。
`claims.py` 因此**不用路径做身份**：路径只用来判断“指向的是不是同名文件”，
身份由 `rules_sha256` / `oracle_sha256` 决定。这就是同一份证据可以换机器复核的原因。

## 5. 证据目录布局

```text
out/
  conformance-report.json                       ToyCPU 全量
  aarch64-vector-conformance-report.json        AArch64 向量
  riscv64-mzb-scalar-conformance-report.json    RISC-V M/Zbb
  oracle/legalizer-oracle.exe                   ToyCPU oracle
  oracle-aarch64/legalizer-oracle-aarch64.exe
  oracle-riscv/legalizer-oracle-riscv32.exe
  ...
```

文件名必须与 `claims.json` 的 `report` / `oracle` 字段逐字一致 —— 账本是唯一清单。

## 6. Provenance and pin semantics

Every conformance report now records:

```text
evaluator_sha256   modules the conformance runner used to enumerate D and
                   interpret the rules (parser, model, interpreter, checker
                   analysis, ...)
codegen_sha256     codegen.py + capability checker; required only for claims
                   whose oracle was built from generated C++
```

`claims verify` checks the report-local fingerprint against the current tree.
A report produced by an older evaluator is a FAIL, and `--write` refuses to
bless it. After regenerating the reports, refresh the pins explicitly:

```powershell
python -m legalizer_dsl.claims verify --write --allow-evaluator-change
```

The canonical report hash excludes checkout-dependent metadata (`rules`,
`oracle`, `llvm_src`, `llvm_build` paths and the temporary JSONL name). Two
identical runs in different temporary directories therefore produce the same
canonical hash, and `reproduce_claims.py` can finish with a plain
`claims verify` (no `--write`). `report_file_sha256` is still recorded for
copy verification by `collect_evidence.py`, but it is informational for
staleness.

The ledger now requires evaluator, rules, report, corpus and oracle pins.
Missing pins fail closed; use `--allow-missing-pins` only for a one-time
migration. Removing a claim or dropping a witness pin is also a FAIL. The
`llvm_build` path is a real pin: on a new machine pass the same override to
both commands, or refresh it with `--write`.

## 7. Reachability declarations

`declared_unreachable` and `declared_shadowed` are checked against the
checker's predicate-level analysis, not against a "never won" count. A claim
that declares an unreachable rule must attach a witness; the override is keyed
by `(rules file, config)`, so a hand-written claim and its generated twin use
the same witness. Shadowed rules are recorded as `declared_shadowed` and do not
need a witness.
