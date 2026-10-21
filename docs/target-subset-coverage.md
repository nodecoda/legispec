# 为什么 AArch64 / RISC-V / X86 的 LDSL 文件和 ToyCPU 差不多大？

Date: 2026-10-20
Status: 说明文档（回答"处理得不完整？"）

## 0. 直接回答

**是，三个真实 target 的处理都是不完整的，而且是刻意不完整的。**

它们是 Legalizer 策略层（`LegalizeRuleSet`）的**有限子集实验**，不是完整后端：

| 实验 | 实际范围 |
|---|---|
| ToyCPU | **完整** target 策略（ToyCPU 本身就是小 ISA） |
| X86 | 整数 + 指针形状；无向量、FP、内存描述、原子、custom |
| AArch64 | scalar + pointer + 原子配置族（generic / +outline-atomics / +lse）+ 固定向量切片；无 scalable vector/SVE/FP 算术/内存描述；G_FCMP 浮点比较已覆盖；G_CONCAT_VECTORS custom/bitcast 已覆盖；CSSC scalar 已覆盖 |
| RISC-V | RV64 与 RV32 两套 scalar + pointer，外加 RV64/RV32 的 `+m,+zmmul,+zbb` 组合；无 RV32+D、`+zbc`/`+zbkc`/`+p`/`+v`、FP/内存/原子/调用 |

**文件大小接近 ToyCPU，不代表覆盖接近 ToyCPU。** 真正的差别在"域"和"特性矩阵"，而 `.ldsl` 文件只是策略表，不是整个后端。

## 1. 硬数据

### 1.1 C++ 源文件 vs LDSL 模型

| Target | C++ 行数 | C++ 字节 | builder 调用 | C++ 中 `G_*` opcode | DSL rulesets | DSL opcodes | 展开后规则 | 对拍查询 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| ToyCPU | 966 | 41,158 | 49 | 81（其中 1 个只在注释） | 47 | 80 | 89 | 243,714 |
| X86 | 1,060 | 40,546 | 58 | 122 | 18 | 31 | 69 | 7,591 |
| AArch64 | 2,874 | 111,579 | 114 | 249 | 33（22+11） | 59 | 108（91+17） | 9,085（8,161+924） |
| AArch64 +cssc | 2,874（同一文件） | 111,579 | 114 | 249 | 22 | 48 | 95 | 8,161 |
| AArch64 atomic generic/lse | 2,874（同一文件） | 111,579 | 114 | 249 | 4 | 12 | 11 | 612 |
| AArch64 atomic outline | 2,874（同一文件） | 111,579 | 114 | 249 | 4 | 12 | 10 | 612 |
| AArch64 fixed-vector | 2,874（同一文件） | 111,579 | 114 | 249 | 3 | 11 | 11 | 176 |
| AArch64 G_ADD-family vector | 2,874（同一文件） | 111,579 | 114 | 249 | 1 | 5 | 15 | 120 |
| AArch64 G_MUL vector | 2,874（同一文件） | 111,579 | 114 | 249 | 1 | 1 | 15 | 24 |
| AArch64 G_SHL family vector | 2,874（同一文件） | 111,579 | 114 | 249 | 1 | 3 | 21 | 270 |
| AArch64 G_ICMP result/operand | 2,874（同一文件） | 111,579 | 114 | 249 | 1 | 1 | 23 | 173 |
| AArch64 G_FCMP result/operand | 2,874（同一文件） | 111,579 | 114 | 249 | 1 | 1 | 14 | 185 |
| AArch64 G_CONCAT_VECTORS custom/bitcast | 2,874（同一文件） | 111,579 | 114 | 249 | 1 | 1 | 3 | 28 |
| RISC-V | 1,821 | 73,887 | 91 | 208 | 49（38+11） | 83 | 135（103+32） | 8,631（7,861+770） |
| RISC-V +m,+zmmul,+zbb | 1,821（同一文件） | 73,887 | 91 | 208 | 39 | 73 | 118 | 7,861 |
| RISC-V RV32 | 1,821（同一文件） | 73,887 | 91 | 208 | 38 | 73 | 100 | 7,861 |
| RISC-V RV32 +m,+zmmul,+zbb | 1,821（同一文件） | 73,887 | 91 | 208 | 39 | 73 | 115 | 7,861 |
| RISC-V RV32 pointer | 1,821（同一文件） | 73,887 | 91 | 208 | 11 | 14 | 31 | 770 |

