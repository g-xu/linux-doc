## net

Linux内核网络驱动是操作系统网络子系统中最底层、最关键的部分，它充当了**Linux内核网络协议栈**与**物理网络硬件（如网卡 NIC）**之间的桥梁。

下面我将从**架构概览、核心数据结构、主要工作流程、NAPI机制、以及常见API**几个方面详细介绍Linux网络驱动模块。

---

### 1. 架构概览 (Architecture Overview)

Linux网络子系统采用分层架构，网络驱动位于最底层：

1.  **用户空间 (User Space)**: 应用程序 (Web Server, Browser)。
2.  **系统调用接口 (System Call Interface)**: socket, read, write。
3.  **协议无关接口 (Protocol Agnostic Interface)**: `struct sock`。
4.  **网络协议栈 (Network Protocols)**: TCP, UDP, IP, ICMP。
5.  **设备无关层 (Device Independent Layer)**: `net_device` 子系统。这是驱动对接的上层，统一了不同硬件的接口。
6.  **设备驱动程序 (Device Drivers)**: **这里是重点**。负责控制硬件、DMA映射、收发包。
7.  **硬件层 (Hardware)**: 物理网卡 (PHY, MAC)。

---

### 2. 核心数据结构 (Core Data Structures)

网络驱动主要围绕两个核心结构体展开：

#### A. `struct net_device` (网卡设备抽象)
这是Linux内核中代表一个网络接口（如 `eth0`）的核心结构。
*   **功能**: 描述网络设备的属性（MAC地址、MTU、中断号、基地址等）和操作方法。
*   **关键成员**:
    *   `name`: 设备名称 (如 "eth0")。
    *   `netdev_ops`: 指向 `struct net_device_ops` 的指针，包含驱动实现的回调函数（如打开、发送、配置MAC等）。
    *   `priv`: 私有数据指针，驱动程序用它来存放自己特有的数据（如映射的寄存器地址、Ring Buffer 指针等）。

#### B. `struct sk_buff` (Socket Buffer)
这是Linux网络子系统的“货币”，数据包在各个层级之间传递的载体。
*   **功能**: 存放网络报文的数据（Payload）以及协议头（MAC头、IP头、TCP头）的元信息。
*   **驱动中的作用**:
    *   **发送 (TX)**: 内核将填充好的 `sk_buff` 传递给驱动，驱动通过DMA映射其数据区发送出去。
    *   **接收 (RX)**: 驱动分配空的 `sk_buff`，网卡通过DMA将数据填入，驱动再将填好的 `sk_buff` 向上层递交。

---

### 3. 网络驱动的主要工作流程

一个标准的网络驱动模块通常包含以下几个生命周期阶段：

#### 3.1 初始化 (Probe/Init)
当内核发现硬件（PCIe Probe 或 Device Tree 匹配）时触发：
1.  **分配 net_device**: 使用 `alloc_etherdev()`。
2.  **硬件初始化**: 启用PCI设备，映射寄存器地址 (IO Remap)。
3.  **设置操作函数**: 将 `dev->netdev_ops` 指向驱动实现的函数表。
4.  **注册 NAPI**: 如果支持NAPI（几乎所有现代网卡都支持），初始化 polling 机制。
5.  **注册设备**: 调用 `register_netdev()` 将设备注册到内核网络子系统。

#### 3.2 打开设备 (ndo_open)
当用户执行 `ifconfig eth0 up` 时触发：
1.  注册中断处理函数 (`request_irq`)。
2.  分配发送和接收的 **Ring Buffer (描述符环)**。
3.  初始化 PHY（物理层），建立链路连接。
4.  开启网卡的发送/接收引擎。
5.  调用 `netif_start_queue()` 告诉内核可以发包了。

#### 3.3 数据发送 (TX Path - ndo_start_xmit)
当上层有数据要发送时：
1.  内核调用驱动的 `ndo_start_xmit(skb, dev)`。
2.  驱动获取 TX Ring Buffer 的下一个可用描述符。
3.  **DMA 映射**: 将 `skb->data` 的虚拟地址映射为物理地址（总线地址），填入描述符。
4.  **更新尾指针 (Tail Pointer)**: 写网卡寄存器，通知硬件有新数据。
5.  **硬件动作**: 网卡读取描述符，通过DMA搬运数据到发送FIFO，发送到物理链路。
6.  **发送完成中断**: 硬件发送完毕后触发中断，驱动在中断或软中断中释放 `skb` 和 DMA 映射。

