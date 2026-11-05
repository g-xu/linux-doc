# 字符串与格式化辅助工具

> 六个与字符串/格式化相关的 B 级模块：string（内核专用字符串操作）、kasprintf（动态分配格式化）、sscanf（内核 sscanf）、glob（模式匹配）、string_helpers（高层字符串工具）、seq_buf（底层顺序缓冲区）。

---

## string — 内核专用字符串操作

### 定位

**功能**：提供 C 标准库字符串函数的内核版本（`memcpy`、`strlen`、`strcmp` 等），以及若干内核特有的安全替代函数。核心亮点是 `strscpy`——替代有溢出风险的 `strncpy`，保证 NUL 终止并返回精确拷贝长度。`lib/string_helpers.c` 则提供更高层的辅助函数（大小格式化、安全日志转义等）。

**实现方式**：`lib/string.c` 实现基础字符串操作，部分函数有 arch 优化版本（`arch/arm64/lib/` 下的汇编实现）。`strscpy`（`lib/string.c`）按 word 对齐逐块拷贝并逐字节检查 NUL，溢出时返回 `-E2BIG`。`string_helpers.c` 中的 `match_string` 线性扫描数组查找匹配，`sysfs_streq` 忽略尾部换行/空格以适配 sysfs 写入习惯。`kstrdup_const` 检查源字符串是否在 `__rodata` 段，如果是则直接返回指针避免拷贝。

**应用场景**：`strscpy` 全树 2,714 处调用，是内核中最常用的字符串拷贝函数，遍布所有子系统。`match_string` 广泛用于枚举值解析（如 regulator 模式选择、GPIO flag 解析）。`kstrdup`/`kstrdup_const` 用于动态复制字符串到堆上。`strim`/`skip_spaces` 在 sysfs 属性 store 回调和内核参数解析中频繁使用。

### 内核特有 API

头文件：`include/linux/string.h`、`include/linux/string_helpers.h`。实现：`lib/string.c`、`lib/string_helpers.c`。

| API | 说明 | 备注 |
|-----|------|------|
| `strscpy(dst, src, count)` | 安全字符串拷贝 | 返回拷贝长度；溢出返回 `-E2BIG`；保证 NUL 终止。替代 `strncpy` |
| `strscpy_pad(dst, src, count)` | strscpy + 尾部补零 | 防止信息泄漏到用户态 |
| `skip_spaces(str)` | 跳过前导空白 | 返回第一个非空白字符指针；`lib/string_helpers.c:851` |
| `strim(s)` | 去除前后空白 | 就地修改，返回去除前导空白后的起点；`lib/string_helpers.c:867` |
| `strreplace(s, old, new)` | 替换所有匹配字符 | 就地替换，返回 s；`lib/string_helpers.c:988` |
| `match_string(array, n, string)` | 在数组中查找匹配字符串 | 返回索引或 `-EINVAL`；`lib/string_helpers.c:929` |
| `sysfs_match_string(array, string)` | 自动计算数组大小的版本 | 宏，内部用 `ARRAY_SIZE` |
| `__sysfs_match_string(array, n, s)` | 用 `sysfs_streq` 比较 | 容忍尾部换行 |
| `sysfs_streq(s1, s2)` | 忽略尾部换行/空格的比较 | sysfs 写入常带 `\n`；`lib/string_helpers.c:895` |
| `kstrdup(s, gfp)` | kmalloc + strcpy | |
| `kstrdup_const(s, gfp)` | 如果 s 在 rodata 则直接返回指针 | 配合 `kfree_const()` 使用 |
| `kfree_strarray(array, n)` | 释放 `kasprintf_strarray` 分配的数组 | |

### 使用规模

`strscpy` 2,714 处调用，是内核中最常用的字符串拷贝函数。

---

## kasprintf — 动态分配格式化

### 定位

**功能**：提供 printf 风格格式化并自动分配内存的函数，省去"先算长度再 kmalloc 再 snprintf"的三步操作。驱动和子系统代码中需要动态构造字符串（设备名、路径、调试信息等）时，`kasprintf` 一步完成格式化 + 分配。

**实现方式**：`kvasprintf`（`lib/kasprintf.c:15-34`）两次调用 `vsnprintf`——第一次传 NULL 缓冲区计算所需长度，第二次写入 `kmalloc` 分配的缓冲区。有 `WARN` 检查两次返回值不一致（理论上不应发生）。`kvasprintf_const`（`lib/kasprintf.c:43-50`）对常量格式串做优化：如果格式串不含 `%` 或仅含 `%s`，用 `kstrdup_const` 直接引用 rodata 段字符串，避免运行时分配。返回值统一用 `kfree_const()` 释放。`kasprintf_strarray` 批量生成 `"prefix0"` … `"prefixN-1"` 字符串数组。