> 查询数是各子集独立对拍的结果（AArch64 scalar/+cssc/pointer/atomic 配置族、RISC-V scalar/pointer、RISC-V mzb），不是一次联合运行。+cssc 与 generic scalar 共用同一查询集，+lse 与 generic atomic 共用同一规则文件，因此都不重复计入总量。

### 1.2 文件大小

| 文件 | 字节 |
|---|---:|
| `rules/toycpu.ldsl` | 9,868 |
| `rules/x86-scalar.ldsl` | 5,661 |
| `rules/aarch64-scalar.ldsl` + `aarch64-pointer.ldsl` | 8,756 + 3,502 = 12,258 |
| `rules/aarch64-cssc-scalar.ldsl` | 8,888 |
| `rules/aarch64-atomic.ldsl` | 3,136 |
| `rules/aarch64-atomic-outline.ldsl` | 2,868 |
| `rules/aarch64-vector.ldsl` | 2,547 |
| `rules/aarch64-vector-arith.ldsl` | 3,227 |
| `rules/aarch64-vector-mul.ldsl` | 3,188 |
| `rules/aarch64-vector-shift.ldsl` | 5,188 |
| `rules/aarch64-icmp.ldsl` | 4,805 |
| `rules/aarch64-fcmp.ldsl` | 7,250 |
| `rules/aarch64-concat-vectors.ldsl` | 4,451 |
| `rules/riscv64-scalar.ldsl` + `riscv64-pointer.ldsl` | 9,852 + 4,205 = 14,057 |
| `rules/riscv64-mzb-scalar.ldsl` | 13,233 |
| `rules/riscv32-scalar.ldsl` | 11,171 |
| `rules/riscv32-mzb-scalar.ldsl` | 11,874 |
| `rules/riscv32-pointer.ldsl` | 5,238 |

AArch64 的 generic scalar + pointer 合计 12,258 字节，仍与 ToyCPU 同量级；+cssc 变体再加 8,888 字节，但覆盖的是同一批 48 个 scalar opcode 的另一个配置点；generic atomic 子集再加 3,136 字节，覆盖 12 个原子 opcode；+outline-atomics 变体再加 2,868 字节，覆盖同一批 opcode 的另一个策略点。RISC-V 的 generic 配置也只有 1.4 倍。特性变体 mzb 把 scalar 文件推到 13,233 字节，但它覆盖的仍是同一批 73 个 scalar opcode。它们的 opcode 覆盖率分别只有：

```text
X86       31 / 122 = 25.4%
AArch64   59 / 249 = 23.7%
RISC-V    83 / 208 = 39.9%
ToyCPU    80 / 80  = 100%（按 builder 中实际出现的 opcode）
```

## 2. 为什么大小会接近？

### 2.1 ToyCPU 是"完整但很小"

ToyCPU 的 Legalizer 只有 966 行、49 个 builder、89 条展开规则。它没有特性矩阵，没有 scalable vector，只有 6 个 vector LLT 构造。**它的完整策略本来就只有这么大。**

所以"和 ToyCPU 差不多大"并不自动意味着"只做了很少"；对 AArch64/RISC-V 来说，这意味着我们取的是与 ToyCPU 同量级的**骨架**，而不是整个目标。

### 2.2 真实 target 的 C++ 体积主要不是 scalar 规则

AArch64 的 2,874 行里有：