#### 3.4 数据接收 (RX Path - Interrupt + NAPI)
这是最复杂且性能敏感的部分：
1.  **数据到达**: 网卡收到报文，通过DMA写入内存中的 RX Ring Buffer。
2.  **触发硬中断**: 网卡通过中断线通知CPU。
3.  **硬中断处理**: 驱动的中断处理函数运行。它通常只做一件事：**屏蔽网卡中断**，并调度 **NAPI 软中断** (`napi_schedule`)。
4.  **软中断轮询 (NAPI Poll)**:
    *   内核在软中断上下文调用驱动的 `poll()` 回调。
    *   驱动遍历 RX Ring，处理已完成的描述符。
    *   分配新的 `skb` 挂载到 Ring 上以备下次使用。
    *   调用 `netif_receive_skb(skb)` 将数据包送入协议栈。
5.  **恢复中断**: 如果处理完所有包，驱动重新开启网卡中断，退出轮询模式。

---

### 4. 关键机制：NAPI (New API)

在早期Linux中，每个报文都触发一次中断。在千兆/万兆网络下，这会导致“中断风暴”，CPU完全被中断处理淹没，无法处理其他任务。

**NAPI 的核心思想是：中断 + 轮询 (Interrupt mitigation)**
1.  第一个报文到达 -> 触发中断。
2.  关闭中断 -> 切换到轮询模式 (Polling)。
3.  内核周期性轮询网卡：“还有数据吗？有就一次性全取走”。
4.  直到没有数据了 -> 重新开启中断 -> 等待下一波流量。

---

### 5. 驱动开发常用 API 汇总

| 类别 | API 函数 | 功能描述 |
| :--- | :--- | :--- |
| **设备分配/注册** | `alloc_etherdev(sizeof(priv))` | 分配以太网设备结构体 |
| | `register_netdev(dev)` | 注册网卡到内核 |
| | `unregister_netdev(dev)` | 注销网卡 |
| | `free_netdev(dev)` | 释放内存 |
| **队列控制** | `netif_start_queue(dev)` | 开启发送队列 |
| | `netif_stop_queue(dev)` | 停止发送队列（通常在Ring满时调用） |
| | `netif_wake_queue(dev)` | 唤醒发送队列（在清理完TX Ring后调用） |
| **SKB 操作** | `dev_alloc_skb(len)` | 分配 socket buffer |
| | `dev_kfree_skb(skb)` | 释放 skb (非中断上下文) |
| | `dev_kfree_skb_irq(skb)` | 释放 skb (中断上下文) |
| | `netif_receive_skb(skb)` | 将接收到的包递交给协议栈 |
| **DMA 操作** | `dma_map_single(...)` | 流式DMA映射，用于映射 skb data |
| | `dma_unmap_single(...)` | 解除映射 |
| **NAPI** | `netif_napi_add(...)` | 初始化 NAPI 结构 |
| | `napi_schedule(...)` | 调度 NAPI 轮询（在硬中断中调用） |
| | `napi_complete_done(...)` | 退出轮询模式，重新开中断 |

---

### 6. 现代网卡驱动的高级特性

如果你在阅读 Intel (i40e/ice) 或 Mellanox (mlx5) 的驱动源码，会发现比上述复杂得多，因为它们包含：

1.  **多队列 (Multi-Queue / RSS)**:
    *   现代网卡有多个 RX/TX 队列。
    *   每个队列对应一个 CPU 核中断，实现并行处理（Receive Side Scaling）。
2.  **硬件卸载 (Offloading)**:
    *   **CSUM**: 硬件计算 TCP/IP 校验和。
    *   **TSO/GSO**: 协议栈发送巨大的包（64KB），由网卡硬件切分成 MTU 大小的包发送。
    *   **LRO/GRO**: 网卡或驱动将接收到的多个小包合并成一个大包交给协议栈，减少协议栈处理开销。
3.  **XDP (eXpress Data Path)**:
    *   在 `skb` 分配之前（驱动的最底层）运行 eBPF 程序。
    *   用于极高性能的丢包（DDoS防御）、负载均衡或转发。

### 总结

Linux网络驱动模块的本质是**生产者-消费者模型**的实现：
*   **发送方向**：内核生产数据 (`skb`)，驱动将其放入环形缓冲区，网卡硬件消费数据发送到线路。
*   **接收方向**：网卡硬件从线路消费数据放入环形缓冲区，驱动生产数据 (`skb`) 扔给内核协议栈。

