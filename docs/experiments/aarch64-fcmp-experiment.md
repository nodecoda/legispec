# AArch64 GlobalISel G_FCMP：浮点比较的结果槽与操作数槽

Date: 2026-10-21
Status: 已证明（hand + generated：185 查询，0 分歧，13/14 规则）

## 1. 范围

`rules/aarch64-fcmp.ldsl` 转写
`llvm/lib/Target/AArch64/GISel/AArch64LegalizerInfo.cpp`
（LLVM `d855d9e48067026317998cbff734004f328f833d`）771-799 行：

```text
getActionDefinitionsBuilder(G_FCMP)
    .legalFor({{i32, f32}, {i32, f64}, {v4i32, v4f32},
               {v2i32, v2f32}, {v2i64, v2f64}})
    .legalFor(HasFP16, {{i32, f16}, {v4i16, v4f16}, {v8i16, v8f16}})
    .widenScalarOrEltToNextPow2(1)
    .clampScalar(0, s32, s32)
    .widenScalarIf(... f16 || bf16 ..., changeElementTo(1, f32))
    .scalarizeIf(scalarOrEltWiderThan(1, 64), 1)
    .minScalarEltSameAsIf(... result <- operand element ..., 0, 1)
    .clampNumElements(1, v4s16, v8s16)
    .clampNumElements(1, v2s32, v4s32)
    .clampMaxNumElements(1, s64, 2)
    .moreElementsToNextPow2(1)
    .libcallFor({{s32, s128}});
```

固定配置：

```text
triple      = aarch64-unknown-linux-gnu
cpu         = generic
features    = none（无 +fullfp16，无 +sve）
HasFP16     = false
pointer     = LLT::pointer(0, 64)，写作 p0:64
extended    = LLT::setUseExtended(true)   （AArch64TargetMachine.cpp:417）
```

这是第一个带**浮点操作数**的切片。槽位语义与 G_ICMP 同形：

```text
Types[0] = 比较结果 mask（i1 / vNi1，随后被 clamp 到 s32 / vNs32）
Types[1] = 被比较的浮点值（f16 / bf16 / f32 / f64 / f128 或固定向量）
```

与 G_ICMP 的区别在于：槽 1 的常量是浮点 LLT，而浮点 LLT 的相等、
加宽、元素替换都依赖 `LLT::FpSemantics`，所以这一片同时是
**DSL 浮点类型模型**的第一片。

`HasFP16 = false` 是一个**冻结配置**。C++ 的第二个 `legalFor` 被
`HasFP16` 包住，在这个配置下宏不发射任何规则，因此规则文件直接省略它，
不写 guard。这与 `rules/aarch64-atomic.ldsl` 对 `UseOutlineAtomics`
的处理一致：一个产品点一份冻结文件；`+fullfp16` 若要做，应是独立文件，
而不是让所有消费者都传 `--define HasFP16=false`。

## 2. 规则展开

C++ builder 在这个配置下展开为 14 条规则，索引与源码顺序一致：

```text
 0  legalFor({(s32,f32), (s32,f64), (v4s32,v4f32),
              (v2s32,v2f32), (v2s64,v2f64)})
 1  widenScalarOrEltToNextPow2(1)
 2/3  clampScalar(0, s32, s32)
 4  widenScalarIf(f16 || bf16, changeElementTo(1, f32))
 5  scalarizeIf(scalarOrEltWiderThan(1, 64), 1)
 6  minScalarEltSameAsIf(result <- operand element, 0, 1)
 7/8  clampNumElements(1, v4s16, v8s16)
 9/10 clampNumElements(1, v2s32, v4s32)
11  clampMaxNumElements(1, s64, 2)
12  moreElementsToNextPow2(1)
13  libcallFor({(s32, s128)})
```

### 2.1 规则 0：显式合法对

`legalFor` 的五个 pair 是 AArch64 原生能直接处理的比较形状：

```text
标量 f32 / f64，128-bit v4f32，64-bit v2f32 / v2f64
```