```text
59 个 vector LLT 构造
48 处 nxv*（scalable vector）
9 处 subtarget 特性判断
SVE / FP16 / 内存描述 / 未建模的原子配置 / 大量 FP opcode（CSSC 与 generic 原子已单独建模）
```

RISC-V 的 1,821 行里有：

```text
39 个 vector LLT 构造
164 处 nxv*
141 处特性判断（hasStdExt* / is64Bit / getELen）
14 处 legalForTypesWithMemDesc
```

这些内容在我们的子集里几乎全部被排除。**如果把它们加进来，文件会成倍增长，而且不是简单加规则：需要给 DSL 增加配置参数和 scalable vector 类型。**

### 2.3 DSL 比 C++ 更密

一条：

```text
ruleset G_UADDE, G_UADDO, G_USUBE, G_USUBO { lower; }
```

覆盖 4 个 opcode；一条 `legal_for {s32, s64}` 覆盖一个类型集合。C++ 里同样的策略要写成 builder 链和类型常量声明。**所以按字节比较会系统性低估 DSL 的规则数。**

展开后的规则数才是可比较量：ToyCPU 89，X86 69，AArch64 119（91+17+11），RISC-V 135（103+32），RISC-V mzb 118。RISC-V 其实已经超过 ToyCPU。

### 2.4 对拍查询数才是"实际踩过的表面"

ToyCPU 的 243,714 条查询远大于其他实验的 7–8k，因为 ToyCPU 的域**完整包含**：

- 标量 + 固定向量；
- 内存描述符与对齐；
- 立即数；
- 所有 opcode 的所有类型组合。

真实 target 的 LDSL 文件只声明了 scalar/pointer 的有限域，所以查询数小。**这是域的选择，不是规则数量的选择。**

## 3. 每个实验到底证明了什么

共同形式：

```text
在声明的有限域 D 上，
Python LDSL 模型与真实 LegalizerInfo::getAction()
对 D ∪ R 中每个查询的 (Action, TypeIdx, NewType) 完全一致。
```

已证明：

| 实验 | 结果 |
|---|---|
| ToyCPU | 243,714 查询，0 分歧，87/89 规则命中（2 条声明为 unreachable） |
| X86 | 7,591 查询，0 分歧，69/69 规则命中 |
| AArch64 scalar | 8,161 查询，0 分歧，91/91 规则命中 |
| AArch64 +cssc | 8,161 查询，0 分歧，95/95 规则命中；与 generic 有 62 个答案不同 |
| AArch64 pointer | 924 查询，0 分歧，17/17 规则命中 |
| AArch64 atomic generic | 612 查询，0 分歧，11/11 规则命中；生成路径同样 11/11 |
| AArch64 atomic +outline-atomics | 612 查询，0 分歧，10/10 规则命中；生成路径同样 10/10 |
| AArch64 atomic +lse | 612 查询，0 分歧，11/11 规则命中；与 generic 差分 0 |
| AArch64 G_ICMP | 173 查询，0 分歧，19/23 规则命中（3 条不可达，1 条被遮蔽） |
| AArch64 G_FCMP | 185 查询，0 分歧，13/14 规则命中（1 条不可达，0 条被遮蔽） |
| AArch64 G_CONCAT_VECTORS | 28 查询，0 分歧，3/3 规则命中（custom body 未证明，2 条 bitcast 后继为域边界） |
| RISC-V scalar | 7,861 查询，0 分歧，103/103 规则命中 |
| RISC-V pointer | 770 查询，0 分歧，32/32 规则命中 |
| RISC-V +m,+zmmul,+zbb | 7,861 查询，0 分歧，117/118 规则命中（1 条不可达） |
| RISC-V RV32 | 7,861 查询，0 分歧，100/100 规则命中 |
| RISC-V RV32 +m,+zmmul,+zbb | 7,861 查询，0 分歧，111/115 规则命中（4 条被规则顺序/域遮蔽） |
| RISC-V RV32 pointer | 770 查询，0 分歧，31/31 规则命中 |
| RISC-V 单源双配置（scalar） | RV64：7,861 查询，0 分歧，108/108；RV32：7,861 查询，0 分歧，100/101（1 条声明为 unreachable；C++ 里该规则在 RV32 上不可满足） |

