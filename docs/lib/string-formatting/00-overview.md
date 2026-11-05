---
title: 总览
---

# 字符串与输出格式化 — 总结与对比

## §1 一句话定位表

| 模块 | 一句话定位 | 独特价值 | 文档链接 |
|------|-----------|---------|---------|
| **seq_file** | 虚拟文件迭代输出框架 | 自动处理分页和缓冲区溢出，`/proc`/`debugfs` 的标准输出方式 | [seq-file.md](seq-file.md) |
| **seq_buf** | 固定缓冲区的顺序写入 | 无文件绑定的轻量版 seq_file，tracing 内部专用 | [misc-string.md](misc-string.md) |
| **string** | 内核安全字符串操作 | `strscpy` 替代 `strncpy` 防溢出，`sysfs_streq` 容忍尾部换行 | [misc-string.md](misc-string.md) |
| **kasprintf** | 格式化并自动分配内存 | 一步完成"格式化+分配"，`kvasprintf_const` 对常量串零拷贝优化 | [misc-string.md](misc-string.md) |
| **sscanf** | 从字符串解析结构化数据 | 内核版 sscanf，sysfs 写入解析和参数解析的标准工具 | [misc-string.md](misc-string.md) |
| **glob** | Shell 风格通配符匹配 | 唯一 API `glob_match()`，非递归回溯，设备黑名单和模块过滤 | [misc-string.md](misc-string.md) |
| **string_helpers** | 高层格式化/转义/安全日志 | 人类可读大小格式化（kB/MiB），安全日志防注入 | [misc-string.md](misc-string.md) |
| **vsprintf** | 内核 printf 格式串引擎 | 所有格式化模块的底层基础，提供 `%pI4`、`%pE` 等内核扩展格式 | [misc-string.md](misc-string.md) |

---

## §2 选型决策

```
需要向用户态输出文本？
├── 是，通过 /proc 或 debugfs 文件
│   ├── 数据是固定的一段文本（不需要迭代）
│   │   └── → single_open + DEFINE_SHOW_ATTRIBUTE
│   └── 数据是列表/需要迭代
│       └── → 实现 seq_operations + DEFINE_SEQ_ATTRIBUTE
│
├── 是，但不是文件（tracing / printk 缓冲 / 自定义传输）
│   └── → seq_buf（手动管理缓冲区，无文件绑定）
│
└── 否，是内核内部字符串操作
    ├── 需要拷贝/比较字符串
    │   └── → strscpy / sysfs_streq / kstrdup / kstrdup_const
    │
    ├── 需要"格式化 + 分配"一步完成
    │   ├── 格式串是常量或仅 %s → kasprintf（kvasprintf_const 自动优化）
    │   └── 需要生成 "prefix0"..."prefixN-1" → kasprintf_strarray
    │
    ├── 需要从字符串解析数据
    │   └── → sscanf（sysfs 写入、参数解析）
    │
    ├── 需要做通配符匹配
    │   └── → glob_match（设备黑名单、模块过滤）
    │
    ├── 需要格式化人类可读大小
    │   └── → string_get_size（STRING_UNITS_10 或 STRING_UNITS_2）
    │
    └── 需要安全日志输出（防注入）
        └── → kstrdup_quotable / kstrdup_quotable_cmdline
```

---

## §3 核心对比表

### 3.1 输出类模块对比

| 模块 | 输出目标 | 溢出处理 | 自动扩容 | 文件绑定 | 典型用途 |
|------|---------|---------|---------|---------|---------|
| seq_file | `/proc`/`debugfs` 文件 | 自动翻倍扩容 + 从头重放 | 是 | 是 | 虚拟文件迭代输出 |
| seq_buf | 固定内存缓冲区 | 标记溢出，后续写入返回 -1 | 否 | 否 | tracing 内部格式化 |
| snprintf | 固定缓冲区 | 截断 | 否 | 否 | 安全格式化到固定缓冲 |
| kasprintf | 动态分配内存 | 分配足够大 | 是（分配） | 否 | 动态构造字符串 |

### 3.2 字符串操作类对比

| 模块 | 功能 | 安全性 | 返回值 | 典型用途 |
|------|------|--------|--------|---------|
| strscpy | 安全拷贝 | 返回 `-E2BIG` 表示溢出 | 拷贝长度或负错误 | 替代 strncpy |
| sysfs_streq | 字符串比较 | 容忍尾部换行 | bool | sysfs 属性比较 |
| kstrdup | 动态拷贝 | 分配失败返回 NULL | 新字符串指针 | 通用字符串拷贝 |
| kstrdup_const | 常量拷贝 | rodata 字符串零拷贝 | 新字符串指针或原指针 | 优化常量字符串 |
| kstrdup_quotable | 安全日志拷贝 | 转义不可打印字符 | 新字符串指针 | 安全日志防注入 |