注意 C++ 把结果槽写成 `i32/i64`，这是 `LLT::integer`；DSL 目前只能写成
`s32/s64`，即 `LLT::scalar`（`ANY_SCALAR`）。在声明的域内（结果槽永远不是
浮点）两者行为一致；**域外不一致**，因为 `s32 == f32` 而 `i32 != f32`。
这是本片用 `not is_float(t0)` 把结果槽限制为整数 mask 的直接原因，
也是必须写进边界的建模近似，而不是可以忽略的细节。

### 2.2 规则 1：操作数宽度对齐到 2 的幂

`widenScalarOrEltToNextPow2(1)` 只看槽 1。标量 `s24` 会变成 `s32`，
向量的非 2 的幂元素宽度也会被向上取整。它先于所有 lane clamp 运行，
所以 `v3s16` 的 16 位元素不会在这里被改，只有宽度不是 2 的幂时才动。

### 2.3 规则 2/3：结果 mask 钉在 s32

`clampScalar(0, s32, s32)` 展开成 min 和 max 两条：

```text
2  min: s1 / s8 / s16 / s24  -> s32   （IRTranslator 的 i1 mask 在这里加宽）
3  max: s64 / s128           -> s32
```

G_FCMP 的结果是布尔 mask，AArch64 用 32 位寄存器承载，所以 min 半是
正常路径，max 半是规则闭包形状（域里保留 `s64` 作为探针）。

### 2.4 规则 4：f16 / bf16 加宽到 f32

C++ 谓词在冻结配置下是：

```text
(!HasFP16 && isFloat16) || isBFloat16
```

`HasFP16 = false` 后就是 `f16 || bf16`。mutation 是
`changeElementTo(1, f32)`：标量 `f16 -> f32`，向量 `v4f16 -> v4f32`，
**保留 lane 数**。这是 `LLT::changeElementType` 的语义，不是
`changeElementSize`。

### 2.5 规则 5：超过 64 位的向量元素被 scalarize

`scalarizeIf(scalarOrEltWiderThan(1, 64), 1)` 等价于：

```text
actionIf(FewerElements, all(pred, isVector(TypeIdx)), scalarize)
```

`isVector` 守卫很关键：标量 `s128` **不会**被它拦截，会继续走到规则 13 的
libcall。只有 `v2f128` 这样的向量才会变成 `FewerElements t1:f128`。
这条守卫是下面“libcall 可达”的前提。

### 2.6 规则 6：结果元素跟随操作数元素

C++ 谓词是三个条件的合取：

```text
Types[0].isVector()
&& !Types[1].isPointerVector()
&& Types[0].getElementType() != Types[1].getElementType()
```

mutation 是 `minScalarEltSameAsIf`，即把槽 1 的元素宽度复制到槽 0，
保留槽 0 的 lane 数。例子：

```text
(v4s16, v4f32) -> WidenScalar t0 -> v4s32
```

但 `(v4s32, v4f32)` **不会**触发，因为 `s32` 与 `f32` 在
`LLT::operator==` 下相等；它先在规则 0 变成 Legal。这正是 3.1 的
`ANY_SCALAR` 语义在规则层的可观测后果。

### 2.7 规则 7/8：16 位元素的 lane clamp

```text
7  min: 少于 4 lane -> 补到 v4s16
8  max: 多于 8 lane -> 收到 v8s16
```

`v2s16 -> v4s16`，`v16s16 -> v8s16`。与 G_ICMP 不同，这里**没有**
`minScalarOrEltIf(t1 == v2s16, ...)` 的特例在前面，所以 rule 7 的谓词
在 `v2s16` 上真的会首次命中，**不是 shadowed**。

### 2.8 规则 9/10：32 位元素的 lane clamp

```text
9  min: 少于 2 lane -> 补到 v2s32
10 max: 多于 4 lane -> 收到 v4s32
```

min 半是**结构性不可达**：LLVM 把 `v1` 表示成标量，`LLT::vector` 要求
`!EC.isScalar()`，所以“少于 2 lane 的固定向量”这个形状不存在。
max 半可达：`v8s32 -> v4s32`。由于 `s32` 是 `ANY_SCALAR`，
`v8f32` 也会命中这条 clamp，结果是 `v4f32`，元素类型保持浮点。

### 2.9 规则 11：64 位元素最多 2 lane

`clampMaxNumElements(1, s64, 2)` 只限制 64 位元素：