没有证明：

- 任何未声明域上的行为；
- 向量 / scalable vector；
- 内存描述符；
- 原子配置的组合点（例如 `+outline-atomics` 与 `+lse` 同时打开）；
- FP 算术/转换、调用与 ABI（G_FCMP 已覆盖浮点比较策略）；
- 特性变体（AArch64 SVE/FP16，RISC-V V、Zbc/Zbkc/P，X86 AVX 级别）；
- `LegalizerHelper` 的实际 MIR 改写。

### 3.1 单源双配置：为什么它不是第二个实验

上面 RV64/RV32 两行是**两份冻结文件**。第十二个实验把同一批 scalar 规则写成
**一份源码**，用 `--define is64bit=... sXLen=... sDoubleXLen=...` 选择配置：

```text
rules/riscv-scalar-conditional.ldsl
  +-- is64bit=true,  sXLen=s64, sDoubleXLen=s128  -> 与 riscv64-scalar.ldsl 决策等价
  +-- is64bit=false, sXLen=s32, sDoubleXLen=s64   -> 与 riscv32-scalar.ldsl 决策等价
```

它证明的不是"又覆盖了一个 target"，而是 **guard 的语义**：构造期条件为假时，
规则必须从表里消失，而不是留下一条恒假谓词。`LegalizeRuleSet` 是按序号
first-match-wins 的表，规则数、顺序、覆盖率和死规则分析都读这张表，所以
"恒假谓词"和"没有这条规则"在模型里不等价。

RV32 那 1 条未命中规则是刻意的：C++ 的
`legalIf(all(typeIs(0, sXLen), sizeIs(0, 64), immIs(0, 32)))`
在 RV32 上不可满足，但 C++ 仍然把它建进表里。冻结文件选择删掉它，单源文件保留
它并在 witness 里登记 unreachable——两种写法决策函数相同，区别是后者保留了
"C++ 里有一张死规则"这个事实。

细节见 `notes/legalizer-dsl-config-guard.md`。


## 4. 如果要把它们做"完整"，要付出什么

按代价从低到高：

### L1：同一 C++ 构造函数的其它配置（小）

- RISC-V：RV64/RV32 两套 scalar + pointer，以及 RV64/RV32 的 `+m,+zmmul,+zbb` **均已完成**；剩余 RV32+D 与其它特性组合。
- AArch64：CSSC 已完成（generic vs +cssc）；原子配置族已完成（generic / +outline-atomics / +lse）；剩余 SVE/FP16 与原子组合点；
- X86：不同 AVX 级别。

需要的机制：**配置参数化 + 多份 ruleset 变体 + 每个配置独立对拍**。不需要改 DSL 语义，主要工作量在转写和验证。

### L2：内存描述符与原子组合点（中）

- `G_LOAD`/`G_STORE`/`G_SEXTLOAD`/`G_ZEXTLOAD`；
- 对齐规则、`lowerIfMemSizeNotByteSizePow2`；
- 原子序与 `MemDesc`；
- `+outline-atomics` / `+lse` 同时打开的组合点（单独配置均已完成）。

DSL 已有 `legal_for_mem`，但需要把每个 target 的 MMO 对齐表和内存类型域补全。查询空间会显著变大。

### L3：固定向量（中到大）

**第一片已完成**：`rules/aarch64-vector.ldsl` 覆盖
`G_SMIN/G_SMAX/G_UMIN/G_UMAX` 的 `clampNumElements`、`G_ABDS` 家族的
`legalFor+lower`、以及 `G_CLMUL` 的纯 `legalFor`。DSL 新增
`clamp_num_elements` / `clamp_min_num_elements` / `clamp_max_num_elements`
三个宏，以及逐字对应 C++ 谓词的 `FixedVectorElemCountCmp` 节点；生成器
输出的 `moreElementsIf` / `fewerElementsIf` 已通过与真实 AArch64 oracle
对拍。