理解了 `net_device`、`sk_buff`、`DMA Ring Buffer` 和 `NAPI`，你就掌握了网络驱动的核心命脉。

## 网络分层

Linux内核网络子系统是Linux内核中最庞大、最复杂的部分之一。它采用了严格的**分层架构**，设计上遵循（但不完全拘泥于）OSI 7层模型和TCP/IP 4层模型。

从上层的系统调用到最底层的硬件驱动，数据包（Packet）主要通过核心数据结构 `sk_buff`（Socket Buffer）在各层之间传递。

以下是Linux内核网络模块的详细分层介绍，以及每一层对应的内核源码路径。

---

### Linux网络子系统分层架构图

为了方便理解，我们可以将内核网络部分划分为以下 **5个逻辑层次**：

1.  **系统调用接口层 (System Call Interface)**
2.  **协议无关接口层 (Protocol Agnostic Interface / Socket Layer)**
3.  **网络协议栈层 (Network Protocols - TCP/IP)**
4.  **设备无关接口层 (Device Independent Interface)**
5.  **设备驱动层 (Device Drivers)**

---

### 第一层：系统调用接口 (System Call Interface)

这是应用程序（用户空间）进入内核空间的入口。当应用程序调用 `socket()`, `bind()`, `connect()`, `send()`, `recv()` 等函数时，会通过系统调用（Soft Interrupt / Syscall instruction）陷入内核。

*   **功能**: 将用户空间的请求传递给内核，进行参数检查和上下文切换。
*   **对应代码**:
    *   `net/socket.c`: 包含了 `sys_socket`, `sys_connect`, `sys_sendto` 等系统调用的通用实现。
    *   `net/syscalls.c`: 系统调用注册相关。

### 第二层：协议无关接口 (Socket Layer)

这一层提供了一个通用的 Socket 抽象，它不关心你具体使用的是 TCP、UDP 还是 Unix Domain Socket。它负责管理 `struct socket` 结构体。

*   **功能**: 将通用的 Socket 操作映射到具体的协议族（Protocol Family，如 AF_INET）操作上。
*   **核心结构**: `struct socket` (BSD socket 抽象)。
*   **对应代码**:
    *   `net/core/sock.c`: **非常核心的文件**。定义了通用的套接字操作。
    *   `include/linux/net.h`: Socket 结构定义。

---

### 第三层：网络协议栈 (Network Protocols)

这是内核网络部分“最厚”的一层，包含了具体的协议实现。在Linux中，这通常指 **TCP/IP 协议族**。数据在这里被封装（添加头部）或解封装（剥离头部）。

#### 3.1 传输层 (Transport Layer - L4)
负责端到端的通信、流控和拥塞控制。
*   **协议**: TCP, UDP, ICMP, SCTP。
*   **核心结构**: `struct sock` (也就是 `sk`，网络层面的socket), `struct tcp_sock`。
*   **对应代码**:
    *   `net/ipv4/tcp*.c` (如 `tcp.c`, `tcp_input.c`, `tcp_output.c`): TCP 协议极其复杂，分散在多个文件中。
    *   `net/ipv4/udp.c`: UDP 协议实现。
    *   `net/ipv4/raw.c`: 原始套接字实现。

#### 3.2 网络层 (Network Layer - L3)
负责路由选择（Routing）和 IP 包的转发。
*   **协议**: IP (IPv4/IPv6), ARP (虽然ARP介于2/3层，但在Linux实现中常归于此), ICMP。
*   **核心功能**: 路由查找 (`fib_lookup`)，IP分片与重组，Netfilter (防火墙钩子)。
*   **对应代码**:
    *   `net/ipv4/ip_input.c`: IP包接收处理。
    *   `net/ipv4/ip_output.c`: IP包发送处理。
    *   `net/ipv4/ip_forward.c`: 转发逻辑。
    *   `net/ipv4/route.c`: **路由子系统**，决定包发往哪里。
    *   `net/core/neighbour.c` 和 `net/ipv4/arp.c`: 邻居子系统（管理 ARP 缓存，解析 MAC 地址）。

---

### 第四层：设备无关接口 (Device Independent Interface)

这一层是协议栈与驱动之间的“腰部”。它统一了所有网络设备的接口，使得上层协议不需要关心底层是 Ethernet 还是 Wi-Fi，也不需要关心网卡是 Intel 的还是 Realtek 的。