```text
v4s64 -> v2s64
v4f64 -> v2f64
```

它不处理 32 位或 16 位元素；那些由前面的 clamp 负责。

### 2.10 规则 12：lane 数向上取整到 2 的幂

`moreElementsToNextPow2(1)` 处理所有还没被前面 clamp 吃掉的非 2 的幂
lane 数。`v6s16 -> v8s16` 是代表性一步；`v3s32 -> v4s32` 也会走到这里
（v3 不满足 rule 9 的“少于 2”，也不满足 rule 10 的“多于 4”）。

rule 12 对 16 位元素**不是 shadowed**：`v3s16` 被 rule 7 补成 `v4s16`，
但 `v6s16` 在两个 clamp 之间，能活到 round-up。

### 2.11 规则 13：s128 的 libcall

`libcallFor({(s32, s128)})` 是这一片唯一的非表动作。它可达的原因是
rule 5 的 `isVector` 守卫：标量 `s128` 不会被 scalarize，直接落到 libcall。
由于 `s128` 是 `ANY_SCALAR`，`(s32, f128)` 也会命中，返回 `Libcall`，
而不是 `Unsupported`。

这条规则只证明**策略层选择 Libcall**；libcall 的具体调用约定、符号名、
参数传递属于 `LegalizerHelper` 与 TargetLowering，不在本片证明范围。

### 2.12 代表性一步结果

```text
(G_FCMP, [s32,  f32])    -> Legal
(G_FCMP, [s1,   f32])    -> WidenScalar   t0:s32
(G_FCMP, [s64,  f32])    -> NarrowScalar  t0:s32
(G_FCMP, [s32,  f16])    -> WidenScalar   t1:f32
(G_FCMP, [s32,  bf16])   -> WidenScalar   t1:f32
(G_FCMP, [v4s1, v4f16])  -> WidenScalar   t1:v4f32
(G_FCMP, [v2s32,v2f128]) -> FewerElements t1:f128
(G_FCMP, [v4s16,v4f32])  -> WidenScalar   t0:v4s32
(G_FCMP, [v2s16,v2s16])  -> MoreElements  t1:v4s16
(G_FCMP, [v16s16,v16s16])-> FewerElements t1:v8s16
(G_FCMP, [v8s32,v8f32])  -> FewerElements t1:v4f32
(G_FCMP, [v4s64,v4s64])  -> FewerElements t1:v2s64
(G_FCMP, [v6s16,v6s16])  -> MoreElements  t1:v8s16
(G_FCMP, [s32,  s128])   -> Libcall
(G_FCMP, [s32,  f128])   -> Libcall
```

## 3. 上游发现

### 3.1 `LLT::scalar(N)` 是 `ANY_SCALAR`，不是“整数”

AArch64 的常量写的是 `LLT::scalar(8/16/32/64/128)`，Kind 是 `ANY_SCALAR`。
`LLT::operator==` 把 `ANY_SCALAR` 与**同尺寸的任意标量**判为相等：

```text
LLT::scalar(32) == LLT::float32()      // true
LLT::scalar(16) == LLT::bfloat16()     // true
LLT::float16()  == LLT::bfloat16()     // false（语义不同）
```

可观测后果：

```text
(v6s32, v6f32) -> FewerElements t1 -> v4f32   # 命中 s32 键的 lane clamp
(s32,  f128)   -> Libcall                     # 命中 s128 的 libcall pair
(v4s32,v4f32)  -> Legal（规则 0）              # 元素类型"相等"，规则 6 不触发
(s32,  s32)    -> Legal                        # s32 同时匹配 i32 和 f32
```

Python 模型因此新增 `LLT.eq_llvm()`，所有镜像 C++ `==` 的谓词
（`TypeIn`、`TypeEq`、`TypePairInSet`、`FixedVectorElemCountCmp`、
`ElemTypesEq`、`MemTypeIn`、`MemCompatInSet`）都改用它。
普通 dataclass 相等保持“拼写精确”，只用于去重与域成员判断。

这是一个典型的“位宽近似会静默出错”的例子：如果模型把
`getElementType() !=` 写成 `elem_bits !=`，`(v4s32, v4f32)` 会被误判成
元素不同，规则 6 被错误触发。类型相等和位宽相等不是一回事。