**第二片已完成**：`rules/aarch64-vector-arith.ldsl` 覆盖
`G_ADD/G_SUB/G_AND/G_OR/G_XOR` 的共享 ruleset。DSL 新增
`min_scalar_or_elt_if`、`more_elements_to_next_pow2` 两个宏；`scalarizeIf`
用 core rule `fewer tN to element_of(tN) when ...` 表达。新增节点
`NumElementsNotPow2` / `NextPow2Lanes` 分别对应 LLVM 的
`numElementsNotPow2` 与 `moreElementsToNextPow2`。本片同时证明了两条
结构性不可达规则：`v1` 不存在，所以 `clampNumElements` 的 `lanes < 2`
min 半条永不命中。

**第三片已完成**：`rules/aarch64-vector-mul.ldsl` 覆盖 `G_MUL`。它与
`G_ADD` 家族的唯一结构差异是 C++ 里没有 `HasSVE` 的 `legalFor` 分支；其余
15 条规则完全同形，因此本片不需要新增 DSL 原语，只验证了独立 builder
边界下的转写与生成。24 条查询（1 opcode × 24 类型）零分歧，13/15 规则，
两条未命中仍是 `v1` 结构性不可达的 `clampNumElements` min 半条。

**第四片已完成**：`rules/aarch64-vector-shift.ldsl` 覆盖
`G_SHL/G_ASHR/G_LSHR` 的共享 ruleset。这是第一片**规则约束两个类型槽位
关系**的切片：`minScalarSameAs` 比较槽 0 的标量宽度与槽 1 的总宽度，
`min/maxScalarEltSameAsIf` 把源 LLT 整体复制到槽 1。DSL 新增
`ScalarSizeVsSizeCmp`、`CopyType` 与域级 `filter same_shape(t0, t1)`；
`minScalarSameAs` 由 core 降为 macro。270 条查询（90 个 shape-consistent
类型对 × 3 opcode）零分歧，19/21 规则，两条未命中是 `v1` 结构性不可达的
`clampNumElements` min 半条。

本片同时得到三个上游发现，并写进 note：`(v2s128, s64)` 存在
`WidenScalar t1:s128` 与 `NarrowScalar t1:s64` 的真实二周期；
`(v8s8, s32)` 与 `(v8s8, v3s16)` 会在
`LegalizerInfo.cpp:204` 的 `mutationIsSane` 断言崩溃。三条查询都违反
opcode 的 shape 契约，因此域必须显式声明契约，而不是扩张到契约外。

**第五片已完成**：`rules/aarch64-icmp.ldsl` 覆盖 `G_ICMP`。这是第一片
`Types[0]` 不是操作数类型的切片：槽 0 是比较结果 mask，槽 1 是 LHS/RHS
共享的操作数类型。DSL 新增 `PointerVector`、`ElemTypesEq`、`EltSizeNotPow2`
三个谓词，以及 `min_scalar_or_elt`、`widen_scalar_or_elt_to_next_pow2`
两个宏；`min_scalar_or_elt_if` 扩展为可接受任意谓词（旧 lanes 形式保留）。
173 条查询零分歧，19/23 规则。

本片引入了新的证人类别 `shadowed`：规则 14（`v4s16` min clamp）的谓词在
D 上可满足（`v3s16`），但 `v2s16` 被规则 9 抢先、`v3s16` 被规则 11 补成
`v4s16`，因此它永远不是首次命中。这与“谓词不可满足”的 `unreachable`
是不同的事实；检查器新增 R003/R004/R005 三条判据区分二者。