**应用场景**：全树 571 处调用。广泛用于设备名构造（如 `devm_kasprintf` 在 probe 中构造中断名字）、debugfs 路径拼接、错误消息格式化、kobject 名字动态生成等场景。`kvasprintf_const` 在静态初始化器路径中避免不必要的堆分配。

### 主要 API

| API | 说明 | 备注 |
|-----|------|------|
| `kasprintf(gfp, fmt, ...)` | 格式化并 kmalloc 返回 | 返回 NULL 表示分配失败；调用者 `kfree` |
| `kvasprintf(gfp, fmt, ap)` | va_list 版本 | |
| `kvasprintf_const(gfp, fmt, ap)` | 常量优化版本 | 无 `%` 或仅 `%s` 时用 `kstrdup_const` 避免分配 |
| `kasprintf_strarray(gfp, prefix, n)` | 生成 `"prefix0"` … `"prefixN-1"` 数组 | 配合 `kfree_strarray` 释放 |

### 实现要点

`kvasprintf`（`lib/kasprintf.c:15-34`）：两次调用 `vsnprintf`——第一次 `NULL` 缓冲区算长度，第二次写入 `kmalloc` 分配的缓冲区。有 `WARN` 检查两次返回值不一致（理论上不应发生）。

`kvasprintf_const`（`lib/kasprintf.c:43-50`）：如果格式串不含 `%`，用 `kstrdup_const` 直接引用 rodata 段字符串，避免运行时分配。返回值统一用 `kfree_const()` 释放。

### 使用规模

571 处调用。

---

## sscanf — 内核 sscanf

### 定位

**功能**：提供内核版 `sscanf`，从字符串中按格式符解析整数、字符、字符串等基本类型。主要用于解析 sysfs 属性写入值、内核命令行参数、设备树属性中的文本数据。不支持浮点（`%f`/`%e` 不可用），但支持 `%d`/`%u`/`%x`/`%o` 及 `hh`/`h`/`l`/`ll`/`z` 长度修饰符。

**实现方式**：`sscanf` 入口在 `lib/vsprintf.c:3698`，核心解析逻辑在同文件的 `vsscanf` 函数中。逐字符扫描格式串，对每个转换说明从输入串中匹配并解析。字符类 `[...]` 在 6.1.y 中支持 `a-b` 范围语法（`lib/vsprintf.c:3558-3589`），使用 bitmap 做字符集匹配。`%s` 匹配非空白字符序列，`%n` 记录已消费字符数。返回成功解析的项数，与 libc sscanf 语义一致。

**应用场景**：全树 1,128 处调用。主要消费者包括：sysfs 属性 store 回调（解析用户写入的数值/字符串）、安全子系统（SELinux `security/selinux/selinuxfs.c`、Smack、TOMOYO 的策略解析）、驱动配置解析（如 `drivers/ata/libata-core.c` 的参数解析）、以及 `kernel/trace/` 下 tracing 事件过滤表达式的解析。

### 与 libc sscanf 的差异

- **不支持浮点**：`%f`、`%e`、`%g` 等不可用
- **不支持 `%p` 以外的指针格式**：不能解析任意指针
- **字符类 `[` 不支持范围简写**：`[a-z]` 中的 `-` 实际被当作字面字符处理——但在 6.1.y 中已支持 `a-b` 范围语法（`lib/vsprintf.c:3558-3589`），使用 bitmap 做字符集匹配
- **支持的整数转换**：`%d`、`%u`、`%i`、`%o`、`%x`、`%X`，配合 `hh`/`h`/`l`/`ll`/`z` 长度修饰符
- **`%s`**：匹配非空白字符序列
- **`%c`**：匹配指定宽度字符
- **`%n`**：记录已消费字符数

### 主要 API

| API | 说明 | 备注 |
|-----|------|------|
| `sscanf(buf, fmt, ...)` | 从 buf 解析 | 返回成功解析的项数 |
| `vsscanf(buf, fmt, args)` | va_list 版本 | |

### 使用规模

1,128 处调用。广泛用于 sysfs 属性写入解析、内核参数解析、驱动配置解析。

---

## glob — Shell 风格模式匹配

### 定位

**功能**：提供 shell 风格通配符匹配，支持 `?`（单字符）、`*`（任意长度）、`[abc]`（字符类）、`[!abc]`（否定字符类）、`[a-z]`（字符范围）、`\x`（转义）。等价于 `!fnmatch(pat, str, 0)`，但实现更简单且无外部依赖。

