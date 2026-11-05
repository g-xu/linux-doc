## 文件系统
Linux内核的文件系统模块是操作系统中最基础、最直观的抽象层。Linux 遵循 Unix 的哲学 **“一切皆文件” (Everything is a file)**，因此文件系统不仅管理磁盘上的数据，还管理设备驱动、网络套接字、管道以及内核状态信息（如 `/proc` 和 `/sys`）。

核心概念是 **VFS (Virtual File System，虚拟文件系统)**。它提供了一个统一的接口，使得 Linux 可以同时支持数十种不同的文件系统（如 Ext4, XFS, NFS, FAT32），而用户空间的应用程序无需关心底层的具体实现。

以下从**架构分层**、**核心对象**、**缓存机制**和**工作流程**四个维度详细介绍。

---

### 1. 架构分层 (Architecture)

Linux 文件系统采用分层架构，从上到下依次为：

| 层级 | 名称 | 描述 |
| :--- | :--- | :--- |
| **L1** | **用户空间 (User Space)** | 应用程序调用标准库或系统调用（如 `open`, `read`, `write`, `stat`）。 |
| **L2** | **系统调用接口 (SCI)** | 内核处理系统调用，进行参数检查，将控制权交给 VFS。 |
| **L3** | **虚拟文件系统 (VFS)** | **核心层**。它定义了通用的文件模型。无论底层是 Ext4 还是 NFS，VFS 都向上传递一致的视图。 |
| **L4** | **具体文件系统 (Concrete FS)** | 实际的驱动实现（如 `ext4.ko`, `xfs.ko`, `nfs.ko`）。它们负责将 VFS 的通用指令翻译成操作磁盘的具体逻辑。 |
| **L5** | **页缓存 (Page Cache)** | 用于加速读写的内存缓存层。文件数据首先读写到 RAM 中的 Page Cache，再同步到磁盘。 |
| **L6** | **块设备层 (Block Layer)** | 处理 IO 调度，将逻辑块请求发送给具体的存储设备驱动（如我们之前讨论的 NVMe 或 SATA 驱动）。 |

---

### 2. VFS 的四大核心对象 (The Big Four Objects)

VFS 为了统一管理文件系统，定义了四个核心的数据结构。理解这四个结构是理解 Linux 文件系统的关键。

#### A. 超级块 (`struct super_block`)
*   **代表什么**: 代表一个 **已挂载的文件系统**（Mounted Filesystem）。
*   **包含信息**: 文件系统的元数据，如块大小、最大文件大小、文件系统类型（Ext4/XFS）、根目录的 inode 等。
*   **生命周期**: 文件系统挂载时创建，卸载时销毁。

#### B. 索引节点 (`struct inode`)
*   **代表什么**: 代表磁盘上的 **一个具体文件**（File）。
*   **包含信息**: 文件的元数据（权限 `rwx`、所有者 `uid/gid`、文件大小、时间戳）。**最重要的是，它包含指向文件数据块在磁盘上位置的指针（映射图）。**
*   **注意**: `inode` **不包含文件名**。文件名是目录项管理的。
*   **唯一性**: 在一个文件系统中，inode 号是唯一的。

#### C. 目录项 (`struct dentry`)
*   **代表什么**: 代表路径中的 **一个组成部分**（Directory Entry）。
*   **功能**: 连接 **文件名** 和 **inode**。建立了文件系统的目录树结构。
*   **举例**: 对于路径 `/home/user/file.txt`，内核会创建 4 个 dentry 对象：`/` (根), `home`, `user`, `file.txt`。
*   **缓存**: 目录项会被积极地缓存在 **dentry cache (dcache)** 中，以加速路径查找。

#### D. 文件对象 (`struct file`)
*   **代表什么**: 代表进程 **打开的一个文件**。
*   **包含信息**: 当前读写位置（File Pointer / Offset）、访问模式（只读/读写）、指向对应 `dentry` 的指针。
*   **生命周期**: 进程调用 `open()` 时创建，调用 `close()` 时销毁。这是进程级别的概念，磁盘上不存在这个对象。

---

### 3. 三大操作方法集 (Operations Structures)

VFS 定义了接口（纯虚函数表），具体文件系统必须实现这些接口。

1.  **`super_operations`**: 针对文件系统的操作。
    *   `alloc_inode`: 分配一个新的 inode。
    *   `write_inode`: 将 inode 元数据刷回磁盘。
    *   `sync_fs`: 同步文件系统数据。