**第六片已完成**：`rules/aarch64-fcmp.ldsl` 覆盖 `G_FCMP`。这是第一片带
浮点操作数的切片：槽 0 是整数比较 mask，槽 1 是被比较的浮点值。DSL 新增
浮点 LLT（`LLT.kind="float"` + `FpSemantics`）、`LLT.eq_llvm()`、
`is_float(tN)`、`scalar_is_float(tN, ...)`、`change_element_to(...)` 与
`libcall_for_pairs`。185 条查询零分歧，13/14 规则；rule 9（`v2s32` min
clamp）因 `v1` 不存在而不可达，没有 shadowed 规则。

本片固定了三个上游事实：`LLT::scalar(N)` 是 `ANY_SCALAR`（与同尺寸任意
标量相等，`v6f32` 会命中 `s32` 键的 lane clamp）；`LLT::integer` 与
`LLT::scalar` 是不同 Kind（浮点结果槽为 Unsupported，域用
`not is_float(t0)` 排除）；生成 oracle 必须自己声明
`LLT::setUseExtended(true)`，否则 `isFloat16()` 退化为 16 位标量判断。

**第七片已完成**：`rules/aarch64-concat-vectors.ldsl` 覆盖
`G_CONCAT_VECTORS`。这是第一片同时带 **custom 逃逸点**与
**消费源槽形状的 mutation** 的切片。DSL 新增 `fixed_vector(tN)`、
`concat_shape(t0,t1)`、`concat_shape_any(t0,t1)`、
`is_pow2(elem_bits(tN))` 与 `concat_bitcast(tN)`；28 条查询零分歧，3/3 规则。

域过滤器用的是 `concat_shape_any` 而不是 `concat_shape`：
`MachineVerifier.cpp:1611-1631` 只要求 `lanes(dst) == k * lanes(src)` 且
`k >= 2`，两个 type slot 把 k 恢复出来。k=2 是两源形式的特例，不是 opcode
契约。用 `concat_shape` 会把 k=4/8 的合法探针挡在证据之外，那是覆盖债。

`concat_bitcast` 不是 `same_size_as`：它把结果元素宽度设成源的**总宽度**，
再把结果 lane 数除以源的 lane 数。`(v8s8, v4s8) -> v2s32`。
这个后继不再满足 `lanes(t0) == k * lanes(t1)`（k >= 2），因此模型新增
`Domain.accepts()` 与 `OutOfDomain` 状态：bitcast 后继被记为域边界，
不再喂回规则表；`ConcatBitcast` 在 lane 不可整除时仍抛 `T017`。

配套的真实 MIR 审计（`notes/aarch64-concat-real-mir.md`）用
`llc -debug-only=legalizer` 抓到 11 条实际查询：k=2:9 / k=4:1 / k=8:1，
全部落在 D 内，0 契约违约、0 策略分歧。审计还确认 IRTranslator 发射的是
`G_SHUFFLE_VECTOR`，concat 由 pre-legalizer combiner 生成。

本片再次确认 `custom` 是决策函数与改写函数的边界：规则 1 只证明策略层
把 `G_CONCAT_VECTORS` 交给 `AArch64LegalizerInfo::legalizeCustom`，
`legalizeConcatVectors` 生成的 MIR 不在 DSL 的证明范围内。
`libcallFor({s32,s128})` 只在策略层被证明，调用体属于 `LegalizerHelper`。

仍未覆盖：

- scalable vector、SVE、内存描述与原子组合；
- 其它 target 的固定向量 legalFor（X86/RISC-V 尚未做）；
- 域需要加入 `vNsM`，并避免把 pointer 喂给未加 `isVector()` 保护的谓词（AArch64 已踩过这个断言）；
- 固定向量 FP 的算术/转换尚未覆盖（G_FCMP 已覆盖浮点比较）。

### L4：scalable vector（大）

- AArch64 SVE/SVE2、RISC-V V；
- 当前 DSL 的 `LLT` 只支持固定向量，需要增加 scalable vector 表示、`getKnownMinValue`、`getELen` 等配置；
- 这不是"多写规则"，而是 DSL 模型的扩展。