*   **功能**:
    *   定义了统一的设备抽象 `struct net_device`。
    *   **流量控制 (Qdisc / Traffic Control)**: 在这里进行排队、限速、整形。
    *   **NAPI 调度**: 负责调度驱动的轮询。
*   **核心结构**: `struct net_device`, `struct sk_buff` (贯穿全程).
*   **对应代码**:
    *   `net/core/dev.c`: **整个网络子系统的核心枢纽**。包含了 `dev_queue_xmit` (发送入口) 和 `netif_receive_skb` (接收入口)。
    *   `net/core/skbuff.c`: `sk_buff` 的分配与释放管理。
    *   `net/sched/`: 流量控制（TC）和队列调度算法（如 pfifo_fast, CoDel）的代码。

---

### 第五层：设备驱动层 (Device Drivers)

这是软件与硬件的边界。驱动程序负责通过 DMA 与物理网卡交互。

*   **功能**:
    *   **TX**: 将 `sk_buff` 映射到物理内存，填充硬件描述符（Descriptor），通知硬件发送。
    *   **RX**: 处理硬件中断，从 Ring Buffer 取下数据，封装成 `sk_buff` 提交给上层。
*   **对应代码**:
    *   `drivers/net/ethernet/`: 以太网卡驱动目录。
        *   `drivers/net/ethernet/intel/`: Intel 网卡驱动 (e1000e, ixgbe, i40e)。
        *   `drivers/net/ethernet/realtek/`: Realtek 网卡驱动。
        *   `drivers/net/ethernet/mellanox/`: 高性能 RDMA/以太网卡驱动。
    *   `drivers/net/wireless/`: Wi-Fi 驱动。

---

### 源码目录结构映射汇总表

为了更直观，以下表格列出了各层级对应的主要源码路径（基于 Linux Kernel 5.x/6.x）：

| 逻辑层级 | 主要功能 | 对应内核源码路径 | 关键源文件举例 |
| :--- | :--- | :--- | :--- |
| **User API** | Socket系统调用 | `net/socket.c` | `SYSCALL_DEFINE3(socket, ...)` |
| **Socket Layer** | 协议族无关抽象 | `net/core/` | `net/core/sock.c` |
| **TCP/UDP (L4)** | 传输层协议 | `net/ipv4/`, `net/ipv6/` | `net/ipv4/tcp.c`, `net/ipv4/udp.c` |
| **IP (L3)** | 路由与寻址 | `net/ipv4/` | `net/ipv4/ip_input.c`, `net/ipv4/route.c` |
| **Neighbor (L2.5)** | ARP/ND 邻居发现 | `net/core/`, `net/ipv4/` | `net/core/neighbour.c`, `net/ipv4/arp.c` |
| **Dev Interface** | **设备无关通用层** | `net/core/` | **`net/core/dev.c`** (网络核心) |
| **Traffic Control** | QoS, 排队规则 | `net/sched/` | `net/sched/sch_generic.c` |
| **Drivers (L1/L2)** | **网卡驱动程序** | `drivers/net/` | `drivers/net/ethernet/intel/e1000/e1000_main.c` |
| **Header Files** | 头文件定义 | `include/linux/`, `include/net/` | `include/linux/skbuff.h`, `include/linux/netdevice.h` |

---

### 数据包流转全景 (Code Path)

理解分层的最好方式是看一个数据包如何穿过这些代码文件：

#### 1. 发送路径 (TX Path)
1.  **用户空间**: `send(fd, buf, ...)`
2.  **Socket层**: `net/socket.c` -> `sock_sendmsg`
3.  **TCP层**: `net/ipv4/tcp.c` -> `tcp_sendmsg` (封装TCP头，申请sk_buff)
4.  **IP层**: `net/ipv4/ip_output.c` -> `ip_output` (封装IP头，查路由)
5.  **设备无关层**: `net/core/dev.c` -> `dev_queue_xmit` (流控Qdisc处理)
6.  **驱动层**: `drivers/net/.../xxx_driver.c` -> `ndo_start_xmit` (映射DMA，写寄存器)
7.  **硬件**: 网卡将光/电信号发出。

