# 03-internals.md 模板

> 质量基准：`output/i2c/03-internals.md`（894 行）

## §0 导航（开篇必写）

- 分层图：本子系统分几层，每层入口
- 生命周期时间线图：注册→探测→传输→拆除
- "哪些内容不在本篇"去处表（指向 02/04/05/06）

### §0.1 职责划分原则

一句话说明本篇只讲什么（主流程逐段剖析），不讲什么（数据结构去 02，子框架去 04）。

## Part A：时间线

主流程表，按"谁调用谁"排顺序（不是直觉）：

| 阶段 | 入口函数 | 关键操作 | 退出/错误路径 |
|------|---------|---------|-------------|

- **必须含拆除/unregister 路径**——不能只写注册不写拆除
- 每个阶段标出：锁获取/释放、引用计数增减、tracepoint 触发点
- ★ **Part A 每个章节标题后面带上函数的完整行号区间**，读者点开就能对照：

  ```
  ## §A2 控制器注册：`spi_register_controller()`（`spi.c:3099-3236`）
  ### §A7.1 `spi_sync()`（`spi.c:4258-4267`）→ `__spi_sync()`（`spi.c:4178-4235`）
  ### §A7.3 `spi_queued_transfer()`（`spi.c:2187-2190`，4 行的壳）→ `__spi_queued_transfer()`（`spi.c:2155-2178`）
  ```

  区间本身也是信息——"4 行的壳"这种事实只有写了区间才看得出来。
  取区间的配方（含两个坑）见 `methodology.md` **§H**：

  ```bash
  awk '/^int spi_register_controller\(/{s=NR} s&&/^}/{print s"-"NR; exit}' drivers/spi/spi.c
  ```

  **注意这条只适用于 03**：01 的 API 表反过来要求"位置只到文件不到行"（规则 10），
  行号集中在 03 和 99 里维护。

## Part B：横切机制

不是时间线上的一个点，而是横跨多个阶段的机制：
- 锁（mutex/spinlock/bus_lock 的层级与获取顺序）
- 引用计数（哪些对象有 kref/get/put，在时间线哪个点增减）
- tracepoint 可观测性（每个关键点有哪些 tracepoint，字段是什么）
- runtime PM（suspend/resume 怎么和传输路径交互）
- PREEMPT_RT 分析（如果目标树打了 RT 补丁，RT 对本子系统有什么影响）

## Part C：评价

- 设计优点（为什么这样设计）
- 已知缺陷（源码里的 REVISIT/FIXME/TODO 注释——必须 grep 源码并引用）
- 替代方案讨论（如果重来会怎么做）

## 常见陷阱

- 时间线顺序凭直觉排（应该用"谁调用谁"验证）
- 只写 happy path，不写 error path 和 teardown
- Part B 和 Part A 混在一起（应该明确分区）
- 引用计数描述错误（"不加引用"常错——必须 grep kref/get/put 确认）
