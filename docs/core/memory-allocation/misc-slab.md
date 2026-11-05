# kmem_cache — 直接 slab 缓存 API

> kmem_cache 是 slab 分配器的核心对象——代表一种"相同大小对象"的缓存。slab/kmalloc 是它的上层包装（按大小分桶），而 kmem_cache 允许驱动自行创建定制大小的缓存，避免 kmalloc 大小类的内部碎片。

---

### 定位

**功能**：kmem_cache 是 slab 分配器的核心对象——代表一种"相同大小对象"的缓存。当驱动或子系统需要频繁分配/释放同一种结构体时，直接创建 kmem_cache 比用 kmalloc 更优：零内部碎片（对象大小精确匹配）、支持构造函数初始化、支持 `SLAB_TYPESAFE_BY_RCU` 延迟释放、支持 `kmem_cache_create_usercopy()` 限定用户空间拷贝白名单。

**实现方式**：核心结构体 `struct kmem_cache`（`include/linux/slub_def.h:90-143`，SLUB 实现）包含 per-CPU 无锁快速路径（`cpu_slab`，含 freelist 和当前 slab）、对象大小、对齐、构造函数和 per-node 管理。分配快速路径读 per-CPU `freelist`，用 `this_cpu_cmpxchg_double()` 原子推进（无锁、无中断禁用）；失败进入慢路径 `__slab_alloc()` 从 node partial list 或页分配器获取新 slab。释放快速路径（`mm/slub.c:3618`）匹配当前 per-CPU slab 时用 `this_cpu_cmpxchg_double()` 前置到 freelist，否则走 `__slab_free()` 取 `node->list_lock` 放入 node partial list。

**应用场景**：全树约 200+ 处 `kmem_cache_create` 调用，1,500+ 处 `kmem_cache_alloc`。主要用户：文件系统（inode/dentry 缓存，如 ext4、btrfs）、网络子系统（`net/core/skbuff.c` 的 sk_buff、`net/socket.c`）、设备驱动、安全子系统（`security/security.c`）、OVS 流表（`net/openvswitch/flow_table.c`）、调试对象（`lib/debugobjects.c`）。arm64 上 `arch/arm64/mm/pgd.c` 直接用 kmem_cache 管理 PGD 页表分配。

## 主要 API

头文件：`include/linux/slab.h`；实现：`mm/slab_common.c`（创建/销毁）+ `mm/slub.c`（分配/释放）

| API | 说明 | 备注 |
|-----|------|------|
| `kmem_cache_create(name, size, align, flags, ctor)` | 创建缓存 | 内部调用 `kmem_cache_create_usercopy()` 并设 useroffset=0 |
| `kmem_cache_create_usercopy(name, size, align, flags, useroffset, usersize)` | 创建带 usercopy 白名单的缓存 | `CONFIG_HARDENED_USERCOPY` 检查用 |
| `kmem_cache_alloc(s, gfp)` | 分配一个对象 | 快速路径无锁 |
| `kmem_cache_alloc_lru(s, lru, gfp)` | 分配并关联 LRU | 文件系统 inode/dentry 缓存用 |
| `kmem_cache_alloc_node(s, gfp, node)` | 从指定 NUMA 节点分配 | |
| `kmem_cache_zalloc(s, gfp)` | 分配并清零 | inline：`kmem_cache_alloc(s, gfp \| __GFP_ZERO)` |
| `kmem_cache_alloc_bulk(s, gfp, size, p)` | 批量分配 | 一次持锁分配 size 个对象 |
| `kmem_cache_free(s, obj)` | 释放对象 | 验证对象确实属于缓存 s |
| `kmem_cache_free_bulk(s, size, p)` | 批量释放 | 按 slab 分组后批量操作 |
| `kmem_cache_destroy(s)` | 销毁缓存 | 减 refcount，到 0 时释放所有 slab |
| `kmem_cache_shrink(s)` | 收缩缓存 | 释放尽可能多的空 slab |
| `kmem_cache_size(s)` | 返回对象大小 | `s->object_size`（原始请求大小） |

### 便捷宏

```c
KMEM_CACHE(struct_name, flags)           // 创建 sizeof(struct_name) 的缓存
KMEM_CACHE_USERCOPY(struct_name, flags, field)  // 白名单某个字段
```

## SLAB 标志

| 标志 | 语义 |
|------|------|
| `SLAB_POISON` | 用 0xa5a5a5a5 填充空闲对象 |
| `SLAB_RED_ZONE` | 检测缓冲区溢出 |
| `SLAB_HWCACHE_ALIGN` | 对齐到缓存行 |
| `SLAB_CACHE_DMA` | 使用 GFP_DMA 内存 |
| `SLAB_STORE_USER` | 记录最后拥有者（调试用） |
| `SLAB_PANIC` | 创建失败时 panic |
| `SLAB_TYPESAFE_BY_RCU` | 延迟 slab 页释放到 RCU（注意：不是对象级 RCU） |
| `SLAB_RECLAIM_ACCOUNT` | 标记为可回收 |
| `SLAB_ACCOUNT` | 计入 memcg |

## arm64 注意事项

- `ARCH_DMA_MINALIGN` = 128 字节（`arch/arm64/include/asm/cache.h:26`）
- `ARCH_SLAB_MINALIGN`：无 KASAN 时为 8 字节；KASAN SW_TAGS 为 16；KASAN HW_TAGS 运行时可选 16
- arm64 在 `arch/arm64/mm/pgd.c` 中直接使用 kmem_cache 管理 PGD 分配

## 使用规模

`kmem_cache_create` 在全树约 200+ 处调用，`kmem_cache_alloc` 约 1,500+ 处，`kmem_cache_free` 约 1,500+ 处。主要用户包括文件系统（inode/dentry/ext4 inode cache）、网络（sk_buff、socket）、设备驱动。
