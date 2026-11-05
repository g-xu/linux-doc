# 内核加密 API（Crypto API）

> A 级文档 · 核心 `crypto/`（153 个 .c 文件，67722 行）· 头文件 `include/linux/crypto.h` + `include/crypto/algapi.h` + 各类型头文件

## 1 定位

Crypto API 是内核的加密算法框架。它提供统一的接口来注册、查找、实例化和使用各种加密算法——从软件通用实现到硬件加速（arm64 CE/NEON 指令），从对称加密到哈希、AEAD、公钥、RNG。主要消费者包括 IPsec（`net/ipv4/`、`net/ipv6/`）、WiFi（`net/mac80211/`）、文件系统加密（`fs/crypto/`）、dm-crypt（`drivers/md/`）、SMB（`fs/smb/`）等。

## 2 核心概念模型

```
算法注册者                    Crypto 核心                     算法使用者
(驱动/模块)                  (crypto/*.c)                    (网络/文件系统/...)
     │                            │                              │
     │ crypto_register_alg()      │                              │
     ├───────────────────────────>│  crypto_alg_list (全局链表)   │
     │                            │                              │
     │                            │   crypto_alloc_skcipher("aes")
     │                            │<─────────────────────────────┤
     │                            │  查找 → 优先级匹配 → 分配 tfm │
     │                            │─────────────────────────────>│
     │                            │  返回 crypto_skcipher *      │
     │                            │                              │
     │                            │   crypto_skcipher_setkey()   │
     │                            │<─────────────────────────────┤
     │                            │   crypto_skcipher_encrypt()  │
     │                            │<─────────────────────────────┤
```

**三层结构**：
1. **算法层**（`struct crypto_alg`）：算法的描述和实现
2. **前端层**（`struct crypto_type`）：按类型分派（skcipher/aead/shash/ahash/...）
3. **模板层**（`struct crypto_template`）：组合算法（如 `cbc(aes)` = CBC 模式 + AES 原语）

## 3 代码地图

### 核心框架

| 文件 | 行数 | 职责 |
|------|------|------|
| `crypto/api.c` | 705 | 中心 API：算法查找、transform 分配 |
| `crypto/algapi.c` | 1311 | 底层 API：注册、模板、实例、spawn 管理 |
| `crypto/algboss.c` | 273 | 加密管理器：自动加载、测试编排 |
| `crypto/internal.h` | 178 | 内部头文件：`crypto_larval` 等 |

### 各类型前端

| 文件 | 行数 | 算法类型 |
|------|------|----------|
| `crypto/skcipher.c` | 984 | 对称密钥密码（scatterlist） |
| `crypto/aead.c` | 302 | 认证加密（AEAD） |
| `crypto/shash.c` | 633 | 同步哈希 |
| `crypto/ahash.c` | 660 | 异步哈希 |
| `crypto/rng.c` | 236 | 随机数生成器 |
| `crypto/akcipher.c` | 166 | 非对称密钥密码 |
| `crypto/kpp.c` | 150 | 密钥协商原语 |
| `crypto/scompress.c` | 311 | 同步压缩 |
| `crypto/acompress.c` | 198 | 异步压缩 |

### arm64 硬件加速（`arch/arm64/crypto/`）

22 个 .c 胶水文件 + 21 个 .S 汇编文件，共 43 个文件。加速算法：

| 算法 | 加速方式 | 优先级 |
|------|----------|--------|
| AES | ARM CE 指令 | 200-300 |
| SHA-1/224/256/384/512 | CE / NEON | 200 |
| SHA-3 | CE | 200 |
| SM3 | CE / NEON | 200 |
| SM4 | CE / NEON | -- |
| ChaCha20 | NEON | 300 |
| GHASH | CE | 150-300 |
| Poly1305 | NEON | 200 |
| CRC-T10DIF | CE | 100-200 |
| NHPoly1305 | NEON | 200 |

通用 C 实现优先级为 100，硬件加速 200-300，框架自动选择最高优先级的实现。

## 4 核心数据结构