#### 2. 接收路径 (RX Path)
1.  **硬件**: 网卡收到包，DMA写入内存，触发**硬中断**。
2.  **驱动层 (硬中断)**: `xxx_interrupt` -> 简单的应答，触发**软中断 (NAPI)**。
3.  **驱动层 (软中断)**: `xxx_poll` -> 读取Ring Buffer，封装成 `sk_buff`。
4.  **设备无关层**: `net/core/dev.c` -> `netif_receive_skb` (GRO合并，RPS分发)。
5.  **IP层**: `net/ipv4/ip_input.c` -> `ip_rcv` (校验IP头，Netfilter处理)。
6.  **TCP层**: `net/ipv4/tcp_input.c` -> `tcp_rcv` (处理ACK，重组数据，放入接收队列)。
7.  **Socket层**: `net/core/sock.c` -> 唤醒等待的进程。
8.  **用户空间**: `recv` 返回数据。
## APIs

Linux内核网络驱动开发涉及大量的API，主要用于设备注册、数据包（SKB）管理、NAPI轮询、DMA映射以及队列控制。

以下是按照功能模块分类的详细API汇总表：

### 1. 设备注册与生命周期管理 (Device Registration & Lifecycle)
这些API用于在驱动加载（Probe）和卸载（Remove）阶段管理 `net_device` 结构体。

| API 函数名 | 功能描述 | 典型应用场景/备注 |
| :--- | :--- | :--- |
| **`alloc_etherdev`** | 分配并初始化一个以太网设备的 `net_device` 结构体。 | Probe阶段调用。包含了私有数据(`priv`)的内存分配。 |
| **`alloc_etherdev_mq`** | 分配支持**多队列**（Multi-Queue）的以太网设备结构体。 | 适用于现代高性能网卡（支持RSS）。 |
| **`register_netdev`** | 将初始化好的 `net_device` 注册到内核网络子系统。 | 注册成功后，网卡在 `ifconfig` 中可见。 |
| **`unregister_netdev`** | 从内核注销网络设备。 | Remove阶段调用，移除设备节点。 |
| **`free_netdev`** | 释放 `net_device` 占用的内存。 | 在注销后或注册失败时的错误处理路径中调用。 |
| **`netdev_priv`** | 获取 `net_device` 结构体中预留的私有数据指针。 | 用于获取驱动自定义的结构体（存放寄存器基址、RingBuffer指针等）。 |

---

### 2. 队列控制 (Queue Control / Flow Control)
用于在网卡Ring Buffer满或空闲时，通知内核协议栈暂停或恢复发送数据，实现流控。

| API 函数名 | 功能描述 | 典型应用场景/备注 |
| :--- | :--- | :--- |
| **`netif_start_queue`** | 开启发送队列，允许内核调用驱动的发送函数。 | 通常在 `ndo_open` 初始化完成后调用。 |
| **`netif_stop_queue`** | 停止发送队列。通知内核暂停向下层传递数据包。 | 当TX Ring Buffer（发送描述符）快满时调用，防止溢出。 |
| **`netif_wake_queue`** | 唤醒发送队列。通知内核可以继续发送数据。 | 当TX中断处理完毕，清理了足够的TX描述符后调用。 |
| **`netif_queue_stopped`** | 检查当前发送队列是否处于停止状态。 | 用于调试或状态判断。 |
| **`netif_carrier_on`** | 通知内核物理链路已连接（Link Up）。 | 当PHY芯片检测到网线插入时调用。 |
| **`netif_carrier_off`** | 通知内核物理链路已断开（Link Down）。 | 当PHY芯片检测到网线拔出时调用。 |

---

### 3. 数据包发送与接收 (SKB & Data Path)
涉及 `struct sk_buff` 的分配、释放以及向协议栈递交数据。

| API 函数名 | 功能描述 | 典型应用场景/备注 |
| :--- | :--- | :--- |
| **`netdev_alloc_skb`** | 分配一个新的 `sk_buff`，并针对该网卡进行内存优化。 | 在RX（接收）流程中，用于填充接收描述符。 |
| **`dev_kfree_skb`** | 释放一个 `sk_buff`。 | 仅限**非中断上下文**使用（如发送失败时的清理）。 |
| **`dev_kfree_skb_irq`** | 在**中断上下文**中释放 `sk_buff`。 | 仅限中断中使用。 |
| **`dev_consume_skb_any`** | 释放 `sk_buff` 的通用函数（自动判断上下文）。 | 现代驱动中推荐使用，替代上面两个函数。 |
| **`netif_receive_skb`** | 将接收到的数据包（skb）提交给上层协议栈。 | NAPI轮询函数中调用此函数将包“推”给内核。 |
| **`skb_put`** | 在skb的数据区末尾扩展数据长度（移动 tail 指针）。 | 接收到数据后，告诉skb实际收到了多少字节。 |
| **`skb_push`** | 在skb的数据区头部扩展（移动 data 指针）。 | 通常用于封装协议头。 |
| **`skb_pull`** | 从skb的数据区头部移除数据（移动 data 指针）。 | 解析报文时，剥离MAC头或IP头。 |

