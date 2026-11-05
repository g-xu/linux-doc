# 内核随机数生成

> B 级文档 · 头文件 `include/linux/random.h`（205 行）· 实现 `drivers/char/random.c`（1643 行）

## 内核随机数生成

### 定位

**功能**：提供密码学安全的随机数生成服务。内核需要随机数用于安全协议（TLS/IPsec 密钥）、地址空间随机化（ASLR）、栈 canary、哈希种子、UUID 生成等场景。自 5.6 起使用基于 ChaCha20 密码学流密码的 CRNG（Cryptographic Random Number Generator），取代了早期的 SHA-1 方案。

**实现方式**：`drivers/char/random.c` 实现 CRNG 核心——从多种熵源（中断时序、输入事件、硬件 RNG、bootloader）收集熵，经 ChaCha20 密码学混合后输出随机字节。启动时经历 `random_init_early` → `random_init` → CRNG 种子就绪三个阶段。arm64 平台可通过 `arch_get_random_longs`（ARM RNDR 指令）和 `arch_get_random_seed_longs`（RNDRRS 指令）硬件加速，CRNG 重新播种时优先尝试硬件加速，失败回退到 ChaCha20 软件实现。`get_random_u32_below` 使用拒绝采样保证均匀分布。

**应用场景**：全树广泛使用。安全子系统（密钥生成、nonce 创建）、内存管理（ASLR 基址、栈 canary）、网络协议（TCP 序列号、SIP hash）、文件系统（inode 号随机化、fs UUID）、设备驱动（MAC 地址生成）等。用户态通过 `/dev/urandom`（非阻塞）和 `/dev/random`（早期阻塞）访问。

### 熵源收集

| API | 说明 | 备注 |
|-----|------|------|
| `add_device_randomness(buf, len)` | 从设备特征添加熵 | 如 MAC 地址、序列号；不保证熵质量 |
| `add_bootloader_randomness(buf, len)` | 从 bootloader 添加熵 | `__init`，仅启动时 |
| `add_input_randomness(type, code, value)` | 从键盘/鼠标事件添加熵 | `__latent_entropy`，输入子系统调用 |
| `add_interrupt_randomness(irq)` | 从中断时序添加熵 | `__latent_entropy`，中断处理调用 |
| `add_hwgenerator_randomness(buf, len, entropy)` | 从硬件 RNG 添加熵 | 带熵量估计 |
| `add_latent_entropy()` | 从编译期 latent 插件添加熵 | 进程 fork 时调用（`:22`） |

### 随机数获取 API

| API | 说明 | 备注 |
|-----|------|------|
| `get_random_bytes(buf, len)` | 填充任意长度随机字节 | 最基础的接口 |
| `get_random_u8()` | 返回随机 u8 | `:41` |
| `get_random_u16()` | 返回随机 u16 | `:42` |
| `get_random_u32()` | 返回随机 u32 | `:43`，最常用 |
| `get_random_u64()` | 返回随机 u64 | `:44` |
| `get_random_long()` | 返回随机 unsigned long | 64 位平台 = u64，32 位 = u32（`:45`） |
| `get_random_u32_below(ceil)` | [0, ceil) 范围随机 | 常量 ceil 编译期优化；拒绝采样保证均匀（`:61`） |
| `get_random_u32_above(floor)` | (floor, U32_MAX] 范围随机 | `:99` |
| `get_random_u32_inclusive(floor, ceil)` | [floor, ceil] 范围随机 | `:111` |
| `get_random_canary()` | 生成栈 canary 值 | 64 位平台首字节清零，防字符串溢出（`:133`） |

### 就绪检查与等待

| API | 说明 | 备注 |
|-----|------|------|
| `rng_is_initialized()` | CRNG 是否已初始化？ | 非阻塞查询 |
| `wait_for_random_bytes()` | 等待 CRNG 初始化完成 | 可睡眠，返回 0 或信号错误 |
| `get_random_bytes_wait(buf, len)` | 等待就绪后获取随机字节 | `wait_for_random_bytes` + `get_random_bytes`（`:145`） |
| `get_random_u32_wait(out)` | 等待就绪后获取 u32 | 宏生成（`:162`） |
| `get_random_u64_wait(out)` | 等待就绪后获取 u64 | `:163` |

### CRNG 初始化流程

```
系统启动
  → random_init_early()         ← 最早期，命令行 + bootloader 熵
  → random_init()               ← 稍后，更多熵源加入
  → CRNG 种子就绪               ← rng_is_initialized() 返回 true
  → 持续收集熵                   ← 中断/输入/硬件 RNG
```

**关键时间点**：在 CRNG 初始化完成之前调用 `get_random_*` 不会阻塞（会返回"不够随机"的值）。如果需要保证密码学安全，必须先用 `wait_for_random_bytes()` 或调用 `*_wait` 变体。

### 架构加速

arm64 通过 `arch/arm64/include/asm/archrandom.h` 提供硬件随机指令支持：

| API | 说明 |
|-----|------|
| `arch_get_random_longs(v, max_longs)` | 从 ARM RNDR 指令获取随机数 |
| `arch_get_random_seed_longs(v, max_longs)` | 从 ARM RNDRRS 指令获取种子 |

CRNG 在重新播种时会尝试使用硬件加速，失败则回退到 ChaCha20 软件实现。

### 用户态接口

| 设备 | 说明 |
|------|------|
| `/dev/urandom` | 非阻塞，始终可用（`urandom_fops`） |
| `/dev/random` | 早期会阻塞等待熵，CRNG 初始化后行为与 urandom 相同（`random_fops`） |

### 注意事项

- **不要在早期启动用 `get_random_*` 做安全决策**：CRNG 可能还没收集足够熵。用 `wait_for_random_bytes()` 或 `*_wait` 变体
- **`get_random_u32_below` 的均匀性**：非常量 ceil 走 `__get_random_u32_below`（拒绝采样），常量 ceil 走编译期优化的快速路径
- **canary 首字节清零**：64 位小端平台上 `CANARY_MASK = 0xffffffffffffff00UL`，保证 canary 不含 NUL 字节之前的随机数据——这是为了检测基于字符串函数的溢出
