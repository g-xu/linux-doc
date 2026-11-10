# 07-debug.md 模板

> 质量基准：`output/i2c/07-debug.md`（691 行）

## 固定检查动作（六条）

1. **日志可见性等级**：每条日志标等级。`dev_err`/`dev_dbg`/`pr_debug`/`WARN`/`ratelimited`/一生只打一次。
   - **必须细化到变体**：`dev_err_once`/`dev_warn_once`/`dev_dbg_ratelimited` 不是"始终输出"
   - "默认看不见"要写在最显眼处
2. **调试开关全门**：`module_param` 权限位、`#ifdef`、用 `dev_dbg` 还是裸 `printk`（决定受不受 `dynamic_debug` 管）
3. **可写调试文件 setter 语义**：写进去的值是什么意思——从 setter 函数读，不能猜
4. **错误码点名 + 分组**：每行点名返回它的函数。同码多源必须按语义分组展开 top-N，不能只罗列行号
5. **tracepoint TP_CONDITION + 字段清单**：连 TP_CONDITION 一起读（决定过滤器写法），字段清单要列全
6. **示例命令输出不许编**：与内核字符串同等级

## §1 编译期开关

Kconfig 选项表：选项 / 类型 / 效果。注意"同码不同可见性"——同一个 `dev_dbg` 在不同 `#ifdef` 下行为不同。

## §2 tracepoint（如有）

事件表 + 调用点表 + TP_CONDITION + 字段清单 + filter 陷阱。

如果子系统没有 tracepoint，说明本子系统无可观测事件点，并指出替代观测手段：
- ftrace function tracer（`function` / `function_graph`）可跟踪哪些函数被调用
- debugfs 文件（见 §4）是否提供状态查询
- 内核日志（见 §1）的默认输出等级

## §3 sysfs（如有）

属性表 + 目录树 + 拓扑关系。无 `new_id` 等机制的澄清。

如果子系统不创建 sysfs 属性，说明本子系统无用户态可见的 sysfs 接口，并指出调试靠什么（debugfs / 内核日志 / ftrace）。

## §4 debugfs

核心建目录的子系统要画目录树。只在某个驱动里建的不画全局树。

## §4b 故障注入（如有）

仅当子系统有故障注入机制时写此节。检查项：
- 触发方式（debugfs 文件 / module_param / fault-injection framework）
- 可注入的故障类型表（错误码注入 / 超时注入 / 丢包注入等）
- 与 `CONFIG_FAULT_INJECTION` / `CONFIG_FAIL_FUNCTION` 的关系
- 示例命令（不许编输出）

## §5 用户态工具

子系统有字符设备时必须有此节：
- 工具名称 + 安装方式
- 典型用法示例
- 输出怎么解读

## §6 错误码分诊

| 错误码 | 含义 | 谁产生 | 典型场景 |
|--------|------|--------|---------|

同码多源按语义分组展开 top-N。

## §7 排查路径

按场景组织。根据子系统类型选择最小覆盖集：

**硬件总线型**（I2C / SPI / MMC 等）：
- 枚举失败（设备不出现）
- 传输失败（数据错误/超时）
- 性能问题（速率不达标）
- 电源问题（suspend/resume 失败）

**框架型**（pinctrl / clk / regmap 等）：
- 注册失败（控制器 probe 失败）
- 消费者获取失败（设备拿不到资源）
- 配置冲突（多个消费者争用同一资源）
- 状态异常（回调未触发、状态机卡住）

**协议/网络型**（net / crypto / block 等）：
- 连接失败（链路不 up）
- 协议错误（包格式/校验失败）
- 性能问题（吞吐不达标）
- 资源耗尽（队列满/内存不足）

## §8 一页速查

篇末一张速查表：场景 → 看哪里 → 用什么命令。

## 常见陷阱

- 把 `_once`/`_ratelimited` 归入"始终输出"
- 只罗列错误码行号不按语义分组
- 示例命令输出是编的（必须与内核字符串一致）
- 忘了 `dynamic_debug` 对 `dev_dbg` 的影响