### `struct crypto_alg`（`include/linux/crypto.h:479-519`）

全局算法描述符，每个算法注册一个。

| 字段 | 说明 |
|------|------|
| `cra_list` | 全局 `crypto_alg_list` 链表节点 |
| `cra_flags` | 类型位（`CRYPTO_ALG_TYPE_*`）+ 状态位 |
| `cra_blocksize` | 最小可处理单元（字节） |
| `cra_ctxsize` | transform 上下文大小 |
| `cra_priority` | 选择优先级（高者胜出） |
| `cra_name[128]` | 通用名（如 `"cbc(aes)"`），用于模糊查找 |
| `cra_driver_name[128]` | 唯一驱动名（如 `"cbc-aes-ce"`），用于精确查找 |
| `cra_type` | 指向 `struct crypto_type`（前端） |
| `cra_init/exit/destroy` | 生命周期回调 |
| `cra_module` | 所属模块 |

### `struct crypto_tfm`（`include/linux/crypto.h:644-656`）

Transform 对象——算法的用户实例。

```c
struct crypto_tfm {
    refcount_t refcnt;              // 引用计数
    u32 crt_flags;                  // CRYPTO_TFM_NEED_KEY 等
    int node;                       // NUMA 节点
    void (*exit)(struct crypto_tfm *tfm);
    struct crypto_alg *__crt_alg;   // 反向指向算法描述符
    void *__crt_ctx[];              // 柔性数组：算法上下文
};
```

所有类型特定的 tfm（`crypto_skcipher`、`crypto_aead` 等）在末尾嵌入 `struct crypto_tfm base`。

### `struct crypto_template`（`include/crypto/algapi.h:69-77`）

模板通过组合其他算法来创建新算法实例。

```c
struct crypto_template {
    struct list_head list;              // 全局模板链表
    struct hlist_head instances;        // 从此模板创建的实例
    struct module *module;
    int (*create)(struct crypto_template *tmpl, struct rtattr **tb);
    char name[CRYPTO_MAX_ALG_NAME];
};
```

例如 `cbc` 模板的 `create` 函数会 grab 一个内部 cipher（如 AES），然后注册为 `cbc(aes)`。

### `struct crypto_spawn`（`include/crypto/algapi.h:79-92`）

依赖边——模板实例对其组成算法的依赖关系。当高优先级算法注册时，spawn 机制会触发依赖树重建，实现热替换。

### 各类型特定结构

| 结构 | 头文件 | 说明 |
|------|--------|------|
| `struct skcipher_alg` | `include/crypto/skcipher.h:108` | setkey/encrypt/decrypt + ivsize/keysize |
| `struct skcipher_request` | `include/crypto/skcipher.h:28` | cryptlen/iv/src/dst + 异步请求基类 |
| `struct aead_alg` | `include/crypto/aead.h:138` | setkey/setauthsize/encrypt/decrypt |
| `struct aead_request` | `include/crypto/aead.h:89` | assoclen/cryptlen/iv/src/dst |
| `struct shash_alg` | `include/crypto/hash.h:198` | init/update/final/digest + descsize |
| `struct ahash_alg` | `include/crypto/hash.h:134` | 同上但异步 + statesize |
| `struct crypto_shash` | `include/crypto/hash.h:239` | descsize + base tfm |
| `struct crypto_ahash` | `include/crypto/hash.h:224` | 函数指针 + reqsize + base tfm |

## 5 算法类型