### 3.3 解析/匹配类对比

| 模块 | 方向 | 功能 | 限制 | 典型用途 |
|------|------|------|------|---------|
| sscanf | 字符串→结构化数据 | 解析格式化字符串 | 不支持浮点，`%c` 行为略有不同 | sysfs 写入解析 |
| glob | 字符串→bool | Shell 风格通配符匹配 | 仅 `*` `?` `[abc]` `[!abc]` `[a-z]` `\x` | 设备黑名单 |
| string_helpers | 结构化数据→字符串 | 人类可读格式化 | 缓冲区需 ≥9 字节 | 大小格式化、安全日志 |

---

## §4 使用规模

以下数据来自对内核源码树的实际 grep 统计：

| 模块 | grep 命令 | 匹配数 |
|------|----------|--------|
| seq_file | `grep -rw 'seq_file\|seq_printf\|seq_puts\|seq_write\|seq_open\|single_open\|seq_release\|seq_list\|DEFINE_SHOW_ATTRIBUTE\|seq_operations' --include='*.c' --include='*.h' . \| wc -l` | 21479 |
| seq_buf | `grep -rw 'seq_buf\|seq_buf_printf\|seq_buf_putmem\|seq_buf_to_user\|seq_buf_has_overflowed' --include='*.c' --include='*.h' . \| wc -l` | 177 |
| string（strscpy 等） | `grep -rw 'strscpy\|strlcpy\|strscpy_pad\|sysfs_streq\|kstrdup\|kstrndup\|kstrdup_const\|strreplace\|strim\|skip_spaces\|match_string\|sysfs_emit' --include='*.c' --include='*.h' . \| wc -l` | 6635 |
| kasprintf | `grep -rw 'kasprintf\|kvasprintf\|kasprintf_strarray\|kvasprintf_const' --include='*.c' --include='*.h' . \| wc -l` | 607 |
| sscanf | `grep -rw 'sscanf\|vsscanf' --include='*.c' --include='*.h' . \| wc -l` | 1138 |
| glob | `grep -rw 'glob_match' --include='*.c' --include='*.h' . \| wc -l` | 31 |
| string_helpers | `grep -rw 'string_get_size\|kstrdup_quotable\|string_escape_mem\|string_escape_mem_ascii\|string_escape_mem_irq' --include='*.c' --include='*.h' . \| wc -l` | 44 |
| vsprintf | `grep -rw 'vsnprintf\|snprintf\|sprintf\|printk\|vsprintf' --include='*.c' --include='*.h' . \| wc -l` | 38915 |

---

## §5 设计模式速览

### 5.1 溢出处理策略

三个输出模块对缓冲区溢出的处理各不相同，这是选型的关键区分点：

| 模块 | 溢出策略 | 调用者需要做什么 |
|------|---------|----------------|
| seq_file | 自动翻倍扩容 + 从头重放 | 什么都不做——框架自动处理 |
| seq_buf | 标记溢出，后续写入返回 -1 | 检查 `seq_buf_has_overflowed()`，分配更大缓冲区重试 |
| snprintf/kasprintf | 截断（snprintf）或分配足够大（kasprintf） | snprintf 需检查返回值是否 ≥ size |

### 5.2 printf 格式串共享

seq_file（`seq_printf`）、seq_buf（`seq_buf_printf`）、kasprintf（`kasprintf`）底层都调用 `vsnprintf`（`lib/vsprintf.c`），共享同一套内核 printf 格式说明符（`%pI4`、`%pE`、`%pOF` 等内核扩展格式）。

### 5.3 与 sysfs 的协作

- **输入方向**：sysfs `store` 回调收到字符串 → 用 `sscanf` 或 `kstrtoint`/`kstrtoul` 解析
- **输出方向**：sysfs `show` 回调 → 用 `sysfs_emit` 或 `sysfs_emit_at` 写入（内部基于 `vsnprintf`，保证不溢出 PAGE_SIZE）
- **字符串比较**：用 `sysfs_streq` 容忍用户态写入时附带的 `\n`

### 5.4 引用与释放配对

| 分配 | 释放 |
|------|------|
| `kasprintf` / `kvasprintf` | `kfree` |
| `kvasprintf_const` / `kstrdup_const` | `kfree_const` |
| `kasprintf_strarray` | `kfree_strarray` |
| `kstrdup_quotable*` | `kfree` |