**实现方式**：`lib/glob.c` 的 `glob_match()` 用非递归回溯处理 `*`——遇到 `*` 时记录回溯点，后续匹配失败则回到最近的 `*` 继续尝试，最坏情况 O(strlen(str) × strlen(pat))。尾部 `*` 有快速路径优化（`lib/glob.c:65-66`），模式以 `*` 结尾时直接返回 true。不对 `/` 和 `.` 做特殊处理——它不是路径名匹配器，只做纯字符串匹配。函数标记为 `__pure`，无副作用。

**应用场景**：全树 31 处调用。主要用于设备黑名单匹配（如 `drivers/ata/libata-core.c` 中按型号通配排除）、模块参数过滤（`kernel/trace/ftrace.c` 中按函数名通配选择追踪目标）、KUnit 测试选择器（`lib/kunit/executor.c` 按名字模式筛选测试用例）。使用量小但场景独特——需要通配符匹配时它是内核中唯一的选择。

### 支持的元字符

| 元字符 | 含义 |
|--------|------|
| `?` | 匹配任意单个非 NUL 字符 |
| `*` | 匹配任意长度字符串（含空串） |
| `[abc]` | 字符类 |
| `[!abc]` | 否定字符类（注意是 `!` 不是 `^`） |
| `[a-z]` | 字符范围 |
| `\x` | 转义特殊字符 |

### 实现特点

- **非递归**：用回溯（backtrack）处理 `*`，最坏情况 O(strlen(str) × strlen(pat)))
- **不预处理模式**：每次匹配线性扫描模式串
- **不对 `/` 和 `.` 做特殊处理**：不是路径名匹配器
- **尾部 `*` 优化**：模式以 `*` 结尾时直接返回 true（`lib/glob.c:65-66`）

### 主要 API

| API | 说明 | 备注 |
|-----|------|------|
| `glob_match(pat, str)` | 全串匹配 | 返回 bool；`__pure` 标记 |

### 使用规模

31 处调用。主要用于设备黑名单匹配、模块参数过滤。

---

## string_helpers — 高层字符串工具

### 定位

**功能**：提供比 `string.c` 更高层的格式化/转义/安全日志辅助函数。核心能力包括：将字节数格式化为人类可读字符串（`string_get_size`）、转义内存区域中的特殊字符（`string_escape_mem`）、以及安全日志输出（`kstrdup_quotable*` 系列，防止不可打印字符注入日志）。

**实现方式**：`string_get_size`（`lib/string_helpers.c`）根据 `STRING_UNITS_10`（SI：kB/MB/GB，1000 进制）或 `STRING_UNITS_2`（IEC：KiB/MiB/GiB，1024 进制）做除法 + 取余，输出到调用者提供的缓冲区（需 ≥9 字节）。`string_escape_mem` 按 flags 控制转义风格（八进制/十六进制/特定字符集），用于将原始数据转为安全可打印形式。`kstrdup_quotable` 系列（`lib/string_helpers.c`）复制字符串并转义不可打印字符，`kstrdup_quotable_cmdline` 安全获取进程命令行，`kstrdup_quotable_file` 安全获取文件路径——这些函数保证输出不含控制字符，防止日志注入攻击。

**应用场景**：`string_get_size` 全树 24 处调用，用于块设备（`drivers/block/virtio_blk.c`、`drivers/mmc/core/block.c`、`drivers/scsi/sd.c`）和存储子系统的容量显示。`string_escape_mem` 被 `seq_escape_mem` 等内部使用，在 tracing 输出中转义二进制数据。`kstrdup_quotable*` 用于安全敏感日志（如 SELinux audit），防止恶意构造的文件名或命令行注入内核日志。

### 主要 API

| API | 说明 | 备注 |
|-----|------|------|
| `string_get_size(size, blk_size, units, buf, len)` | 将大小格式化为人类可读字符串 | 支持 SI（kB/MB）和 IEC（KiB/MiB）；buf 需 ≥9 字节 |
| `string_escape_mem(src, isz, dst, osz, flags, esc)` | 转义内存区域中的特殊字符 | flags 控制转义风格（八进制/十六进制等） |
| `string_unescape(src, dst, size, flags)` | 反转义 | |
| `kstrdup_quotable(src, gfp)` | 复制字符串并转义不可打印字符 | 用于安全日志输出 |
| `kstrdup_quotable_cmdline(task, gfp)` | 安全获取进程命令行 | |
| `kstrdup_quotable_file(file, gfp)` | 安全获取文件路径 | |
| `parse_int_array_user(from, count, array)` | 从用户态解析整数数组 | |
| `kasprintf_strarray(gfp, prefix, n)` | 生成带前缀的字符串数组 | 见 kasprintf 节 |

