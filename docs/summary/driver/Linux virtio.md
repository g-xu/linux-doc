
## APIs

核心数据结构接口

| 接口/数据结构 | 头文件 | 功能描述 |
|--------------|--------|----------|
| struct virtio_device | &lt;linux/virtio.h&gt; | 表示一个VirtIO设备，包含设备状态、配置、virtqueues等 |
| struct virtqueue | &lt;linux/virtio.h&gt; | VirtIO队列，用于设备与驱动之间的数据传输 |
| struct virtio_driver | &lt;linux/virtio.h&gt; | VirtIO驱动结构，包含probe、remove等回调函数 |
| struct virtio_config_ops | &lt;linux/virtio_config.h&gt; | 设备配置操作函数集，用于访问设备配置空间 |
设备发现与注册接口

| 接口函数 | 功能描述 | 使用场景 |
|----------|----------|----------|
| register_virtio_driver() | 注册VirtIO驱动到VirtIO总线 | 驱动初始化时调用 |
| unregister_virtio_driver() | 从VirtIO总线注销驱动 | 驱动卸载时调用 |
| register_virtio_device() | 注册VirtIO设备到总线 | 设备发现时由transport层调用 |
| unregister_virtio_device() | 从总线注销设备 | 设备移除时调用 |
Virtqueue管理接口

| 接口函数 | 功能描述 | 参数说明 |
|----------|----------|----------|
| virtio_find_vqs() | 查找并初始化多个virtqueues | vdev: 设备, nvqs: 队列数, vqs: 队列数组 |
| virtio_find_single_vq() | 查找并初始化单个virtqueue | vdev: 设备, callback: 回调函数, name: 队列名 |
| virtqueue_add_inbuf() | 添加输入缓冲区到virtqueue | 设备写入数据到驱动 |
| virtqueue_add_outbuf() | 添加输出缓冲区到virtqueue | 驱动写入数据到设备 |
| virtqueue_add_sgs() | 添加多个scatterlist到virtqueue | 支持多个输入/输出缓冲区 |
| virtqueue_get_buf() | 从used ring获取已处理的缓冲区 | 获取设备处理完成的缓冲区 |
| virtqueue_kick() | 通知设备有新的缓冲区可用 | 触发设备处理请求 |
| virtqueue_disable_cb() | 禁用virtqueue回调 | 优化性能，减少中断 |
| virtqueue_enable_cb() | 启用virtqueue回调 | 重新启用中断处理 |
设备配置接口

| 接口函数 | 功能描述 | 使用示例 |
|----------|----------|----------|
| virtio_cread() | 从设备配置空间读取数据 | virtio_cread(vdev, struct virtio_rng_config, entropy) |
| virtio_cwrite() | 向设备配置空间写入数据 | virtio_cwrite(vdev, struct virtio_net_config, mtu, mtu_val) |
| virtio_has_feature() | 检查设备是否支持特定特性 | if (virtio_has_feature(vdev, VIRTIO_F_VERSION_1)) |
| virtio_device_ready() | 标记设备准备就绪 | 在probe函数中启用设备 |
特性协商接口

| 宏定义 | 功能描述 | 典型特性值 |
|--------|----------|------------|
| VIRTIO_F_VERSION_1 | VirtIO 1.0兼容性 | 32 |
| VIRTIO_F_ACCESS_PLATFORM | 平台IOMMU支持 | 33 |
| VIRTIO_F_RING_PACKED | Packed virtqueue支持 | 34 |
| VIRTIO_F_IN_ORDER | 按序处理特性 | 35 |
| VIRTIO_F_ORDER_PLATFORM | 平台内存排序 | 36 |
| VIRTIO_F_SR_IOV | SR-IOV支持 | 37 |
| VIRTIO_F_NOTIFICATION_DATA | 通知数据特性 | 38 |
设备类型接口