---

## §6 锁模型对比

本分类不涉及锁模型。字符串操作模块均为无状态函数或每次调用独立分配，不需要并发保护。

---

## §7 组合使用模式

### 7.1 seq_file + list_head（/proc 文件输出）

`fs/proc/` 下的文件大量使用 `seq_file` 框架遍历 `list_head` 链表输出信息。`start` 回调定位链表头，`next` 回调推进到下一个节点，`show` 回调用 `seq_printf` 格式化输出。

### 7.2 seq_file + rbtree（有序输出）

当需要按 key 顺序输出时，`seq_file` 配合 rbtree 使用。`start` 回调取 `rb_first()`，`next` 回调取 `rb_next()`，保证输出按 key 排序。

### 7.3 kasprintf + 设备模型

设备模型中大量使用 `kasprintf` 动态构造设备名、路径。例如 `kasprintf(GFP_KERNEL, "%s:%d", name, id)` 构造唯一设备标识。

### 7.4 sscanf + sysfs（属性写入解析）

sysfs `store` 回调收到用户写入的字符串，用 `sscanf(buf, "%d", &value)` 或 `kstrtoint(buf, 0, &value)` 解析为数值。这是 sysfs 属性写入的标准模式。

### 7.5 strscpy + 驱动配置

驱动初始化时从设备树或配置结构拷贝字符串，用 `strscpy(dest, src, sizeof(dest))` 保证不溢出。返回值检查 `-E2BIG` 判断是否截断。

### 7.6 string_helpers + 日志输出

驱动日志中需要输出人类可读的大小时，用 `string_get_size(size, STRING_UNITS_2, buf, sizeof(buf))` 格式化为 "1.5 MiB" 形式。安全日志用 `kstrdup_quotable` 防止注入。

### 7.7 glob + 模块参数

模块参数中需要过滤设备时，用 `glob_match(pattern, device_name)` 做通配符匹配。例如 `glob_match("pci-0000:*", dev_name)` 匹配 PCI 设备。

---

## §8 阅读顺序建议

### 8.1 入门路径（理解内核字符串处理）

1. **string** → 理解 `strscpy` 等安全字符串操作
2. **seq_file** → 理解虚拟文件输出框架
3. **kasprintf** → 理解动态分配格式化

### 8.2 需要向用户态输出 → seq_file → seq_buf

先学 seq_file（文件输出标准框架），再看 seq_buf（无文件绑定的轻量版）。seq_buf 主要用于 tracing 内部。

### 8.3 需要字符串操作 → string → kasprintf

先学 `strscpy` 等安全操作，再看 `kasprintf` 动态分配。`kstrdup_const` 的零拷贝优化在性能敏感路径很有价值。

### 8.4 需要解析用户输入 → sscanf → string_helpers

先学 `sscanf` 解析数值，再看 `string_helpers` 的高层工具。`string_get_size` 在日志输出中很实用。

### 8.5 需要模式匹配 → glob

`glob_match` 只有一个 API，使用简单。主要用于设备黑名单和模块参数过滤。

---

## §9 容易混淆的关系

| 容易混淆的对 | 区别要点 |
|-------------|----------|
| seq_file vs seq_buf | seq_file 绑定文件操作，自动扩容；seq_buf 无文件绑定，手动管理 |
| strscpy vs strncpy | strscpy 返回 `-E2BIG` 表示溢出；strncpy 用 `\0` 填充剩余空间（不安全） |
| kasprintf vs snprintf | kasprintf 自动分配足够大；snprintf 截断到固定大小 |
| kstrdup vs kstrdup_const | kstrdup 总是分配新内存；kstrdup_const 对 rodata 字符串返回原指针 |
| sscanf vs kstrtoint | sscanf 支持多字段解析；kstrtoint 只解析单个数值（更安全） |
| string_helpers vs string | string_helpers 提供高层工具（大小格式化、安全日志）；string 提供基础操作 |

---

## §10 文档索引

| 文档 | 覆盖模块 |
|------|---------|
| [seq-file.md](seq-file.md) | seq_file 框架：数据结构、四回调模型、缓冲区溢出与自动扩容、single_open、便捷宏、输出函数、链表迭代辅助 |
| [misc-string.md](misc-string.md) | string（内核字符串操作）、kasprintf（动态分配格式化）、sscanf（内核 sscanf）、glob（模式匹配）、string_helpers（高层工具）、seq_buf（底层顺序缓冲区） |