2.  **`inode_operations`**: 针对文件元数据的操作。
    *   `create`: 创建新文件。
    *   `lookup`: 在目录中查找文件名对应的 inode。
    *   `mkdir`: 创建目录。
3.  **`file_operations`**: 针对文件内容的操作（这是驱动开发最常接触的）。
    *   `read` / `read_iter`: 读取数据。
    *   `write` / `write_iter`: 写入数据。
    *   `mmap`: 内存映射。
    *   `ioctl`: 设备控制。

---

### 4. 关键机制：缓存 (Caching)

Linux 文件系统性能极高的核心原因在于两层缓存。

#### A. Dentry Cache (dcache)
*   **问题**: 每次读 `/etc/passwd` 都要从磁盘根目录一层层解析路径太慢。
*   **解决**: VFS 将解析过的路径（文件名 -> inode 映射）保存在内存哈希表中。
*   **效果**: 第二次访问同一路径时，直接从内存获取 inode，零磁盘 IO。

#### B. Page Cache (页缓存)
*   **问题**: 磁盘读写比内存慢 1000 倍以上。
*   **解决**:
    *   **读**: 当你读取文件时，内核读取 4KB 数据并放入内存（Page）。下次读同一位置，直接从内存给数据。
    *   **写**: `write` 调用只是将数据写入 Page Cache 并标记为 "Dirty"（脏页），然后立刻返回成功。后台线程（`pdflush` / `wb`）稍后异步将脏页刷回磁盘。
*   **副作用**: 这就是为什么 Linux 系统看起来内存总是快被“用光”了，其实是被用作 Cache。

---

### 5. 文件系统分类

Linux 支持多种类型的文件系统：

1.  **基于磁盘的文件系统 (Disk-based)**:
    *   **Ext4**: Linux 标准默认，稳定，支持日志。
    *   **XFS**: 高性能，适合大文件和服务器，格式化快。
    *   **Btrfs**: 现代文件系统，支持快照、CoW (Copy-on-Write)。
2.  **基于网络的文件系统 (Network)**:
    *   **NFS**: Unix/Linux 间共享。
    *   **SMB/CIFS**: Windows 共享。
3.  **伪文件系统 (Pseudo/Virtual)**: 不占用磁盘空间，存在于内存中，作为内核与用户交互的接口。
    *   **`/proc`**: 展示进程信息和内核状态。
    *   **`/sys` (sysfs)**: 展示设备驱动模型（kobject）。
    *   **`tmpfs`**: 基于内存的临时文件系统。

---

### 6. 一个 read() 操作的完整生命周期

当用户程序执行 `read(fd, buf, len)` 时，内核发生了什么？

1.  **系统调用**: 用户态陷入内核态 `sys_read`。
2.  **查找 File 对象**: 内核根据 `fd` (文件描述符) 找到进程文件表中的 `struct file`。
3.  **VFS 调用**: VFS 调用 `file->f_op->read_iter`（指向具体文件系统的读函数，如 `ext4_file_read_iter`）。
4.  **查询 Page Cache**:
    *   Ext4 计算文件偏移量对应磁盘的哪个逻辑块。
    *   在 Page Cache (Radix Tree / XArray) 中查找该页。
    *   **命中**: 直接拷贝数据到用户缓冲区 `buf`，返回。
    *   **未命中**: 触发缺页（Page Fault）逻辑。
5.  **构建 BIO**: 文件系统构造一个 `bio` 请求，包含物理块号。
6.  **提交给通用块层**: 调用 `submit_bio`。
7.  **IO 调度与驱动**: 块层排序合并，驱动 (NVMe/SATA) 通过 DMA 将数据从磁盘搬运到内存 Page。
8.  **完成**: 唤醒等待的进程，数据拷贝到用户态。

### 总结

Linux 文件系统模块通过 **VFS** 实现了极强的抽象能力：
*   对上（用户），它统一了接口（open/read/write）。
*   对下（驱动），它允许无限扩展新的文件系统类型。
*   中间通过 **Dentry Cache** 和 **Page Cache** 保证了接近内存速度的访问性能。

对于内核开发者来说，如果要写一个新的文件系统，重点是实现 `super_operations` 和 `inode_operations`；如果要写一个字符设备驱动，重点是实现 `file_operations`。

## APIs

Linux内核文件系统（VFS）的API非常庞大，因为它既要服务于**设备驱动开发**（如何让设备变成一个文件），也要服务于**文件系统开发**（如何实现Ext4/FAT32），还要服务于**内核态文件操作**（如何在内核里读写文件）。