---

### 4. NAPI (New API) 高性能轮询机制
现代网卡驱动的核心，用于缓解“中断风暴”，结合中断与轮询。

| API 函数名 | 功能描述 | 典型应用场景/备注 |
| :--- | :--- | :--- |
| **`netif_napi_add`** | 初始化 NAPI 实例，绑定 `poll` 回调函数。 | 在 Probe 阶段调用。 |
| **`napi_enable`** | 启用 NAPI 机制。 | 在 `ndo_open` 中调用。 |
| **`napi_disable`** | 禁用 NAPI 机制。 | 在 `ndo_stop` 中调用。 |
| **`napi_schedule`** | 调度 NAPI 软中断（Softirq）。 | 在**硬中断处理函数**中调用，触发后续的 `poll` 轮询。 |
| **`napi_complete_done`** | 轮询完成。将设备从轮询列表移除并重新开启硬中断。 | 在 `poll` 函数中，当处理完所有包且预算（budget）未用尽时调用。 |
| **`napi_gro_receive`** | 通过 GRO（Generic Receive Offload）机制提交数据包。 | 比 `netif_receive_skb` 更高效，能合并小包减少协议栈开销。 |

---

### 5. DMA 操作 (Direct Memory Access)
网卡不直接读取CPU内存，需要将虚拟地址转换为物理总线地址。

| API 函数名 | 功能描述 | 典型应用场景/备注 |
| :--- | :--- | :--- |
| **`dma_map_single`** | 将 `skb->data` 的虚拟地址映射为物理地址（DMA地址）。 | **发送路径 (TX)**：将数据缓冲区交给网卡发送前调用。 |
| **`dma_unmap_single`** | 解除 DMA 映射。 | **发送完成 (TX Complete)**：网卡发送完数据后调用。 |
| **`dma_alloc_coherent`** | 分配一致性DMA内存（无缓存）。 | 用于分配描述符环（Ring Descriptors），CPU和网卡都能直接看到更新。 |
| **`dma_free_coherent`** | 释放一致性DMA内存。 | 驱动卸载或关闭时调用。 |

---

### 6. 核心回调函数 (`struct net_device_ops`)
虽然不是直接调用的API，但这是驱动**必须实现**并挂载到 `net_device` 上的接口，供内核调用。

| 回调函数名 | 功能描述 | 触发时机 |
| :--- | :--- | :--- |
| **`ndo_open`** | 打开设备，分配资源，开中断。 | 用户执行 `ifconfig eth0 up` 时。 |
| **`ndo_stop`** | 关闭设备，释放中断，停队列。 | 用户执行 `ifconfig eth0 down` 时。 |
| **`ndo_start_xmit`** | **发送函数**。将 skb 放入 TX Ring，触发网卡发送。 | 协议栈有数据要发送时。 |
| **`ndo_set_mac_address`** | 修改 MAC 地址。 | 用户通过命令修改 MAC 地址时。 |
| **`ndo_change_mtu`** | 修改 MTU 大小。 | 用户修改 MTU 时（需重新分配 Buffer 大小）。 |
| **`ndo_tx_timeout`** | 发送超时处理（看门狗）。 | 网卡长时间未发送完成，通常在此重置硬件。 |

### 开发总结
编写一个网络驱动通常遵循以下流程：
1.  **Probe**: `alloc_etherdev` -> `netif_napi_add` -> `register_netdev`。
2.  **Open**: `request_irq` -> `napi_enable` -> `netif_start_queue`。
3.  **RX (Interrupt)**: `napi_schedule` (关闭硬中断)。
4.  **RX (Poll)**: `netdev_alloc_skb` -> `dma_map` -> `napi_gro_receive` -> `napi_complete_done` (开硬中断)。
5.  **TX**: `dma_map_single` -> 写寄存器 -> `netif_stop_queue` (如果满)。
6.  **TX Done**: `dma_unmap_single` -> `dev_kfree_skb_any` -> `netif_wake_queue`。