| 设备类型 | 设备ID | 头文件 | 主要功能 |
|----------|--------|--------|----------|
| 网络设备 | VIRTIO_ID_NET (1) | &lt;linux/virtio_net.h&gt; | 虚拟网络接口 |
| 块设备 | VIRTIO_ID_BLOCK (2) | &lt;linux/virtio_blk.h&gt; | 虚拟磁盘设备 |
| 控制台 | VIRTIO_ID_CONSOLE (3) | &lt;linux/virtio_console.h&gt; | 虚拟串口控制台 |
| 熵源 | VIRTIO_ID_RNG (4) | &lt;linux/virtio_rng.h&gt; | 随机数生成器 |
| Balloon | VIRTIO_ID_BALLOON (5) | &lt;linux/virtio_balloon.h&gt; | 内存气球设备 |
| SCSI主机 | VIRTIO_ID_SCSI (8) | &lt;linux/virtio_scsi.h&gt; | SCSI主机适配器 |
| 9P传输 | VIRTIO_ID_9P (9) | &lt;linux/virtio_9p.h&gt; | 9P文件系统传输 |
| GPU设备 | VIRTIO_ID_GPU (16) | &lt;linux/virtio_gpu.h&gt; | 虚拟GPU设备 |
| 输入设备 | VIRTIO_ID_INPUT (18) | &lt;linux/virtio_input.h&gt; | 虚拟输入设备 |
| Crypto设备 | VIRTIO_ID_CRYPTO (20) | &lt;linux/virtio_crypto.h&gt; | 加密加速设备 |
传输层接口

| 传输类型 | 驱动模块 | 功能描述 |
|----------|----------|----------|
| PCI传输 | virtio-pci | 通过PCI总线传输VirtIO设备 |
| MMIO传输 | virtio-mmio | 通过内存映射IO传输 |
| CCW传输 | virtio-ccw | s390架构的通道命令字传输 |
| VDPA传输 | vdpa | 用户态数据路径加速 |
中断处理接口

| 接口函数 | 功能描述 | 使用场景 |
|----------|----------|----------|
| vring_interrupt() | Virtqueue中断处理函数 | 处理设备完成通知 |
| virtqueue_get_buf() | 在中断上下文中获取缓冲区 | 中断处理函数中调用 |
设备状态管理接口

| 接口函数 | 功能描述 | 使用场景 |
|----------|----------|----------|
| virtio_reset_device() | 重置VirtIO设备 | 设备错误恢复或移除时 |
| virtio_device_ready() | 标记设备准备就绪 | probe函数中启用设备 |
| virtio_check_driver_offered_feature() | 检查驱动提供的特性 | 特性协商验证 |
内存管理接口

| 接口函数 | 功能描述 | 使用场景 |
|----------|----------|----------|
| virtqueue_get_desc_addr() | 获取描述符表地址 | DMA映射管理 |
| virtqueue_get_avail_addr() | 获取可用环地址 | 缓冲区管理 |
| virtqueue_get_used_addr() | 获取已用环地址 | 完成状态检查 |
调试与统计接口

| 接口函数 | 功能描述 | 使用场景 |
|----------|----------|----------|
| virtqueue_get_vring_size() | 获取virtqueue大小 | 性能调优 |
| virtqueue_enable_cb_prepare() | 准备启用回调 | 优化中断处理 |
| virtqueue_poll() | 轮询virtqueue状态 | 非阻塞操作 |
辅助宏和函数

| 宏/函数 | 功能描述 | 示例 |
|---------|----------|------|
| module_virtio_driver() | 简化驱动模块注册 | module_virtio_driver(virtio_xxx_driver) |
| VIRTIO_DEVICE() | 定义设备ID表项 | { VIRTIO_ID_XXX, VIRTIO_DEV_ANY_ID } |
| virtio_config_val() | 读取配置值 | virtio_config_val(vdev, offset, type, val) |
关键头文件

| 头文件 | 包含内容 | 重要性 |
|--------|----------|--------|
| &lt;linux/virtio.h&gt; | 核心VirtIO定义 | 必需 |
| &lt;linux/virtio_config.h&gt; | 配置空间操作 | 必需 |
| &lt;linux/virtio_ring.h&gt; | Virtqueue实现 | 必需 |
| &lt;linux/virtio_ids.h&gt; | 设备ID定义 | 推荐 |
| 设备特定头文件 | 如&lt;linux/virtio_net.h&gt; | 按需 |