以下是按功能分类的详细API汇总表。

### 1. 内核态文件读写 API (File I/O from Kernel Space)
**场景**：在内核模块中直接读写文件（如读取配置文件、写日志）。
*注意：在现代内核中，建议使用 `kernel_read/write` 替代旧的 `vfs_read/write` + `set_fs` 方式。*

| API 函数名 | 功能描述 | 关键参数/备注 |
| :--- | :--- | :--- |
| **`filp_open`** | 在内核空间打开一个文件。类似于用户态的 `open()`。 | `filename`: 路径<br>`flags`: `O_RDONLY` 等<br>**返回**: `struct file *` (需用 `IS_ERR()` 检查错误) |
| **`filp_close`** | 关闭由 `filp_open` 打开的文件。 | `filp`: 文件指针<br>`id`: 通常为 `NULL` |
| **`kernel_read`** | 从打开的文件中读取数据。 | `file`: 文件指针<br>`buf`: 接收缓冲区<br>`count`: 读取字节数<br>`pos`: 读取位置指针 (`loff_t *`) |
| **`kernel_write`** | 向打开的文件写入数据。 | `file`: 文件指针<br>`buf`: 数据源<br>`count`: 写入字节数<br>`pos`: 写入位置指针 |
| **`vfs_stat`** | 获取文件的元数据（大小、权限、时间等）。类似于用户态 `stat()`。 | `path`: 文件路径<br>`stat`: 结果结构体 (`struct kstat`) |
| **`vfs_fsync`** | 将文件缓存数据刷入磁盘。 | `file`: 文件指针<br>`datasync`: 是否只同步数据（忽略元数据） |

---

### 2. 字符设备驱动注册 API (Char Device Registration)
**场景**：编写字符设备驱动，将其注册到 VFS，生成 `/dev/xxx` 节点。

| API 函数名 | 功能描述 | 关键参数/备注 |
| :--- | :--- | :--- |
| **`alloc_chrdev_region`** | 动态申请主/次设备号（Major/Minor Number）。 | `dev`: 返回的设备号<br>`baseminor`: 起始次设备号<br>`count`: 数量 |
| **`cdev_init`** | 初始化 `struct cdev` 结构体，并绑定 `file_operations`。 | `cdev`: 字符设备结构体<br>`fops`: **核心操作函数表** (open/read/write) |
| **`cdev_add`** | 将字符设备添加到内核系统中，使其激活。 | `cdev`: 已初始化的结构体<br>`dev`: 设备号<br>`count`: 数量 |
| **`cdev_del`** | 从系统中移除字符设备。 | 模块卸载时调用。 |
| **`class_create`** | 在 `/sys/class/` 下创建一个类目录。 | 用于配合 `device_create` 自动创建设备节点。 |
| **`device_create`** | 在 `/dev/` 下创建具体的设备节点文件。 | 替代了旧的手动 `mknod` 命令。<br>依赖 `udev` 或 `mdev` 用户态守护进程。 |

---

### 3. 文件操作回调接口 (`struct file_operations`)
**场景**：驱动开发者**必须实现**的函数集，VFS 会在用户调用对应系统调用时回调这些函数。

| 回调函数名 | 功能描述 | 典型实现逻辑 |
| :--- | :--- | :--- |
| **`.owner`** | 模块所有者，防止模块在被使用时卸载。 | 通常设为 `THIS_MODULE`。 |
| **`.open`** | 设备打开时的钩子。 | 分配私有数据，初始化硬件，增加引用计数。 |
| **`.release`** | 设备关闭时的钩子（对应 `close`）。 | 释放私有数据，关闭硬件，减少引用计数。 |
| **`.read`** | 从设备读取数据到用户空间。 | 使用 `copy_to_user` 将数据从内核传给用户。 |
| **`.write`** | 将用户空间数据写入设备。 | 使用 `copy_from_user` 获取用户数据。 |
| **`.unlocked_ioctl`** | 设备控制接口（对应 `ioctl`）。 | 处理特定命令（如设置波特率、复位设备）。 |
| **`.mmap`** | 内存映射接口。 | 将设备物理内存映射到用户进程虚拟地址空间。 |
| **`.poll`** | 轮询接口（对应 `select` / `epoll`）。 | 配合等待队列 (`wait_queue`) 实现非阻塞 I/O。 |