### string_get_size 单位

```c
enum string_size_units {
    STRING_UNITS_10,   // SI: kB, MB, GB（1000 进制）
    STRING_UNITS_2,    // IEC: KiB, MiB, GiB（1024 进制）
};
```

### 使用规模

`string_get_size` 24 处调用。`string_escape_mem` 被 `seq_escape_mem` 等内部使用。`kstrdup_quotable*` 用于安全敏感日志。

---

## seq_buf — 底层顺序缓冲区

### 定位

**功能**：提供底层顺序写入缓冲区，用于在内核中逐步构造格式化输出。与 seq_file 共享"顺序写入"概念，但**没有回调模型、不自动扩容、不绑定文件操作**——调用者提供固定大小的缓冲区，直接往里写入内容。主要用于 tracing 子系统内部格式化，以及需要把格式化输出拷贝到用户态但不想走 seq_file 回调模型的场景。

**实现方式**：`struct seq_buf`（`include/linux/seq_buf.h:19-24`）包含 `buffer`（调用者提供的缓冲区）、`size`（缓冲区大小）、`len`（已写入长度）、`readpos`（读取位置）。写入函数（`seq_buf_printf`、`seq_buf_puts`、`seq_buf_putmem` 等）调用 `vsnprintf` 写入剩余空间，溢出时将 `len` 设为 `size + 1` 标记溢出状态，后续写入返回 -1。`seq_buf_to_user()` 从 `readpos` 开始拷贝到用户态缓冲区。`seq_buf_print_seq()` 桥接到 seq_file，把 seq_buf 内容写入 seq_file 的输出。`seq_buf_terminate()` 确保 NUL 终止（溢出时截断最后一个字节）。

**应用场景**：`seq_buf_printf` 全树 69 处调用。主要用户是 `kernel/trace/` 下的 tracing 代码（如 `trace_output.c` 中格式化 trace 事件输出），以及 `arch/powerpc/kernel/` 下的平台特定日志构造。相比 seq_file，seq_buf 适合"一次性构造 + 单次拷贝"的场景，不需要 start/next/stop/show 回调模型的迭代语义。

### 核心结构体（`include/linux/seq_buf.h:19-24`）

```c
struct seq_buf {
    char    *buffer;    // 调用者提供的缓冲区
    size_t   size;      // 缓冲区大小
    size_t   len;       // 已写入长度（溢出时设为 size+1）
    loff_t   readpos;   // 读取位置（用于 seq_buf_to_user）
};
```

### 与 seq_file 的对比

| | seq_file | seq_buf |
|---|---------|---------|
| 缓冲区 | 自动 kvmalloc + 翻倍扩容 | 调用者提供固定缓冲区 |
| 迭代模型 | start/next/stop/show 回调 | 无——直接写入 |
| 溢出行为 | 自动重试 | 标记溢出（`len = size + 1`），返回 -1 |
| 用户态接口 | read/lseek/release | `seq_buf_to_user` 单次拷贝 |
| 典型用户 | /proc、debugfs | tracing、printk 格式化 |

### 主要 API

| API | 说明 | 备注 |
|-----|------|------|
| `seq_buf_init(s, buf, size)` | 初始化 | 绑定调用者提供的缓冲区 |
| `seq_buf_clear(s)` | 清零 len 和 readpos | 可重新使用 |
| `seq_buf_printf(s, fmt, ...)` | printf 格式化写入 | 溢出返回 -1 |
| `seq_buf_vprintf(s, fmt, args)` | va_list 版本 | |
| `seq_buf_puts(s, str)` | 写入字符串 | 含尾部 NUL（但不计入 len） |
| `seq_buf_putc(s, c)` | 写入单字符 | |
| `seq_buf_putmem(s, mem, len)` | 写入原始数据 | |
| `seq_buf_putmem_hex(s, mem, len)` | 十六进制写入 | 每次最多 8 字节一组 |
| `seq_buf_path(s, path, esc)` | 写入路径名 | |
| `seq_buf_hex_dump(s, ...)` | 格式化十六进制转储 | |
| `seq_buf_to_user(s, ubuf, cnt)` | 拷贝到用户态 | 从 readpos 开始 |
| `seq_buf_print_seq(m, s)` | 将 seq_buf 内容写入 seq_file | 桥接两者 |
| `seq_buf_has_overflowed(s)` | 检查是否溢出 | `len > size` |
| `seq_buf_terminate(s)` | 确保 NUL 终止 | 溢出时截断最后一个字节 |

### 使用规模

`seq_buf_printf` 69 处调用。主要用户是 `kernel/trace/` 下的 tracing 代码。