| 类型常量 | 值 | 前端 | 说明 |
|----------|-----|------|------|
| `CRYPTO_ALG_TYPE_SKCIPHER` | 0x05 | `crypto/skcipher.c` | 对称密钥密码（scatterlist） |
| `CRYPTO_ALG_TYPE_AEAD` | 0x03 | `crypto/aead.c` | 认证加密（AEAD） |
| `CRYPTO_ALG_TYPE_HASH` (shash) | 0x0e | `crypto/shash.c` | 同步哈希 |
| `CRYPTO_ALG_TYPE_AHASH` | 0x0f | `crypto/ahash.c` | 异步哈希 |
| `CRYPTO_ALG_TYPE_RNG` | 0x0c | `crypto/rng.c` | 随机数生成器 |
| `CRYPTO_ALG_TYPE_AKCIPHER` | 0x0d | `crypto/akcipher.c` | 非对称密钥密码（RSA/ECDSA） |
| `CRYPTO_ALG_TYPE_KPP` | 0x08 | `crypto/kpp.c` | 密钥协商（DH/ECDH） |
| `CRYPTO_ALG_TYPE_SCOMPRESS` | 0x0b | `crypto/scompress.c` | 同步压缩 |
| `CRYPTO_ALG_TYPE_ACOMPRESS` | 0x0a | `crypto/acompress.c` | 异步压缩 |

## 6 核心 API

### 算法注册

头文件：`include/crypto/algapi.h`；实现：`crypto/algapi.c`

| API | 说明 | 备注 |
|-----|------|------|
| `crypto_register_alg(alg)` | 注册一个算法 | 加入 `crypto_alg_list`，触发测试 |
| `crypto_unregister_alg(alg)` | 注销算法 | |
| `crypto_register_algs(algs, count)` | 批量注册 | 驱动常用 |
| `crypto_register_template(tmpl)` | 注册模板 | 如 `cbc`、`hmac` |
| `crypto_register_instance(tmpl, inst)` | 注册模板实例 | 如 `cbc(aes)` |
| `crypto_register_skcipher(alg)` | 注册 skcipher 算法 | `crypto/skcipher.c:810` |
| `crypto_register_aead(alg)` | 注册 AEAD 算法 | `crypto/aead.c:237` |
| `crypto_register_shash(alg)` | 注册同步哈希 | `crypto/shash.c:561` |
| `crypto_register_ahash(alg)` | 注册异步哈希 | `crypto/ahash.c:584` |

### Transform 分配与释放

头文件：各类型头文件；实现：各前端 .c

| API | 说明 | 备注 |
|-----|------|------|
| `crypto_alloc_skcipher(name, type, mask)` | 分配 skcipher transform | 按名称查找，返回 `crypto_skcipher *` |
| `crypto_alloc_aead(name, type, mask)` | 分配 AEAD transform | |
| `crypto_alloc_shash(name, type, mask)` | 分配同步哈希 transform | |
| `crypto_alloc_ahash(name, type, mask)` | 分配异步哈希 transform | |
| `crypto_destroy_tfm(mem, tfm)` | 释放 transform | `crypto/api.c:659` |
| `crypto_has_alg(name, type, mask)` | 检查算法是否可用 | 不分配 transform |

### 对称密码操作（skcipher）

头文件：`include/crypto/skcipher.h`

| API | 说明 | 备注 |
|-----|------|------|
| `crypto_skcipher_setkey(tfm, key, keylen)` | 设置密钥 | 成功清除 `NEED_KEY` 标志 |
| `crypto_skcipher_encrypt(req)` | 加密 | 通过 `skcipher_alg->encrypt` 分派 |
| `crypto_skcipher_decrypt(req)` | 解密 | |
| `crypto_skcipher_ivsize(tfm)` | 取 IV 大小 | |
| `crypto_skcipher_reqsize(tfm)` | 取请求上下文大小 | 用于 `SKCIPHER_REQUEST_ON_STACK` |

### AEAD 操作

头文件：`include/crypto/aead.h`

| API | 说明 | 备注 |
|-----|------|------|
| `crypto_aead_setkey(tfm, key, keylen)` | 设置密钥 | |
| `crypto_aead_setauthsize(tfm, authsize)` | 设置认证标签大小 | |
| `crypto_aead_encrypt(req)` | 认证加密 | 同时产生密文和标签 |
| `crypto_aead_decrypt(req)` | 认证解密 | 同时验证标签 |

### 同步哈希操作（shash）

头文件：`include/crypto/hash.h`

