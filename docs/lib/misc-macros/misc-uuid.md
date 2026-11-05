# UUID / GUID

> B 级文档 · 头文件 `include/linux/uuid.h`（106 行）· 实现 `lib/uuid.c`（133 行）

## UUID / GUID

### 定位

**功能**：提供 UUID/GUID（128 位全局唯一标识符）的统一类型定义和操作函数。内核中大量子系统需要标识唯一实体：EFI 分区表、ACPI 表、设备树节点、文件系统超级块、网络协议会话等。

**实现方式**：`uuid_t` 和 `guid_t` 在内存布局上等价（16 字节数组），区别在于字节序解释——`uuid_t` 按大端（网络序/RFC 4122），`guid_t` 按混合序（前 3 段小端/EFI/ACPI）。`lib/uuid.c` 维护两张字节重排表（`uuid_index[16]`/`guid_index[16]`），`uuid_parse`/`guid_parse` 据此把字符串按正确字节序填入数组。`uuid_gen` 生成 v4 随机 UUID，底层走 `get_random_bytes`。

**应用场景**：EFI 子系统（分区表 GUID、协议 GUID，用 `guid_t`）、设备树（节点 compatible 匹配，用 `uuid_t`）、文件系统（ext4/btrfs 超级块 UUID）、网络协议（NFS filehandle、iSCSI session ID）、安全子系统（IMA 策略标识）等。混用 `uuid_t` 和 `guid_t` 会导致字节序错误。

### 类型体系

```c
typedef struct {
    __u8 b[UUID_SIZE];      /* UUID_SIZE = 16 */
} uuid_t;
```

`guid_t` 定义在 `include/uapi/linux/uuid.h`，结构完全相同（16 字节数组）。两者在内存布局上等价，区别在于**字节序解释**：

| 类型 | 字节序 | 典型来源 |
|------|--------|----------|
| `uuid_t` | 大端（网络序） | RFC 4122 标准、设备树 |
| `guid_t` | 混合序（前 3 段小端） | EFI/ACPI、Windows |

`guid_index[16]` 和 `uuid_index[16]`（`lib/uuid.c`）是两张字节重排表，`uuid_parse` / `guid_parse` 据此把字符串按正确字节序填入数组。

### 核心 API

头文件：`include/linux/uuid.h`

| API | 说明 | 备注 |
|-----|------|------|
| `uuid_gen(u)` | 生成随机 UUID（v4） | 内部调用 `generate_random_uuid` |
| `guid_gen(u)` | 生成随机 GUID | 同上 |
| `uuid_parse(str, u)` | 解析字符串 "xxxxxxxx-xxxx-..." 到 uuid_t | 返回 0 成功，-EINVAL 格式错 |
| `guid_parse(str, u)` | 解析字符串到 guid_t | 同上 |
| `uuid_is_valid(str)` | 检查字符串是否为合法 UUID 格式 | `__must_check` |
| `uuid_equal(u1, u2)` | 比较两个 uuid_t 是否相等 | `memcmp` 实现 |
| `guid_equal(g1, g2)` | 比较两个 guid_t 是否相等 | 同上 |
| `uuid_copy(dst, src)` | 拷贝 uuid_t | `memcpy` |
| `guid_copy(dst, src)` | 拷贝 guid_t | 同上 |
| `uuid_is_null(u)` | 判断是否全零 | 与 `uuid_null` 比较 |
| `guid_is_null(g)` | 判断是否全零 | 同上 |
| `import_uuid(dst, src)` | 从原始字节数组导入 | `memcpy`，16 字节 |
| `export_uuid(dst, src)` | 导出到原始字节数组 | 同上 |
| `UUID_INIT(a,b,c,d0..d7)` | 编译期构造 uuid_t 常量 | 宏，处理字节序 |

### 常量与长度

| 常量 | 值 | 说明 |
|------|-----|------|
| `UUID_SIZE` | 16 | UUID 字节数 |
| `UUID_STRING_LEN` | 36 | 字符串长度（不含 NUL）：`"xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"` |
| `uuid_null` | 全零 | 外部常量 |
| `guid_null` | 全零 | 外部常量 |

### 使用模式

```c
uuid_t id;
uuid_gen(&id);                          /* 生成随机 UUID */

uuid_t parsed;
if (uuid_parse("550e8400-e29b-41d4-a716-446655440000", &parsed))
    return -EINVAL;                     /* 解析失败 */

if (uuid_equal(&id, &parsed))
    pr_info("match\n");
```

### 注意事项

- **`uuid_le_cmp` 已废弃**：头文件标注 "backwards compatibility, don't use in new code"（`:100`），新代码用 `uuid_equal`
- **uuid_t vs guid_t**：混用两者会导致字节序错误。EFI 子系统用 `guid_t`，设备树 / 网络用 `uuid_t`
- **随机性来源**：`uuid_gen` 最终走 `get_random_bytes`，依赖 CRNG 初始化完成