### 3.2 `LLT::integer` 与 `LLT::scalar` 是不同 Kind

`legalFor` 的结果槽写的是 `i32/i64`，Kind 是 `INTEGER`；浮点操作数是
`FLOAT`。`INTEGER != FLOAT`，所以一个浮点结果槽不会因为“32 位”而匹配
`i32`，会落到 `Unsupported`。

本片用域过滤 `not is_float(t0)` 把这个事实显式化：

```text
Types[0] 是比较结果 mask，只能是整数形状；
Types[1] 才是被比较的浮点值。
```

同时要承认建模近似：DSL 规则 0 的常量写成 `s32/s64`，而 C++ 是
`i32/i64`。在 `not is_float(t0)` 的域内二者等价；一旦把浮点结果槽放进来，
DSL 会把 `(f32, f32)` 误判成 Legal，而 C++ 返回 Unsupported。
早期探针把浮点结果槽也放进域，产生的分歧全部属于这类域外形状；
把域收窄到 mask 结果后归零。这不是“规则表修好了”，而是“域写对了”。

### 3.3 生成 oracle 必须自己声明 `LLT::setUseExtended(true)`

手工 oracle 通过 `AArch64TargetMachine` 构造器
（`AArch64TargetMachine.cpp:417`）间接得到 extended LLT。生成的 oracle
不建 TargetMachine，如果不显式调用，`isFloat16()` 会退化成
“16 位标量”的判断，`s16` 被当成 `f16` 合法化，产生 42 个分歧。

修复是双重的：

```text
tools/oracle/main.cpp   在 GENERATED_LEGALIZER_EXTENDED_LLT 下显式调用
                        LLT::setUseExtended(true)
CMakeLists.txt          只给 legalizer-oracle-aarch64-fcmp-gen 加这个宏
```

只给 fcmp-gen 加宏，是为了不改变其它已钉 sha256 的 oracle 二进制。
这条发现说明：**配置本身就是证明的一部分**；oracle 与真实 target 的
LLT 模式不一致时，规则表再对也会给出不同答案。

### 3.4 已确认但本片未触发

- `scalarizeIf` 内含 `isVector()` 守卫，所以标量 `s128` 走到 libcall，
  而不是 scalarize。
- `LLT::changeElementSize` 对 bf16 会丢掉浮点语义，落到 `LLT::scalar`。
  G_FCMP 的 16 位元素已经是 2 的幂，rule 1 不会触发这条路径；
  但模型里保留了 `change_element_size` 的这一行为，供后续 bf16 切片使用。

## 4. 结果

```text
domain types = 40（12 scalar + 28 固定向量）
opcodes      = 1（G_FCMP）
queries      = 185（same_shape 过滤 + 结果槽非浮点）
closure      = 15 queries，0 outside D
rules        = 14
matched      = 13
unreachable  = 1（rule 9：v1 不存在）
shadowed     = 0
divergences  = 0（hand vs real AArch64；generated vs real AArch64）
```

witness 共 23 条：

```text
positives   = 19
negatives   = 3
unreachable = 1
shadowed    = 0
```

三个 negative witness 分别钉住：

```text
(v4s32, v4f32) 不触发 rule 6   # ANY_SCALAR 元素相等
(s32, s128)    不触发 rule 5   # scalarizeIf 的 isVector 守卫
(s32, s32)     不是 Unsupported # s32 同时匹配 i32 与 f32
```

## 5. 本片新增的 DSL 能力

| 构造 | 语义 | 对应 C++ |
|---|---|---|
| `LLT(kind="float", fp=...)` | 带 `FpSemantics` 的浮点 LLT | `LLT::float16/32/64/128`、`bfloat16` |
| `LLT.eq_llvm()` | 镜像 `LLT::operator==`（ANY_SCALAR 同尺寸相等） | `LLT::operator==` |
| `float_type` / `is_float` | 构造与判断浮点 LLT | `LLT::isFloat()` |
| `FloatValue` / `is_float(tN)` | 标量或向量元素是浮点 | `LLT::isFloatOrFloatVector()` |
| `scalar_is_float(tN, f16)` | 单个标量元素的浮点语义判断 | `getScalarType().isFloat16()` |
| `change_element_to(ty)` | 保留 lane 数，替换元素类型 | `LegalizeMutations::changeElementTo` |
| `libcall_for_pairs {(a,b)}` | 类型对级 libcall | `libcallFor({{a,b}})` |
| `LLT.change_element_type` | 向量换元素、标量换自身 | `LLT::changeElementType` |