| API | 说明 | 备注 |
|-----|------|------|
| `crypto_shash_init(desc)` | 初始化哈希状态 | inline，`include/crypto/hash.h:942` |
| `crypto_shash_update(desc, data, len)` | 喂入数据 | `crypto/shash.c:121` |
| `crypto_shash_final(desc, out)` | 输出摘要 | `crypto/shash.c:163` |
| `crypto_shash_digest(desc, data, len, out)` | 一步完成 | init+update+final |
| `crypto_shash_finup(desc, data, len, out)` | update+final 合并 | `crypto/shash.c:183` |
| `crypto_shash_tfm_digest(tfm, data, len, out)` | 最简一步哈希 | 内部分配 desc，适合一次性使用 |
| `crypto_shash_setkey(tfm, key, keylen)` | 设置密钥（HMAC 用） | |

### Spawn API

头文件：`include/crypto/algapi.h`；实现：`crypto/algapi.c`

| API | 说明 | 备注 |
|-----|------|------|
| `crypto_grab_spawn(spawn, inst, name, type, mask)` | 声明对内部算法的依赖 | 模板 `create` 函数中调用 |
| `crypto_drop_spawn(spawn)` | 释放 spawn 依赖 | |
| `crypto_spawn_tfm(spawn, type, mask)` | 从 spawn 获取 tfm | |
| `crypto_grab_shash(spawn, ...)` | 类型安全的 shash spawn | |

## 7 端到端调用路径

### skcipher 加密完整流程

```
1. 分配 transform
   crypto_alloc_skcipher("cbc(aes)", 0, 0)
     → crypto_alloc_tfm_node("cbc(aes)", &crypto_skcipher_type, 0, 0)
       → crypto_alg_mod_lookup() → 扫描 crypto_alg_list
         → 精确匹配 cra_driver_name 或模糊匹配 cra_name（取最高优先级）
         → 未找到则 request_module("crypto-cbc(aes)") 自动加载
       → crypto_create_tfm_node() → 分配内存 + init_tfm

2. 设置密钥
   crypto_skcipher_setkey(tfm, key, 32)
     → 校验 keylen 在 min_keysize~max_keysize 范围内
     → 分派到 skcipher_alg->setkey()
     → 成功则清除 CRYPTO_TFM_NEED_KEY

3. 准备请求
   SKCIPHER_REQUEST_ON_STACK(req, tfm);     // 栈上分配请求
   crypto_skcipher_set_crypt(req, src, dst, len, iv);

4. 加密
   crypto_skcipher_encrypt(req)
     → 检查 NEED_KEY 标志（未设密钥返回 -ENOKEY）
     → 分派到 skcipher_alg->encrypt(req)
     → 硬件实现可能异步完成（通过回调）

5. 清理
   crypto_destroy_tfm(tfm, tfm);
```

### 算法查找与优先级匹配

```
crypto_alg_mod_lookup(name)
  → __crypto_alg_lookup(name, type, mask)
    → 遍历 crypto_alg_list
    → 跳过 (cra_flags ^ type) & mask ≠ 0 的条目
    → 先试精确匹配 cra_driver_name
    → 再试模糊匹配 cra_name，取最高 cra_priority
  → 未找到 → request_module() → 创建 crypto_larval 占位
  → crypto_larval_wait() 等待实际算法注册
```

### Spawn 热替换

```
新的高优先级 AES 驱动注册
  → crypto_register_alg()
  → crypto_remove_spawns() → 深度遍历依赖树
    → 找到所有使用旧 AES 的模板实例（cbc(aes), gcm(aes), ...）
    → 注销这些实例
  → crypto manager (algboss.c) 重新创建实例
    → 使用新高优先级 AES
  → 所有消费者自动受益（透明升级）
```

## 8 优先级体系

| 实现类型 | 典型优先级 | 示例 |
|----------|-----------|------|
| 通用 C 实现 | 100 | `aes_generic.c`、`sha256_generic.c` |
| 时间安全变体 | 101 | `aes_ti.c` |
| arm64 NEON | 200-250 | `aes-neonbs-glue.c`（250） |
| arm64 CE 指令 | 250-300 | `aes-ce-glue.c`（250）、`aes-ce-ccm-glue.c`（300） |
| 模板实例 | 由内部算法计算 | `authenc`: enc×10 + auth；`ccm`: mac + enc |