---

### 4. 通用 VFS 辅助函数 (Generic VFS Helpers)
**场景**：在实现文件系统或驱动时，使用内核提供的标准实现来简化代码。

| API 函数名 | 功能描述 | 典型应用场景/备注 |
| :--- | :--- | :--- |
| **`generic_file_read_iter`** | 标准的文件读取实现。 | 利用 Page Cache 进行读取。<br>大多数基于磁盘的文件系统直接使用此函数作为 `.read_iter`。 |
| **`generic_file_write_iter`** | 标准的文件写入实现。 | 利用 Page Cache 进行写入。 |
| **`generic_file_mmap`** | 标准的内存映射实现。 | 适用于常规文件映射。 |
| **`nonseekable_open`** | 标记文件为不可定位（不支持 `lseek`）。 | 用于管道、Socket 或某些流式字符设备。 |
| **`simple_read_from_buffer`** | 从内存缓冲区读取数据的简便函数。 | 适用于伪文件系统或数据量很小的驱动读取实现。 |

---

### 5. 文件描述符与文件对象管理 (FD & File Management)
**场景**：在内核中手动将一个 `struct file` 关联到一个整数文件描述符 `fd`（常见于实现 `anon_inode` 或特殊 IPC 机制）。

| API 函数名 | 功能描述 | 典型应用场景/备注 |
| :--- | :--- | :--- |
| **`get_unused_fd_flags`** | 申请一个未使用的文件描述符整数（fd）。 | 返回一个 int，如 3。 |
| **`fd_install`** | 将 `fd` 和 `struct file *` 绑定。 | 绑定后，用户空间的 fd 3 就对应了这个文件对象。 |
| **`fget`** | 根据 fd 获取对应的 `struct file *` 并增加引用计数。 | 使用完必须调用 `fput`。 |
| **`fput`** | 减少 `struct file` 的引用计数。 | 如果计数为0，触发文件释放。 |
| **`alloc_file`** | 分配一个新的 `struct file` 对象。 | 创建匿名文件时使用。 |

---

### 6. 文件系统注册与挂载 (Filesystem Registration)
**场景**：编写一个新的文件系统类型（如 MyFS）并注册到内核。

| API 函数名 | 功能描述 | 典型应用场景/备注 |
| :--- | :--- | :--- |
| **`register_filesystem`** | 注册一种新的文件系统类型。 | 模块加载时调用。<br>参数为 `struct file_system_type`。 |
| **`unregister_filesystem`** | 注销文件系统类型。 | 模块卸载时调用。 |
| **`mount_nodev`** | 辅助函数：挂载不依赖物理设备的文件系统。 | 用于 `tmpfs`, `proc` 等伪文件系统。 |
| **`mount_bdev`** | 辅助函数：挂载依赖块设备的文件系统。 | 用于 `ext4`, `xfs` 等磁盘文件系统。 |
| **`kill_litter_super`** | 销毁超级块的通用函数。 | 卸载文件系统时使用。 |
| **`new_inode`** | 分配一个新的 `struct inode`。 | 在创建新文件时调用。 |
| **`d_make_root`** | 为根 inode 创建对应的 `dentry`。 | 在文件系统挂载初始化时调用。 |

---

### 7. 核心数据结构速查

理解 API 必须了解操作的对象，以下是 VFS 的四大金刚：

| 结构体名 | 代表含义 | 包含的关键信息 |
| :--- | :--- | :--- |
| **`struct file`** | **已打开的文件** (进程级) | `f_pos` (偏移量), `f_op` (操作函数), `private_data` (驱动私有数据)。 |
| **`struct inode`** | **文件实体** (磁盘级) | `i_ino` (索引号), `i_size` (大小), `i_mode` (权限), 物理块映射。 |
| **`struct dentry`** | **目录项** (路径级) | `d_name` (文件名), `d_parent` (父目录), `d_inode` (指向的inode)。 |
| **`struct super_block`** | **文件系统** (挂载级) | `s_blocksize` (块大小), `s_magic` (魔数), 根目录 dentry。 |

### 总结：开发者该用哪些？

1.  **普通驱动开发者**：主要关注 **第 2 类**（注册设备）和 **第 3 类**（实现 fops）。
2.  **内核业务逻辑开发者**：主要关注 **第 1 类**（读写配置/日志）。
3.  **文件系统开发者**：主要关注 **第 4 类**（通用 Helper）和 **第 6 类**（FS 注册）。