### L5：完整特性矩阵（很大）

- AArch64：SVE、SVE2、FP16、BF16、PAuth、LSE、内存标签……（CSSC scalar 已完成）
- RISC-V：RV32/RV64 × I/M/A/F/D/C/V × Zb* × P；
- X86：SSE/AVX/AVX2/AVX512 子集。

完整覆盖意味着"每个特性组合一份策略 + 一套域 + 一次对拍"，规模是组合爆炸。

## 5. 建议的完成标准

不要用"文件大小接近 C++"或"规则条数"作为完成标准。建议用：

```text
1. 明确写出配置（triple/cpu/features/指针宽度）；
2. 明确写出域 D（类型/内存/立即数）；
3. D ∪ R 上 0 分歧；
4. 声明域内 100% 规则命中（或显式登记 unreachable）；
5. 静态检查 0 error / 0 unknown；
6. 文档写清排除项。
```

当前所有已登记实验都满足这六条；它们不是"完整后端"，但它们是**可证伪、可复现、边界明确**的策略层模型。

## 6. 证据位置

```text
out/conformance-report.json
out/x86-scalar-conformance-report.json
out/aarch64-scalar-conformance-report.json
out/aarch64-cssc-scalar-conformance-report.json
out/aarch64-cssc-flips.json
out/aarch64-pointer-conformance-report.json
out/aarch64-atomic-conformance-report.json
out/aarch64-atomic-gen-conformance-report.json
out/aarch64-atomic-outline-conformance-report.json
out/aarch64-atomic-outline-gen-conformance-report.json
out/aarch64-atomic-lse-conformance-report.json
out/aarch64-atomic-generic-vs-lse.json
out/aarch64-atomic-generic-vs-outline.json
out/aarch64-vector-conformance-report.json
out/aarch64-vector-gen-conformance-report.json
out/aarch64-vector-arith-conformance-report.json
out/aarch64-vector-arith-gen-conformance-report.json
out/aarch64-vector-mul-conformance-report.json
out/aarch64-vector-mul-gen-conformance-report.json
out/aarch64-vector-shift-conformance-report.json
out/aarch64-vector-shift-gen-conformance-report.json
out/aarch64-icmp-conformance-report.json
out/aarch64-icmp-gen-conformance-report.json
out/aarch64-fcmp-conformance-report.json
out/aarch64-fcmp-gen-conformance-report.json
out/riscv64-scalar-conformance-report.json
out/riscv64-pointer-conformance-report.json
out/riscv64-mzb-scalar-conformance-report.json
out/riscv64-pointer-mzb-conformance-report.json
out/riscv32-scalar-conformance-report.json
out/riscv32-mzb-scalar-conformance-report.json
out/riscv32-pointer-conformance-report.json
out/riscv-conditional-scalar-rv64-conformance.json
out/riscv-conditional-scalar-rv32-conformance.json
out/riscv32-pointer-mzb-conformance-report.json

notes/x86-gisel-experiment.md
notes/aarch64-gisel-experiment.md
notes/aarch64-pointer-experiment.md
notes/aarch64-cssc-experiment.md
notes/aarch64-atomic-experiment.md
notes/aarch64-vector-experiment.md
notes/aarch64-vector-arith-experiment.md
notes/aarch64-vector-mul-experiment.md
notes/aarch64-vector-shift-experiment.md
notes/aarch64-icmp-experiment.md
notes/aarch64-fcmp-experiment.md
notes/riscv64-gisel-experiment.md
notes/riscv64-pointer-experiment.md
notes/riscv64-mzb-experiment.md
notes/riscv32-gisel-experiment.md
notes/riscv32-mzb-experiment.md
notes/riscv32-pointer-experiment.md
notes/legalizer-dsl-config-guard.md
notes/claim-ledger.md
notes/construct-coverage.md
```