## 9 消费者分布

| 目录 | 调用次数 | 用途 |
|------|---------|------|
| `drivers/crypto/` | 96 | 硬件加密驱动（fallback 分配） |
| `fs/smb/` | 14 | SMB 加密与哈希 |
| `drivers/md/` | 10 | dm-crypt/dm-verity/dm-integrity |
| `net/ipv4/` + `net/ipv6/` | 9 | IPsec (ESP/AH) |
| `net/mac80211/` | 7 | WiFi 加密（CCMP/TKIP） |
| `net/bluetooth/` | 13 | 蓝牙加密 |
| `security/keys/` | 8 | 加密密钥、DH |
| `fs/ecryptfs/` | 4 | eCryptfs 文件系统 |
| `security/integrity/` | 3 | IMA/EVM 文件哈希 |
| `fs/crypto/` | 3 | fscrypt 文件级加密 |
| `net/tls/` | 3 | 内核 TLS (kTLS) |

全树共 303 个 `crypto_alloc_*` 调用点（不含 `crypto/` 自身）。

## 10 用户态接口

Crypto API 通过 `af_alg.c`（1217 行）向用户态暴露 AF_ALG socket 族：

| 接口 | 说明 |
|------|------|
| `AF_ALG` socket | 用户态通过 socket 访问加密算法 |
| `algif_skcipher.c` | skcipher 用户态接口 |
| `algif_aead.c` | AEAD 用户态接口 |
| `algif_hash.c` | 哈希用户态接口 |
| `algif_rng.c` | RNG 用户态接口 |
| `crypto_user_base.c` | netlink 配置接口（SALG_*） |

## 11 注意事项

- **`CRYPTO_TFM_NEED_KEY`**：分配 transform 后必须先 `setkey` 再 `encrypt/decrypt`，否则返回 `-ENOKEY`
- **同步 vs 异步**：shash 是同步的（直接返回结果），ahash 是异步的（通过回调完成）。skcipher 和 aead 都支持异步
- **模板名 vs 算法名**：`"cbc(aes)"` 是模板实例名，`"cbc-aes-ce"` 是具体驱动名。`crypto_alloc_skcipher("cbc(aes)")` 会匹配最高优先级的实现
- **Spawn 依赖**：模板实例通过 spawn 机制跟踪对内部算法的依赖。高优先级实现注册时自动热替换
- **arm64 加速透明**：加载 `aes-ce` 模块后，所有使用 `"aes"` 的消费者自动切换到 CE 加速版本，无需修改代码

---

## §4 常见陷阱

### 陷阱 1：在原子上下文中调用 crypto_alloc_*

**错误写法**：
```c
spin_lock(&my_lock);
tfm = crypto_alloc_skcipher("cbc(aes)", 0, 0);
/* 可能睡眠！crypto_alloc_tfm_node 内部使用 GFP_KERNEL 分配 */
spin_unlock(&my_lock);
```

**正确写法**：
```c
/* 在进程上下文（可睡眠）中分配 tfm */
tfm = crypto_alloc_skcipher("cbc(aes)", 0, 0);
/* 然后在中断/原子上下文中使用已分配的 tfm 进行加密操作 */
spin_lock(&my_lock);
crypto_skcipher_encrypt(req);  /* 加密操作本身可以是原子的 */
spin_unlock(&my_lock);
```

**原因**：`crypto_alloc_tfm_node()` 内部调用 `crypto_alloc_tfmmem()` 使用 `GFP_KERNEL` 分配内存（`crypto/api.c:524`），还可能触发 `request_module()` 加载模块（`crypto/api.c:285`）。这些操作都会睡眠。tfm 分配是一次性开销，应在初始化路径中完成；加密/解密操作本身可以在原子上下文中使用已分配的 tfm。

