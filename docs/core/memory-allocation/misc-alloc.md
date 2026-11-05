# kvmalloc — kmalloc/vmalloc 自动回退分配

> kvmalloc 先尝试 kmalloc（物理连续），失败且大小超过 PAGE_SIZE 时回退到 vmalloc（虚拟连续）。释放时 `kvfree()` 自动判断走 kfree 还是 vfree。适合"大小不确定、可能很大"的分配场景。

---

### 定位

**功能**：kvmalloc 系列提供自动选择 kmalloc（物理连续）或 vmalloc（虚拟连续）的内存分配能力。分配时根据大小自动选择最优路径，释放时 `kvfree()` 通过 `is_vmalloc_addr()` 自动判断走 kfree 还是 vfree。适合"大小不确定、可能很大"的分配场景，调用者无需手动判断该用哪种分配器。

**实现方式**：核心函数 `kvmalloc_node()`（`mm/util.c:559-610`）先判断大小：小于等于 PAGE_SIZE 时只走 kmalloc；大于 PAGE_SIZE 时给 kmalloc 加 `__GFP_NOWARN | __GFP_NORETRY` 尝试，失败后回退到 `__vmalloc_node_range()`。不允许阻塞的上下文（`!gfpflags_allow_blocking`）直接返回 NULL，因为 vmalloc 需要可睡眠上下文。`kvfree()`（`mm/util.c:616-628`）通过地址范围检查（`addr >= VMALLOC_START && addr < VMALLOC_END`）自动分派到 kfree 或 vfree。arm64 上 PAGE_SIZE = 4 KiB。

**应用场景**：主要消费者包括 BPF 子系统（`kernel/bpf/syscall.c`、`kernel/bpf/btf.c`，用于 maps 和 BTF 元数据）、netfilter（`net/netfilter/nf_tables_api.c`、`net/netfilter/x_tables.c`，用于规则集）、printk（`kernel/printk/printk.c`，用于日志缓冲区）、zram（`drivers/block/zram/zram_drv.c`，用于压缩设备元数据）、AppArmor（`security/apparmor/policy_unpack.c`，用于策略解包）、XFS scrub（`fs/xfs/scrub/attr.c`）和 Ceph 网络文件系统（`net/ceph/osdmap.c`、`net/ceph/messenger_v2.c`）。

## 主要 API

头文件：`include/linux/slab.h`；实现：`mm/util.c`

| API | 说明 | 备注 |
|-----|------|------|
| `kvmalloc(size, gfp)` | 分配，自动 kmalloc/vmalloc | inline 包装 `kvmalloc_node()` |
| `kvmalloc_node(size, gfp, node)` | 指定 NUMA 节点 | 核心实现 |
| `kvzalloc(size, gfp)` | 分配并清零 | `kvmalloc(size, gfp \| __GFP_ZERO)` |
| `kvzalloc_node(size, gfp, node)` | 清零 + NUMA | |
| `kvmalloc_array(n, size, gfp)` | 数组分配 | 带溢出检查 `check_mul_overflow()` |
| `kvcalloc(n, size, gfp)` | 清零数组 | `kvmalloc_array(n, size, gfp \| __GFP_ZERO)` |
| `kvfree(addr)` | 释放 | 自动判断 kfree/vfree |
| `kvfree_sensitive(addr, len)` | 释放敏感数据 | 先 `memzero_explicit()` 清零再释放 |
| `kvrealloc(p, oldsize, newsize, gfp)` | 调整大小 | 分配新 → 拷贝 → 释放旧 |
| `vmemdup_user(src, len)` | 从用户空间复制 | 用 kvmalloc 分配，返回 `ERR_PTR` |

### RCU 延迟释放

| API | 说明 | 备注 |
|-----|------|------|
| `kvfree_rcu(ptr, rhf)` | 2 参数形式 | `rhf` 是结构体中 `rcu_head` 字段名，偏移须 < 4096 |
| `kvfree_rcu(ptr)` | 1 参数形式 | 不需要嵌入 `rcu_head`，但要求 `might_sleep()` 上下文 |

内部实现：对象批量加入 per-CPU 数组，在 `KFREE_DRAIN_JIFFIES` 后统一释放。批处理失败时回退到 `synchronize_rcu() + kvfree()`。

注意：`kfree_rcu(ptr, rhf)` 现在是 `kvfree_rcu(ptr, rhf)` 的别名（`include/linux/rcupdate.h:1003`）。

## __GFP_ACCOUNT 处理

`__GFP_ACCOUNT`（`include/linux/gfp_types.h:111`，值 `0x400000u`）使分配计入 kmemcg。`kvmalloc_node()` 将原始 flags（含 `__GFP_ACCOUNT`）**不变地**传给 kmalloc 和 vmalloc 两条路径。`GFP_KERNEL_ACCOUNT = GFP_KERNEL | __GFP_ACCOUNT`（`include/linux/slab.h:336`）。

## 使用规模

| API | 调用点数 |
|-----|---------|
| `kvfree` | 1,565 |
| `kvzalloc` | 413 |
| `kvmalloc` | 236 |
| `kvcalloc` | 178 |
| `kvmalloc_array` | 155 |
| `kvzalloc_node` | 32 |
| `kvfree_rcu` | 33 |
| `vmemdup_user` | 19 |
| `kvfree_sensitive` | 15 |
| `kvmalloc_node` | 16 |
| `kvrealloc` | 4 |