覆盖范围变化：

```text
methods=63 classified=63 unclassified=0
core=27 guard=5 macro=15 none=11 partial=5
```

`constructs.py` 的分类没有新增未表示构造；浮点 LLT 与
`changeElementTo` 都是在既有 core/macro 分类下补语义。

## 6. 本片新增文件

```text
rules/aarch64-fcmp.ldsl
aarch64-fcmp-capabilities.json
witness/aarch64-fcmp.witness
tests/test_fcmp_primitives.py（18 个测试）
out/codegen-aarch64-fcmp/（AArch64FCMPGen）
scripts/check-aarch64-fcmp.ps1
```

capabilities：

```text
custom = {}
libcall = {G_FCMP}
lower   = {}
```

也就是说，这一片没有 custom handler，唯一的逃逸是 libcall 的**策略选择**；
libcall 的具体 lowering 仍不在 DSL 内。

## 7. 边界

- 只比较 `LegalizerInfo::getAction` 一步；
- 不包含 `LegalizerHelper::legalizeInstrStep` 的 MIR 改写与迭代 Legalizer pass；
- 不包含指令选择、寄存器分配与 codegen；
- 不覆盖 scalable vector（SVE）、内存描述、原子与 custom handler body；
- 不覆盖 `+fullfp16`：`HasFP16 = false` 是冻结配置，`+fullfp16` 需要独立文件；
- 不覆盖 libcall 的具体调用约定与 ABI，只证明策略层返回 `Libcall`；
- 域为 `same_shape(t0, t1)` 且 `not is_float(t0)`：
  结果槽是整数 mask，操作数槽是浮点或整数探针；
- 规则 0 的常量用 `s32/s64` 近似 C++ 的 `i32/i64`；只在上述域内等价。

### 7.1 `custom` 是决策函数与改写函数的分界线

这一片最重要的方法论结论，不是又证明了 14 条规则，而是把 DSL 的
规范地位说清楚：

```text
getAction(q)            决策函数：下一步做什么
legalizeInstrStep(...)  改写函数：具体怎么改 MIR
custom                  两者的分界线
```

DSL 能完整描述 `getAction`，也能描述“哪些 opcode 会走到 custom”这个
**目标侧接口契约**；但它不能描述 custom handler 内部生成了什么指令。
因此正确的定位是：

```text
DSL = 决策函数的完整规范 + 改写函数的目标侧接口契约
DSL ≠ 完整语义规范
```

这不是 DSL 的缺陷，而是它刻意选择的证明边界。`custom` 不是“漏掉的规则”，
而是“规则表把控制权交给 C++”的那个点。capabilities 层能验证
“每个 custom opcode 是否有处理体”，但不能验证“处理体是否语义正确”；
后者需要另一套针对 MIR 改写的规范与测试。

G_FCMP 恰好 `custom = {}`，所以它在这条边界上是完整可证的：
所有结果要么是 Legal、要么是表内动作、要么是 Libcall 策略选择。
如果将来做 `G_CONCAT_VECTORS` 这类 custom 密集的 opcode，必须把结论
写成“策略层已证明，custom body 未证明”，不能把两者混成一句“DSL 证明了
整个 lowering”。

## 8. 复现

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File `
  F:\llvm-study\toycpu\scripts\check-aarch64-fcmp.ps1
```

或先重建 oracle（会重新生成 `out/codegen-aarch64-fcmp/`）：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File `
  F:\llvm-study\toycpu\scripts\check-aarch64-fcmp.ps1 -BuildOracles
```

输出应为：

```text
strict check    errors=0 warnings=28 unknown=0 rules=14
hand vs LLVM    185 queries, 0 divergences, 13/14
gen  vs LLVM    185 queries, 0 divergences, 13/14
claim ledger    claims=41 proven=41
```

整套门禁：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File `
  F:\llvm-study\toycpu\scripts\check-legalizer-suite.ps1
```