**源码依据**：`crypto/api.c:524` — `crypto_alloc_tfmmem()` 使用 `GFP_KERNEL`；`crypto/api.c:285` — `request_module("crypto-%s", name)`。

### 陷阱 2：未设密钥就调用 encrypt/decrypt

**错误写法**：
```c
tfm = crypto_alloc_skcipher("cbc(aes)", 0, 0);
/* 忘记调用 setkey */
crypto_skcipher_encrypt(req);
/* 返回 -ENOKEY */
```

**正确写法**：
```c
tfm = crypto_alloc_skcipher("cbc(aes)", 0, 0);
err = crypto_skcipher_setkey(tfm, key, keylen);
if (err)
    goto err_free;
/* 现在可以安全加密 */
crypto_skcipher_encrypt(req);
```

**原因**：分配 transform 后，`CRYPTO_TFM_NEED_KEY` 标志被设置（`include/linux/crypto.h:147`）。加密/解密函数在分派到算法实现前会检查此标志——如果未通过 `setkey` 清除，直接返回 `-ENOKEY`。部分算法（标记了 `CRYPTO_ALG_OPTIONAL_KEY` 的）有默认密钥可跳过此步骤，但绝大多数对称加密算法必须显式设密钥。

**源码依据**：`include/linux/crypto.h:147` — `CRYPTO_TFM_NEED_KEY` 定义；`include/linux/crypto.h:93-96` — `CRYPTO_ALG_OPTIONAL_KEY` 说明。

### 陷阱 3：混淆 cra_name 和 cra_driver_name 导致选错实现

**错误写法**：
```c
/* 想精确使用 arm64 CE 加速版本 */
tfm = crypto_alloc_skcipher("cbc-aes-ce", 0, 0);
/* 如果 aes-ce 模块未加载，将返回 -ENOENT */
/* 不会自动降级到通用实现 */
```

**正确写法**：
```c
/* 使用通用名——框架自动选最高优先级实现 */
tfm = crypto_alloc_skcipher("cbc(aes)", 0, 0);
/* 加载 aes-ce 模块后自动使用 CE 加速版本 */
/* 未加载时使用 aes_generic 软件实现 */
```

**原因**：`__crypto_alg_lookup()` 的查找逻辑是：先精确匹配 `cra_driver_name`，再模糊匹配 `cra_name`（`crypto/api.c:74-88`）。精确匹配 `cra_driver_name` 时不会 fallback 到其他实现；模糊匹配 `cra_name` 时取最高 `cra_priority` 的算法。使用通用名 `"cbc(aes)"` 才能享受优先级体系的自动选择和热替换。

**源码依据**：`crypto/api.c:74-88` — `__crypto_alg_lookup()` 先 `exact = !strcmp(q->cra_driver_name, name)`，再 `fuzzy = !strcmp(q->cra_name, name)`。

### 陷阱 4：dma_map_sg 返回值被误用为 nents

**错误写法**：
```c
int nents = dma_map_sg(dev, sg, 4, DMA_TO_DEVICE);
/* 假设 nents == 4，继续用原始 4 个 entry 操作硬件 */
for (i = 0; i < 4; i++)
    setup_dma(sg[i].dma_address, sg[i].dma_length);
/* 错误！dma_map_sg 可能合并 entry，实际映射数可能 < 4 */
```

**正确写法**：
```c
int mapped = dma_map_sg(dev, sg, 4, DMA_TO_DEVICE);
if (mapped <= 0)
    goto error;
/* 使用返回值 mapped 遍历映射后的 entry */
for (i = 0; i < mapped; i++)
    setup_dma(sg[i].dma_address, sg[i].dma_length);
/* unmap 时必须传原始 nents */
dma_unmap_sg(dev, sg, 4, DMA_TO_DEVICE);
```

**原因**：`dma_map_sg_attrs()` 返回实际映射的段数，可能小于传入的 `nents`（`kernel/dma/mapping.c:227-236`）。IOMMU 驱动可能合并相邻的物理连续段。`dma_unmap_sg` 必须使用原始的 `nents` 而非返回值（`kernel/dma/mapping.c:279-293`），因为底层需要遍历原始 scatterlist 结构。

**源码依据**：`kernel/dma/mapping.c:227-236` — 返回值 `ret` 可能 < `nents`；`kernel/dma/mapping.c:279` — `dma_unmap_sg_attrs()` 使用传入的 `nents`。

### 陷阱 5：crypto_destroy_tfm 传错 mem 指针

**错误写法**：
```c
struct crypto_skcipher *tfm = crypto_alloc_skcipher("cbc(aes)", 0, 0);
/* ... 使用 tfm ... */
crypto_free_tfm((struct crypto_tfm *)tfm);
/* 错误！crypto_skcipher 的 base 不在结构体开头 */
/* 导致释放错误的内存地址 */
```

**正确写法**：
```c
struct crypto_skcipher *tfm = crypto_alloc_skcipher("cbc(aes)", 0, 0);
/* ... 使用 tfm ... */
crypto_free_skcipher(tfm);
/* 或正确方式：传分配时返回的原始指针 */
/* crypto_destroy_tfm(mem, tfm) 中 mem 是分配返回的起始地址 */
```

**原因**：`crypto_destroy_tfm(void *mem, struct crypto_tfm *tfm)` 的第一个参数 `mem` 是 `kzalloc` 返回的原始指针（`crypto/api.c:659-676`），第二个参数 `tfm` 用于获取 alg 引用。前端结构（如 `crypto_skcipher`）在 `crypto_tfm base` 之前有额外字段——`crypto_alloc_tfmmem()` 返回的 `mem` 指向 `tfm` 之前的偏移位置（`crypto/api.c:508`：`tfm = (struct crypto_tfm *)(mem + tfmsize)`）。传错 `mem` 会导致 `kfree_sensitive(mem)` 释放错误地址。

**源码依据**：`crypto/api.c:508` — `tfm = (struct crypto_tfm *)(mem + tfmsize)`；`crypto/api.c:674` — `kfree_sensitive(mem)`。

---

## §6 取证摘要

**读取的文件**：
- `include/linux/crypto.h` — `struct crypto_alg`、`struct crypto_tfm`、`CRYPTO_TFM_NEED_KEY`、`CRYPTO_ALG_ALLOCATES_MEMORY` 约束条件、`CRYPTO_MINALIGN` 对齐要求
- `crypto/api.c` — 算法查找逻辑（`__crypto_alg_lookup`）、tfm 分配（`crypto_alloc_tfm_node`/`crypto_create_tfm_node`）、释放（`crypto_destroy_tfm`）、`crypto_larval_wait` 超时机制
- `include/crypto/algapi.h` — `struct crypto_template`、`struct crypto_spawn` 定义（通过文档引用）
- `crypto/skcipher.c` — skcipher 前端注册（通过文档引用）

**关键论断出处**：
- "`crypto_alloc_tfm_node` 使用 `GFP_KERNEL`" — 来源：`crypto/api.c:524`（`crypto_alloc_tfmmem` 中 `kzalloc_node(total, gfp, node)`，`gfp = GFP_KERNEL`）
- "精确匹配 `cra_driver_name` 优先于模糊匹配 `cra_name`" — 来源：`crypto/api.c:74-88`
- "`crypto_destroy_tfm` 的 `mem` 参数是分配起始地址" — 来源：`crypto/api.c:508,659-676`
- "`dma_map_sg` 返回值可能 < nents" — 来源：`kernel/dma/mapping.c:227-236`
- "`CRYPTO_TFM_NEED_KEY` 阻止未设密钥的加密" — 来源：`include/linux/crypto.h:147`

**未验证项**：
- `crypto_skcipher_encrypt()` 内部检查 `NEED_KEY` 的精确行号（需查看 `crypto/skcipher.c` 的 inline 分派逻辑）
- 模板实例热替换时 `crypto_remove_spawns()` 的完整依赖树遍历逻辑
- `CRYPTO_ALG_ALLOCATES_MEMORY` 约束中"某些边界情况仍会分配"的具体场景
