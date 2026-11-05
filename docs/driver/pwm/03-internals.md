---
title: 内部实现
---

# pwm 子系统机制

> **基线**：`linux-6.1.158`，**arm64-only**。
> 本篇分三部分：**Part A 流程**（谁在什么时候调什么）·
> **Part B 横切机制**（锁、编号、引用、调试开关）·
> **Part C 设计评价**（源码与官方文档自陈的债、不一致、可质疑的选择）。
> 结构与字段在 02 篇，接口签名在 01 篇，本篇不重复。

---

# Part A：流程

## §A1 注册：`pwmchip_add()` 的九步

`drivers/pwm/core.c:258-311`，全文只有 54 行，但每一步都有可讲的：

```
pwmchip_add(chip)
 1. 入参门禁          :264-265   !chip || !chip->dev || !chip->ops || !chip->npwm → -EINVAL
 2. ops 检查          :267-268   pwm_ops_check() 假 → -EINVAL（只查 .apply）
 3. 拿全局锁          :270       mutex_lock(&pwm_lock)
 4. 分配全局编号      :272-274   alloc_pwms(chip->npwm)  失败 → -ENOSPC
 5. ⚠️ 回填 base      :276       chip->base = ret        无条件覆盖驱动写的值
 6. 分配通道数组      :278-282   kcalloc(npwm, ...)      失败 → -ENOMEM
 7. 逐通道初始化      :284-292   只设 chip/pwm/hwpwm 三个字段 + radix_tree_insert
 8. 登记             :294-297   bitmap_set + list_add(&chip->list, &pwm_chips)
 9. DT 收尾          :301-302   IS_ENABLED(CONFIG_OF) → of_pwmchip_add(chip)
    放锁             :305
10. ⚠️ sysfs 导出     :307-308   在锁外，且失败不影响返回值
```

### §A1.1 第 7 步不调 `.get_state`

通道初始化循环（`:284-292`）的全部内容是：

```c
pwm->chip = chip;
pwm->pwm = chip->base + i;
pwm->hwpwm = i;
radix_tree_insert(&pwm_tree, pwm->pwm, pwm);
```

⚠️ **没有 `.get_state`、没有 `pwm->state` 初始化。**
`chip->pwms` 来自 `kcalloc`，所以每条通道的 `state` 是**全零**——
`period = 0`、`enabled = false`。
`.get_state` 的真正调用点在 `pwm_device_request()`（`core.c:110-111`），
见 §A4。

⇒ 由此可推出一条排查规律：**在任何消费者 request 之前，
`/sys/kernel/debug/pwm` 里所有通道的 period 都显示 0**，
即便硬件上 bootloader 已经在输出 PWM。这不是丢状态，是核心还没去读。

### §A1.2 第 10 步的两个反常

```c
out:
	mutex_unlock(&pwm_lock);

	if (!ret)
		pwmchip_sysfs_export(chip);      /* core.c:308 */

	return ret;
```

| 反常 | 原因/后果 |
|---|---|
| **`pwmchip_sysfs_export()` 在 `mutex_unlock` 之后** | `device_create()` 会触发 uevent、可能睡眠、可能回调进 sysfs 层——放在 `pwm_lock` 里会拉长临界区并有锁序风险。⇒ **chip 已经在 `pwm_chips` 链表上、可以被 `pwm_get()` 找到之后，sysfs 目录才出现**，中间有一个可观测的窗口 |
| **sysfs 导出失败不影响 `pwmchip_add()` 的返回值** | `pwmchip_sysfs_export()`（`sysfs.c:505`）失败时只打 `dev_warn "device_create failed for pwm_chip sysfs export\n"`（`sysfs.c:516-517`）⇒ **`pwmchip_add()` 返回 0，但 `/sys/class/pwm/pwmchipN/` 不存在**。内核功能完好，用户态接口静默缺失 |

⚠️ 第二条是排查"pwmchipN 目录不见了"的第一落点：**先 `dmesg | grep pwm_chip`**，
不要一开始就怀疑驱动没注册。

### §A1.3 `of_pwmchip_add()`：默认 `#pwm-cells = 2`

`core.c:181-198`：

```c
if (!chip->dev || !chip->dev->of_node)      /* :183-184 无 DT 节点直接返回 */
	return;

if (!chip->of_xlate) {
	u32 pwm_cells;
	if (of_property_read_u32(chip->dev->of_node, "#pwm-cells", &pwm_cells))
		pwm_cells = 2;                     /* :191 ⚠️ 读不到就默认 2 */
	chip->of_xlate = of_pwm_xlate_with_flags;
	chip->of_pwm_n_cells = pwm_cells;
}

of_node_get(chip->dev->of_node);           /* :197 拿一次引用 */
```

三条要点：

1. **`#pwm-cells` 缺失不报错，默认 2。** 后果是消费者的
   `pwms = <&pwm 0 50000 PWM_POLARITY_INVERTED>` 里第 4 个 cell
   会被 `of_parse_phandle_with_args()` 按 2 cells 解析而**多余出来**
   ⇒ 解析失败或极性被吞。**DT 里漏写 `#pwm-cells = <3>` 时极性静默失效**。
2. `chip->of_xlate` 已填时**完全跳过**，包括 `of_pwm_n_cells` 也不填
   ⇒ 自定义 xlate 的驱动**必须自己设 `of_pwm_n_cells`**，
   否则它是 0，`of_pwm_xlate_with_flags()` 里的 `< 2` 检查会让所有解析返回 `-EINVAL`。
3. `of_node_get()` 在 `of_pwmchip_remove()`（`core.c:200`）里对应 `of_node_put()`。

---

## §A2 注销：`pwmchip_remove()` 的四步（以及它不做的事）

`drivers/pwm/core.c:323-337`，**返回 `void`**：

```c
void pwmchip_remove(struct pwm_chip *chip)
{
	pwmchip_sysfs_unexport(chip);        /* :325 锁外 */
	mutex_lock(&pwm_lock);               /* :327 */
	list_del_init(&chip->list);          /* :329 从此 pwm_get 找不到它 */
	if (IS_ENABLED(CONFIG_OF))
		of_pwmchip_remove(chip);         /* :332 of_node_put */
	free_pwms(chip);                     /* :334 ⚠️ kfree(chip->pwms) */
	mutex_unlock(&pwm_lock);             /* :336 */
}
```

`free_pwms()`（`core.c:53-67`）做三件事：逐通道 `radix_tree_delete`、
`bitmap_clear(allocated_pwms, chip->base, chip->npwm)`、
`kfree(chip->pwms)` 并置 `NULL`。

### §A2.1 它不检查任何占用状态

⚠️ **通篇没有 `PWMF_REQUESTED` 检查，没有遍历通道看有没有人在用。**
一个仍被消费者持有的 chip 被移除后：

| 时刻 | 状态 |
|---|---|
| `list_del_init()` 之后 | 新的 `pwm_get()` 找不到这个 chip（返回 `-EPROBE_DEFER`） |
| `kfree(chip->pwms)` 之后 | ⚠️ **所有消费者手里的 `struct pwm_device *` 全部悬空** |
| 消费者后续调 `pwm_apply_state()` | ⚠️ **use-after-free** |
| 消费者后续调 `pwm_put()` | ⚠️ **use-after-free**，而且会 `module_put` 一个已释放对象里的 owner |

⚠️ 唯一的防线是 `pwm_ops.owner` 的模块引用计数：只要有通道被 request，
`try_module_get()` 就钉住了 provider 模块，`rmmod` 会失败（`-EBUSY`）。
**但这挡不住 provider 驱动自己在 `remove()` 里调 `pwmchip_remove()`**——
`remove()` 是被 driver core 调的（`unbind`、设备热插拔、`devm` 回收），
和模块引用计数无关。

⇒ 复现路径：`echo <dev> > /sys/bus/platform/drivers/<pwm-drv>/unbind`，
而背光正在用这条 PWM。这是**真实可触发**的。

### §A2.2 kerneldoc 与代码矛盾

`core.c:314-322` 的 kerneldoc 至今写着：

> Removes a PWM chip. This function may return busy if the PWM chip provides
> a PWM device that is still requested.
>
> **Returns: 0 on success or a negative error code on failure.**

⚠️ **函数原型是 `void`（`:323`），返回不了任何东西，也不做 busy 检查。**
这是 6.1 里明确的陈旧文档——历史上 `pwmchip_remove()` 确实返回 `int` 并检查占用，
后来改成 `void` 时 kerneldoc 没跟着改。**以代码为准。**

⚠️ 而且 `include/linux/pwm.h` 里 `CONFIG_PWM=n` 的桩函数**签名不一致**：

| 配置 | 原型 | 位置 |
|---|---|---|
| `CONFIG_PWM=y` | `void pwmchip_remove(struct pwm_chip *chip)` | `include/linux/pwm.h:392` |
| `CONFIG_PWM=n` | `static inline int pwmchip_remove(...)` 返回 `-EINVAL` | `include/linux/pwm.h:476-479` |

⇒ 写 `ret = pwmchip_remove(chip);` 的代码在 `CONFIG_PWM=y` 下编不过、
在 `=n` 下能编过；写 `pwmchip_remove(chip);` 的代码两边都能编过。
**只有后一种写法是可移植的**，而这一点没有任何文档说明。

### §A2.3 `devm_pwmchip_add()` 才是正确用法

`core.c:347-356`：

```c
ret = pwmchip_add(chip);
if (ret)
	return ret;
return devm_add_action_or_reset(dev, devm_pwmchip_remove, chip);
```

树内 **44 处 `pwmchip_add` vs 32 处 `devm_pwmchip_add`**（PROGRESS 基线），
新驱动一律该用 devm 版。⚠️ 注意 devm 版的注销时机是**设备 devm 回收时**，
排在驱动 `remove()` 回调**之后** ⇒ 驱动在 `remove()` 里还能安全访问 `chip`。

---

## §A3 三条发现路径

`pwm_get()`（`core.c:900-1008`）是一个三分支函数：

```
pwm_get(dev, con_id)
 ├─ is_of_node(fwnode)   :913-914  → of_pwm_get()  【arm64 的唯一实际路径】
 ├─ is_acpi_node(fwnode) :917-921  → acpi_pwm_get()  ← 本文档集裁掉，见 99 §2.7
 └─ 都不是               :943-1007 → 静态查表 pwm_lookup_list
```

⚠️ 注意 ACPI 分支的返回条件（`:919-920`）：
`if (!IS_ERR(pwm) || PTR_ERR(pwm) != -ENOENT) return pwm;`
⇒ **只有 ACPI 返回 `-ENOENT` 才会继续往下走查表**，其他错误直接返回。
DT 分支没有这个"继续"逻辑——`is_of_node()` 为真就**无条件 return**，
不会回退到静态查表。

### §A3.1 DT 路径：`of_pwm_get()`

`core.c:737-798`：

```
 1. con_id 非空？    :747-751  of_property_match_string(np, "pwm-names", con_id)
                              失败 → 直接返回该错误码（不回退到 index 0）
 2. 解析 phandle     :753-758  of_parse_phandle_with_args(np, "pwms", "#pwm-cells", index)
                              失败 → pr_err "can't parse \"pwms\" property"
 3. 找 chip          :760-767  fwnode_to_pwmchip()  找不到 → -EPROBE_DEFER
                              ⚠️ 只有非 EPROBE_DEFER 才打 "PWM chip not found"
 4. 翻译 + request   :769-771  pc->of_xlate(pc, &args)  ← 内部会 request
 5. 建 device link   :773-779  失败则 pwm_free(pwm) 回滚
 6. 补 label         :786-793  con_id → pwm-names[index] → np->name
 7. of_node_put      :796      唯一出口，goto put 都汇到这里
```

四条要点：

1. **`con_id` 非空就要求 DT 里有 `pwm-names`**。`of_property_match_string()`
   失败返回的通常是 `-EINVAL`（属性不存在）或 `-ENODATA`。
   ⚠️ 官方文档 `Documentation/driver-api/pwm.rst` 也强调
   "the `pwm-names` property becomes mandatory for devices that look up
   the PWM device via the con_id parameter"（kerneldoc `core.c:728-732` 同义）。
   而 **arm64 DTS 里只有 4 个文件写了 `pwm-names`**（PROGRESS 基线，
   对比 112 个文件写了 `pwms =`）⇒ **绝大多数消费者必须传 `con_id = NULL`**。
2. **`-EPROBE_DEFER` 不打日志，其他错误才打**（`:762-763`）。
   这是正确的设计（defer 是正常现象），但也意味着
   "PWM chip 一直没出来"这种真实故障**完全静默**——
   驱动会无限 defer 而 dmesg 里什么都没有。
3. **request 发生在 `of_xlate` 里面**（`core.c:139` / `:167`），
   `of_pwm_get()` 自己不 request。所以第 5 步失败时要用
   `pwm_free(pwm)` 回滚，注释写得很直白：
   `/* of_xlate ended up calling pwm_request_from_chip() */`（`:775`）。
4. **`label` 的三级回退**（`:786-793`）：显式 `con_id` → `pwm-names[index]`
   → `np->name`。⇒ `/sys/kernel/debug/pwm` 里看到的名字来源于此，
   最差情况是消费者的 DT 节点名。

### §A3.2 `of_xlate` 两个实现的差别

| 实现 | 位置 | cells 要求 | index 来自 | 极性 cell |
|---|---|---|---|---|
| `of_pwm_xlate_with_flags()`（默认） | `core.c:124-152` | `of_pwm_n_cells >= 2` 且 `args_count >= 2` | `args[0]`，要 `< npwm` | `args[2]`，仅当 `of_pwm_n_cells >= 3` |
| `of_pwm_single_xlate()` | `core.c:155-178` | `of_pwm_n_cells >= 1` 且 `args_count` 为 1 或 2 | ⚠️ **恒为 0** | `args[1]`，无 cells 数门槛 |

⚠️ `of_pwm_single_xlate()` 给的是**单通道控制器**的简化绑定：
DTS 写 `pwms = <&pwm 50000>`（周期直接是第 1 个 cell，没有 index）。
两者的 cell 含义完全错位——**同一段 DTS 在两种 xlate 下解释不同**。
选哪个由 provider 驱动决定，消费者的 DTS 必须匹配 provider 的绑定文档。

⚠️ 两个 xlate 都**先 request 再填 `args`**（`:139` 然后 `:143-149`）。
⇒ `.get_state` 在 request 时被调（§A4），此时 `pwm->args` **还是全零**。
驱动的 `.get_state` 不能依赖 `pwm->args`。

### §A3.3 静态查表路径的模糊打分

`core.c:943-1007`。评分规则（注释在 `:936-941`，坦白句在 `:933`
"shamelessly taken from the clock framework"）：

| 条目 | 匹配要求 | 得分 |
|---|---|---|
| `p->dev_id` 非空 | 必须与 `dev_name(dev)` 完全相等，否则 `continue` | +2 |
| `p->con_id` 非空 | 必须与传入 `con_id` 完全相等，否则 `continue` | +1 |
| 两者都为 NULL | 通配 | 0 分 |

优先级 **dev+con(3) > dev only(2) > con only(1) > 通配(0)**。
⚠️ 一个细节（`:962-969`）：`match == 3` 时**立即 `break`**，
`match < 3` 时更新 `best` 继续扫。所以完全匹配是短路的，
其余情况要扫完整张表。

找到条目后（`:977-1005`）：
`pwmchip_find_by_name(chosen->provider)` → 找不到且 `chosen->module` 非空
则 `request_module()` 后**重试一次** → 仍找不到返回 `-EPROBE_DEFER` →
`pwm_request_from_chip(chip, chosen->index, con_id ?: dev_id)` →
建 device link → 填 `args.period` / `args.polarity`。

⚠️ `request_module()` 那段的注释（`:979-984`）自陈用途：
"can be used to work around driver load ordering issues if driver's can't be
made to properly support the deferred probe mechanism" ——
**明确写着这是 workaround**。这条路径在 arm64 上基本用不到
（`pwm_add_table()` 只被板级 setup 代码调，而且**没有导出符号**，
见 01 篇讲静态查表的那节）。

### §A3.4 `pwm_device_link_add()`：不传 `dev` 会打警告

`core.c:691-715`：

```c
if (!dev) {
	dev_warn(pwm->chip->dev,
		 "No consumer device specified to create a link to\n");   /* :702-703 */
	return NULL;                    /* ⚠️ 返回 NULL，不是错误 */
}

dl = device_link_add(dev, pwm->chip->dev, DL_FLAG_AUTOREMOVE_CONSUMER);
if (!dl) {
	dev_err(dev, "failed to create device link to %s\n", ...);     /* :709-710 */
	return ERR_PTR(-EINVAL);        /* ⚠️ 这个才是错误，会导致 pwm_get 失败 */
}
```

⚠️ 两种失败的**处理完全不同**：`!dev` 只警告并继续（`pwm_get()` 成功）；
`device_link_add()` 失败则整个 `pwm_get()` 回滚失败。

⚠️ 警告文本里说明了后果："It may impact the PM sequence ordering:
the PWM supplier may get suspended before the consumer."（`:698-700`）
⇒ **`pwm_get(NULL, ...)` 是合法的，但会丢掉 suspend 顺序保证**。
`devm_fwnode_pwm_get()` 这类接口存在的部分理由就是确保 `dev` 非空。

---

## §A4 request：占用一条通道

`pwm_device_request()`（`core.c:92-122`）是**所有获取路径的共同终点**：

```
 1. 已被占用？        :96-97    test_bit(PWMF_REQUESTED) → -EBUSY
 2. 钉住 provider 模块 :99-100   !try_module_get(ops->owner) → -ENODEV
 3. 调 .request        :102-108  失败则 module_put 后返回驱动的错误码
 4. ⚠️ 调 .get_state   :110-116  有回调就调，填 pwm->state
                                 + trace_pwm_get(pwm, &pwm->state)   :112
                                 + CONFIG_PWM_DEBUG 时 pwm->last = pwm->state
 5. 置标志 + 存 label  :118-119  set_bit(PWMF_REQUESTED) / pwm->label = label
```

三个入口都套在这个函数外面：

| 入口 | 位置 | 拿锁 | 备注 |
|---|---|---|---|
| `pwm_request(int pwm, label)` | `core.c:369-393` | `pwm_lock` | ⚠️ **已废弃**（kerneldoc `:364` 明说 "use pwm_get() instead"）；走 radix tree；找不到返回 `-EPROBE_DEFER` |
| `pwm_request_from_chip(chip, index, label)` | `core.c:406-425` | `pwm_lock` | provider 侧接口；`index >= npwm` → `-EINVAL` |
| sysfs `export_store()` | `sysfs.c:326` | 同上（间接） | label 硬编码 `"sysfs"` |

⚠️ **第 4 步是 `.get_state` 的唯一调用点**（`CONFIG_PWM_DEBUG` 的回读除外）。
所以：

1. **`.get_state` 每次 request 都会被调**，不是"开机一次"。
   ⇒ 驱动的 `.get_state` 必须可重入，且不能依赖 `pwm->args`（§A3.2）。
2. `trace_pwm_get` 这个 tracepoint 在这里触发 ⇒ 它的语义是
   **"从硬件读到了 state"**，与 `pwm_get()` 这个函数没有对应关系。
   驱动没有 `.get_state` 时，`pwm_get()` 成功也不产生任何 trace 事件（07 篇 §7.1）。
3. `.request` 在 `.get_state` **之前**调 ⇒ 驱动可以在 `.request` 里上电、
   使能时钟，保证 `.get_state` 读寄存器时硬件是活的。**顺序是对的。**

⚠️ 一条容易被老资料误导的事：`pwm_set_chip_data()` 在 6.1 里
**没有状态检查**——函数体三行（`core.c:213-221`），
`!pwm` 返回 `-EINVAL`，否则赋值返回 0。
老内核（以及网上多数示例的注释）里它检查过 `PWMF_REQUESTED`，
**6.1 已经去掉了** ⇒ 在 `.request` 里调、在 `probe()` 里对
`&chip->pwms[i]` 调，两种写法都成立。

⚠️ 但注意 `pwm_put()` 会把它清零（§A8 第 5 步）⇒
**通道被释放再重新 request 时，`chip_data` 是 NULL**。
在 `probe()` 里设一次就不管的写法，在"消费者 put 之后又 get"的场景下会拿到 NULL。
⇒ **正确做法仍然是在 `.request` 里设**，理由不是"标志检查"而是"会被清零"。

---

## §A5 `pwm_apply_state()`：完整判定链

`core.c:546-588`，**整个子系统最热的函数**：

```
pwm_apply_state(pwm, state)
 1. might_sleep()      :558     ⚠️ 声明"本函数可能睡眠"——原子上下文禁用
 2. 入参门禁            :560-562 !pwm || !state || !state->period ||
                                 state->duty_cycle > state->period → -EINVAL
 3. ⚠️ 五字段全等短路   :566-571 period/duty_cycle/polarity/enabled/usage_power
                                 全部相等 → return 0（.apply 不被调）
 4. 调驱动 .apply       :573-575 失败则直接返回，pwm->state 不更新
 5. tracepoint         :577     trace_pwm_apply(pwm, state)
 6. 存请求值            :579     pwm->state = *state   ← 存的是"请求"不是"实际"
 7. DEBUG 回读校验      :585     pwm_apply_state_debug()（默认是空操作）
```

### §A5.1 第 2 步：`period == 0` 是错误，不是"关闭"

⚠️ `!state->period` 直接 `-EINVAL`。**不能用 `period = 0` 表示关闭**——
关闭要用 `state.enabled = false`（此时 `period` 仍必须非零）。
这是新手最常撞的一个 `-EINVAL`。

⚠️ `duty_cycle > period` 也是 `-EINVAL`。所以**改配置的顺序有讲究**：
从 `50000/100000` 改到 `150000/200000`，如果分两次写
（先写 duty 再写 period），中间态 `150000/100000` 会被拒。
sysfs 路径正是这样逐属性写的，所以**用户态必须先把 period 调大**（08 篇会展开）。

### §A5.2 第 3 步：全等短路的四个后果

```c
if (state->period == pwm->state.period &&
    state->duty_cycle == pwm->state.duty_cycle &&
    state->polarity == pwm->state.polarity &&
    state->enabled == pwm->state.enabled &&
    state->usage_power == pwm->state.usage_power)
	return 0;                            /* core.c:571 */
```

| 后果 | 说明 |
|---|---|
| **无法强制刷新硬件** | 值没变就绝不调 `.apply`。硬件被外部（bootloader、看门狗、别的核）改掉了，Linux 侧再 apply 同一份 state 也不会纠正 |
| **tracepoint 也不触发** | `trace_pwm_apply` 在 `:577`，**在短路之后** ⇒ 用 trace 观察时"没有事件"可能意味着"值没变"而不是"没调用" |
| **比较的是 5 个字段全部** | 只改 `usage_power`（不影响波形）也会穿过短路去调 `.apply` ⇒ 驱动的 `.apply` 必须能处理"波形参数一个都没变"的调用 |
| **比较基准是"上次请求值"** | 不是硬件值。所以驱动 `.apply` 内部把 period 向下取整成 `3300` 之后，`pwm->state.period` 存的还是请求的 `3333`；下次再请求 `3333` 仍然短路 |

⚠️ **想强制重写硬件的唯一办法**：先 apply 一个不同的 state（比如
`duty_cycle` 改 1 ns），再 apply 目标 state。没有官方的"force"标志。

### §A5.3 第 6 步是"存请求值"的源头

`pwm->state = *state;`（`core.c:579`）——存的是调用者传进来的那份，
**不是从硬件回读的**。这一行就是 01 篇 §2.4 那个陷阱
（`pwm_get_state()` 不是硬件状态）的全部机制。

官方文档对此的表述（`Documentation/driver-api/pwm.rst:68-72`）：

> There is currently no way for consumers to get the actually implemented settings.

⚠️ 注意 `:581-584` 的注释解释了为什么第 7 步在第 6 步之后：
"only do this after pwm->state was applied as some implementations of
.get_state depend on this" ⇒ **有驱动的 `.get_state` 会去读 `pwm->state`**，
这本身是个循环依赖的味道（回读函数依赖被回读的缓存值），
但核心通过固定顺序把它变成了确定行为。

---

## §A6 接管 bootloader 状态：两个函数选哪个

启动时 bootloader 常已经在输出 PWM（背光已亮）。Linux 接管时有两个选择：

| 函数 | 位置 | 会不会关输出 | 做什么 |
|---|---|---|---|
| `pwm_apply_args()` | `include/linux/pwm.h:522`（内联，非导出） | ⚠️ **会**（`enabled = false`） | 复位到板级默认：period/polarity 取 `args`，`usage_power = false` |
| `pwm_adjust_config()` | `core.c:626-671`（导出） | **不会** | 保持当前占空比**比例**，把周期换算到 `args.period` |

### §A6.1 `pwm_adjust_config()` 的三段逻辑

```
 1. 取 args 与 state    :631-632  pwm_get_args + pwm_get_state
 2. period == 0？       :642-648  ⇒ 驱动没实现 .get_state，或还没配过
                                  duty = 0, period/polarity 取 args → apply
 3. 周期不同？          :654-660  按比例缩放 duty：duty * args.period / state.period
                                  （u64 乘法后 do_div，避免溢出）
 4. 极性不同？          :665-668  改极性并把 duty 取补：duty = period - duty
 5. apply              :670
```

⚠️ 第 2 步的注释（`:635-641`）说明了 `period == 0` 的**两种含义无法区分**：
"either the PWM driver does not support initial state retrieval or the PWM has
not yet been configured"。两种情况的正确处理其实不同
（前者应该保持输出、只调周期；后者应该从 0 开始），
但核心没有信息区分，一律按后者处理 ⇒ **驱动不实现 `.get_state` 时，
`pwm_adjust_config()` 会把占空比清零 ⇒ 背光会灭一下。**

⇒ 这是"实现 `.get_state` 强烈建议"的最实际理由：不为了查询，
而是为了**无闪烁接管**。

⚠️ 第 4 步的极性取补（`state.duty_cycle = state.period - state.duty_cycle`）
是在**已经缩放过周期之后**做的，顺序正确。但注意它**没有边界检查**——
若 `duty_cycle > period`（理论上不该发生），结果会下溢成巨大的 `u64`，
然后被 `pwm_apply_state()` 第 2 步的 `duty_cycle > period` 门禁挡住返回 `-EINVAL`。
不会崩，但错误码会指向错误的方向。

---

## §A7 `pwm_capture()`：唯一持锁调驱动的路径

`core.c:599-615`，全文 17 行：

```c
if (!pwm || !pwm->chip->ops)
	return -EINVAL;
if (!pwm->chip->ops->capture)
	return -ENOSYS;                     /* :608 */

mutex_lock(&pwm_lock);                  /* :610 ⚠️ 全局锁 */
err = pwm->chip->ops->capture(pwm->chip, pwm, result, timeout);
mutex_unlock(&pwm_lock);                /* :612 */
```

⚠️ **`mutex_lock(&pwm_lock)` 横跨整个驱动回调。** 后果：

| 影响 | 说明 |
|---|---|
| 一个慢 capture 卡住全系统 pwm | `pwmchip_add` / `pwmchip_remove` / 所有 request/put 全部等在 `pwm_lock` 上 |
| sysfs 侧的 timeout 是 **1 秒** | `pwm_capture` 的 `timeout` 参数由 sysfs 层传入 ⇒ **最坏情况全系统 pwm 阻塞 1 秒** |
| 与 `pwm_apply_state()` 完全不加锁形成鲜明反差 | 见 §B1 |

⚠️ `-ENOSYS`（38）在这里的用法值得注意：`-ENOSYS` 的标准含义是
"函数未实现（系统调用不存在）"，用来表示"这个驱动没有 capture 能力"
是**不规范**的——规范做法是 `-EOPNOTSUPP`。
而这个错误码会**直接传到用户态**（读 `capture` sysfs 属性时），
用户态看到的是 `Function not implemented`。见 §C2.3。

---

## §A8 释放：`pwm_put()`

`core.c:1015-1036`：

```
 1. !pwm 直接返回        :1017-1018
 2. 拿全局锁             :1020
 3. test_and_clear_bit   :1022-1025  没置位过 → pr_warn "PWM device already freed\n" 并跳出
 4. 调 .free             :1027-1028
 5. 清 chip_data 与 label :1030-1031  pwm_set_chip_data(pwm, NULL) / pwm->label = NULL
 6. module_put            :1033
```

四条要点：

1. ⚠️ **不关输出。** 通篇没有 `pwm_disable()` 或 `.apply`。
   ⇒ `pwm_put()` 之后硬件继续按上次的 state 输出。
   消费者必须自己先 `pwm_disable()`。
   `devm_pwm_get()` 的自动释放路径（`devm_pwm_release`，`core.c:1039`）
   同样只调 `pwm_put()`，**也不关输出**。
2. **重复 put 只打一条 `pr_warn`**，不崩、不返回错误（函数是 `void`）。
   ⚠️ 日志文本 `"PWM device already freed\n"` 用的是 `pr_warn` 而非 `dev_warn`
   ⇒ **消息里没有设备名**，多个 PWM 的系统上无法定位是哪一个。
3. 第 5 步的 `pwm_set_chip_data(pwm, NULL)` **确实生效**——该函数在 6.1 里
   没有状态检查（§A4 末），所以 `pwm->chip_data` 被清零。
   ⇒ 通道被 put 再重新 get 时私有指针是 NULL，**驱动的 `.request`
   必须每次重新设置**。在 `probe()` 里设一次就不管的写法在这种场景下会取到 NULL。
4. ⚠️ 第 6 步 `module_put(pwm->chip->ops->owner)` 在第 3 步的 goto 路径上
   **不会执行** ⇒ 重复 put 不会造成模块引用计数下溢。这一点是对的。

---

## §A9 sysfs 的 export / unexport

### §A9.1 export：`echo N > /sys/class/pwm/pwmchipM/export`

`export_store()`（`sysfs.c:310-334`）→ `pwm_export_child()`（`:241-278`）：

```
 1. 解析数字            :319-321  kstrtouint
 2. 范围检查            :323-324  hwpwm >= chip->npwm → -ENODEV
                                  ⚠️ 写的是 hwpwm（本地编号），不是全局编号
 3. request             :326-328  pwm_request_from_chip(chip, hwpwm, "sysfs")
                                  ⚠️ 已被内核消费者占用 → -EBUSY
 4. 建子设备            :330      pwm_export_child()
    4a. 置 EXPORTED     :247-248  test_and_set_bit → 已置位则 -EBUSY
    4b. 分配 pwm_export :250-254  kzalloc 失败则清标志返回 -ENOMEM
    4c. 初始化          :256-263  child.release / parent / groups
                                  dev_set_name(&export->child, "pwm%u", pwm->hwpwm)
    4d. device_register :265-271  失败则清标志 + put_device
    4e. 发 uevent       :272-275  "EXPORT=pwm%u"
 5. 失败回滚            :331-332  pwm_put(pwm)
```

⚠️ **两处 `-EBUSY` 来源不同**：第 3 步是"内核消费者占着"，
第 4a 步是"已经 export 过了"。用户态看到的 errno 一样，
排查要靠"`/sys/class/pwm/pwmchipM/pwmN/` 目录在不在"来区分。

⚠️ **`export` / `unexport` 写的是 `hwpwm`（0 .. npwm-1），
而目录名 `pwmchipM` 里的 M 是全局 `base`。** 同一条命令里两个口径
（02 篇 §1.0.C）：`echo 0 > /sys/class/pwm/pwmchip4/export` 是合法的，
产生 `/sys/class/pwm/pwmchip4/pwm0/`。

### §A9.2 unexport：不对称的一点

`pwm_unexport_child()`（`sysfs.c:285-308`）：

```
 1. test_and_clear_bit(PWMF_EXPORTED)   :290-291  没置位 → -ENODEV
 2. device_find_child                   :293-295  找不到 → -ENODEV
 3. 发 uevent "UNEXPORT=pwm%u"          :297-300
 4. put_device（配 find_child 的引用）   :303
 5. device_unregister                   :304
 6. pwm_put(pwm)                        :305      ⚠️ 释放占用
```

⚠️ **unexport 不关输出。** 第 6 步只是 `pwm_put()`（§A8）⇒
`echo 0 > unexport` 之后**PWM 继续输出**。
用户态想真正停下来必须先 `echo 0 > pwmN/enable`。

⚠️ 第 1 步与第 2 步之间有一个窗口：标志已清但子设备还在。
两个并发的 unexport 里只有一个能穿过第 1 步，另一个得 `-ENODEV`
⇒ 这是正确的互斥，靠的是 `test_and_clear_bit` 的原子性，不靠锁。

### §A9.3 通道级属性的写入语义

5 个属性（`pwm_attrs[]`，`sysfs.c:224-231`）——⚠️ 比官方文档
（`Documentation/driver-api/pwm.rst:86-129` 只列 4 个）多一个 `capture`：

| 属性 | 权限 | 读什么 | 写什么 |
|---|---|---|---|
| `period` | RW | `state.period`（纳秒） | 改 period 后立刻 apply |
| `duty_cycle` | RW | `state.duty_cycle`（纳秒） | 同上 |
| `enable` | RW | `state.enabled`（0/1） | 同上 |
| `polarity` | RW | `"normal"` / `"inversed"` | 同上 |
| `capture` | ⚠️ **RO** | ⚠️ **读一次触发一次硬件测量** | — |

⚠️ 每个写操作都是"读当前 state → 改一个字段 → 整体 apply"
（各 store 函数都以 `pwm_get_state()` 开头，如 `sysfs.c:43` `:78` `:113` `:162`）
⇒ **逐属性写会产生中间态**，见 §A5.1 的 `duty > period` 陷阱。

⚠️ `capture` 是**读有副作用**的属性：`cat capture` 会调
`pwm_capture()`（§A7），持全局锁最多 1 秒，驱动不支持时返回 `-ENOSYS`。
**它不在官方文档里**，也就是说没有 ABI 承诺。

---

## §A10 suspend / resume

pwm 的 PM 全部在 class 层（`sysfs.c`），**核心 `core.c` 里一行都没有**：

| 函数 | 位置 | 覆盖范围 |
|---|---|---|
| `pwm_class_suspend()` | `sysfs.c:443` | ⚠️ **只遍历已 export 的通道** |
| `pwm_class_resume()` | `sysfs.c:484` | 同上 |
| `pwm_class_get_state()` | `sysfs.c:377` | 取 `pwm_export` 并**加它的 `lock`** |
| `pwm_class_apply_state()` | `sysfs.c:400` | apply 后释放 `pwm_class_get_state` 拿的锁 |
| `pwm_class_resume_npwm()` | `sysfs.c:412` | resume 的逐通道循环 |
| `DEFINE_SIMPLE_DEV_PM_OPS` | `sysfs.c:491` | 挂到 `pwm_class`（`:493-498`） |

三条结论：

1. ⚠️ **内核消费者的 PWM 状态跨 suspend 不保证保持。**
   核心不保存、不恢复。这与官方文档
   `Documentation/driver-api/pwm.rst:82-84` 的要求一致：
   > All consumers should really be reconfiguring the PWM upon resume
2. ⚠️ **驱动不该实现 PM。** 官方文档 `:162-163` 明说
   "Drivers should not implement any power management"——
   状态恢复是消费者的责任。
3. **sysfs export 出来的通道是例外**：它们的 state 被存进
   `pwm_export->suspend`（02 篇 §8）并在 resume 时恢复。
   ⇒ **同一块硬件，用户态控制的通道会自动恢复，内核驱动控制的不会。**
   这个不对称是 pwm PM 语义里最容易误判的一处。

---

# Part B：横切机制

## §B1 锁：两把 mutex，覆盖极不均匀

| 锁 | 位置 | 保护 | 谁拿 |
|---|---|---|---|
| `pwm_lock` | `core.c:30` | `pwm_chips` 链表 · `allocated_pwms` 位图 · `pwm_tree` | 注册/注销、所有 request/put、capture、debugfs 读 |
| `pwm_lookup_lock` | `core.c:28` | `pwm_lookup_list` | `pwm_add_table` / `pwm_remove_table` / `pwm_get` 的查表分支 |
| `pwm_export->lock` | `sysfs.c:257` | ⚠️ 单条已 export 通道的 sysfs 操作 | sysfs 各 store/show、class PM |

两把全局锁**从不嵌套**（`pwm_get()` 的 DT 分支和查表分支互斥，
`fwnode_to_pwmchip()` 拿完 `pwm_lock` 就放，`:678-686`）⇒ **无锁序问题**。

### §B1.1 哪些路径持锁调驱动回调

这是判断"驱动回调能做什么"的关键：

| 路径 | 持 `pwm_lock` 调驱动？ | 回调 |
|---|---|---|
| `pwm_device_request()` | ⚠️ **是**（调用者已持锁） | `.request` · `.get_state` |
| `pwm_put()` | ⚠️ **是** | `.free` |
| `pwm_capture()` | ⚠️ **是**（`core.c:610-612`） | `.capture` |
| **`pwm_apply_state()`** | ⚠️ **完全不持任何锁** | `.apply` |
| debugfs 读 | **是**（`pwm_seq_start` 拿锁，`core.c:1139`；`pwm_seq_stop` 放，`:1154`） | 不调驱动（只读 `pwm->state`） |

⇒ 对驱动作者的两条硬约束：

1. **`.request` / `.free` / `.capture` / `.get_state` 里不能调任何
   会再次拿 `pwm_lock` 的 pwm 核心 API**——即不能调 `pwm_get()`、
   `pwm_put()`、`pwm_request_from_chip()`、`pwmchip_add()`。⚠️ 会死锁。
   （能调 `pwm_apply_state()`，因为它不拿锁——但那会递归进 `.apply`，
   另一个问题。）
2. **`.apply` 必须自己处理并发**。核心不串行化它。两个线程同时对同一条通道
   apply，`pwm->state` 的读改写（`:566` 的比较和 `:579` 的赋值）也不是原子的。

### §B1.2 官方文档承认这套锁不对

`Documentation/driver-api/pwm.rst:165-172` 的 Locking 一节自陈
这套锁语义"should be fixed soon"。这是 pwm 唯一的自陈设计债——
子系统里 `FIXME/TODO/XXX` 一处都没有（PROGRESS 基线取证），
债全部登记在文档而不是代码里。完整评价在 §C1。

⚠️ 实践上的判断：**最热的 `.apply` 零加锁其实是有意的**——
`pwm_apply_state()` 每帧都可能被调（背光渐变），加全局锁会成瓶颈。
问题不在"apply 不加锁"，而在"没有 per-chip 或 per-channel 的锁供它用"。
消费者只能自己在上层互斥，或者依赖"一条通道只有一个消费者"这个由
`PWMF_REQUESTED` 保证的事实——**后者其实基本够用**，这也是这条债
一直没被真正修掉的原因。

---

## §B2 编号分配：`chip->base` 是输出

### §B2.1 分配算法

`alloc_pwms()`（`core.c:40-51`）：

```c
start = bitmap_find_next_zero_area(allocated_pwms, MAX_PWMS, 0, count, 0);
if (start + count > MAX_PWMS)
	return -ENOSPC;
return start;
```

⚠️ **函数只有 `count` 一个参数，没有"期望的 base"。**
`pwmchip_add()` 拿到返回值后 `chip->base = ret;`（`core.c:276`）**无条件覆盖**。

⇒ 三条后果：

1. **驱动无法钉住自己的编号。** 树内有些老驱动仍在 probe 里写
   `chip->base = pdev->id;`——**那一行在 6.1 里是死代码**。
2. **`pwmchipN` 的 N 依赖 probe 顺序。** 同一块板子换个内核版本、
   改个 `initcall` 顺序、把某个驱动从内建改成模块，编号就会变。
   ⚠️ **用户态脚本里硬编码 `/sys/class/pwm/pwmchip0` 是不可靠的**——
   正确做法见 07 篇（按 `of_node` 或 `device` 符号链接找）。
3. **需要连续空位** ⇒ 反复加载/卸载不同 `npwm` 的驱动理论上会产生碎片，
   在总数没满时就 `-ENOSPC`。实践中 `MAX_PWMS = 1024`（`core.c:26`）
   远大于真实系统的通道数，碰不到。

### §B2.2 `MAX_PWMS` 是全系统上限

不是每 chip 的上限。整机所有 PWM 通道加起来超过 1024 就注册失败。
带 16 路的 I2C 扩展芯片（pca9685）挂 64 片到顶。⚠️ 这个数字**不可配置**，
是 `core.c:26` 的一个 `#define`。

---

## §B3 引用与生命周期：只有模块级保护

| 机制 | 粒度 | 挡住什么 |
|---|---|---|
| `try_module_get(ops->owner)` / `module_put` | **模块** | `rmmod` provider（有通道被 request 时失败） |
| `PWMF_REQUESTED` | 通道（布尔，非计数） | 第二个消费者 request 同一通道 → `-EBUSY` |
| `PWMF_EXPORTED` | 通道（布尔） | 重复 export → `-EBUSY` |
| `device_link_add(..., DL_FLAG_AUTOREMOVE_CONSUMER)` | 设备 | **只保证 PM 顺序**，不阻止 unbind |
| `of_node_get()` / `of_node_put()` | DT 节点 | 节点被释放 |
| `pwm_export` 内嵌的 `struct device` 的 kref | sysfs 子设备 | sysfs 目录在使用中被释放 |

⚠️ **`pwm_device` 和 `pwm_chip` 都没有引用计数。**
`chip->pwms` 是一次 `kcalloc` 的数组，`pwmchip_remove()` 里 `kfree` 掉
（§A2.1）。消费者手里的裸指针没有任何保护。

⇒ **完整的安全论证链**（也是它的薄弱点）：

```
消费者持有 pwm_device *
  ← 只要它没 pwm_put()，PWMF_REQUESTED 就是置位的
  ← 置位期间 provider 模块被 try_module_get() 钉住
  ← 所以 rmmod 失败，模块的 .text 和 chip 结构不会消失
  ⚠️ 但 chip 结构是 devm 分配的、绑在 platform_device 上
  ⚠️ unbind / 热插拔 / 驱动自己 remove 都不经过模块引用计数
  ⇒ pwmchip_remove() 照样跑，chip->pwms 照样被 kfree
  ⇒ 悬空指针
```

⚠️ **可复现**：`echo <dev> > /sys/bus/platform/drivers/<pwm-drv>/unbind`
而背光正在用这条 PWM。这不是理论问题。
⚠️ provider 是内建的（Kconfig 写 `bool` 而非 `tristate`）时，
`try_module_get(NULL)` 恒成功，这层保护**完全不存在**。

---

## §B4 `CONFIG_PWM_DEBUG`：会改变执行路径的调试开关

`drivers/pwm/Kconfig:36-43`，依赖 `DEBUG_KERNEL`。它打开
`pwm_apply_state_debug()`（`core.c:440-539`，**100 行**）。

⚠️ **这不是"只多打日志"的开关，它会额外调用驱动回调、重新 apply 一次。**

### §B4.1 它做什么

```
pwm_apply_state_debug(pwm, state)     ← 在 pwm_apply_state() 成功之后调（:585）
 1. 未开 DEBUG 直接返回     :448-449
 2. 无 .get_state 直接返回  :452-453   注释：No reasonable diagnosis possible
 3. ⚠️ 回读硬件            :460-461   ops->get_state(&s1) + trace_pwm_get
 4. 极性归一化             :468-475   驱动"反相 + duty 取补"的等价实现，撤销后再比
 5. 七项检查（dev_warn）    :477-513   见下表
 6. ⚠️ 再 apply 一次 s1     :516-521   失败 → dev_err "failed to reapply current setting"
 7. trace_pwm_apply(&s1)   :523
 8. ⚠️ 再回读一次到 last     :525-526   ops->get_state(chip, pwm, last) + trace_pwm_get
 9. 幂等性检查（dev_err）   :529-538   "apply is not idempotent"
```

七条 `dev_warn` 检查（全部在 `state->enabled` 为真时才查，除第 1、7 条）：

| 检查 | 消息 | 位置 | 判据 |
|---|---|---|---|
| 极性被忽略 | `.apply ignored .polarity` | `:479` | 归一化后极性还不对，且 `duty < period` |
| 周期没取到最优 | `.apply didn't pick the best available period (requested/applied/possible)` | `:486` | 上次实现过一个更接近的周期 |
| 周期没向下取整 | `.apply is supposed to round down period` | `:491` | 实现的周期 > 请求的 |
| 占空比没取到最优 | `.apply didn't pick the best available duty cycle` | `:500` | 同周期下上次实现过更接近的 |
| 占空比没向下取整 | `.apply is supposed to round down duty_cycle` | `:507` | 实现的 duty > 请求的 |
| 关不掉 | `requested disabled, but yielded enabled with duty > 0` | `:513` | 请求 disabled 但回读是 enabled 且 duty>0 |
| 不幂等 | `.apply is not idempotent (...) -> (...)` | `:534`（**`dev_err`**） | 重新 apply 回读值后结果又变了 |

外加一条 `dev_err`：`failed to reapply current setting`（`:519`）。

### §B4.2 三条使用注意

1. ⚠️ **它把"驱动向下取整"定为规范**。`.apply` 应当选
   **不超过请求值的最大可实现值**——这是 pwm 的取整契约，
   但**契约本身只写在这个调试函数的检查逻辑里**，
   官方 rst 和 kerneldoc 都没有正式声明。⇒ 写驱动前应该读这 100 行。
2. ⚠️ **它是唯一写 `pwm->last` 的地方**（`:525`，加上 request 时的 `core.c:115`）。
   `CONFIG_PWM_DEBUG=n` 时 `pwm->last` 是死字段（02 篇 §1.0.B）。
3. ⚠️ **它改变时序**：每次 apply 变成 `apply → get_state → apply → get_state`。
   对时序敏感的负载（舵机、精确脉宽）**开了这个选项行为会不同**。
   排查"开了 DEBUG 就好了/就坏了"要先想到这条。

### §B4.3 `pwm_ops_check()` 的唯一 DEBUG 依赖

`core.c:236-247`：只有 `.apply` 是硬性要求（`:240-241`）；
缺 `.get_state` 只在 `IS_ENABLED(CONFIG_PWM_DEBUG)` 下打
`"Please implement the .get_state() callback\n"`（`:244-245`）。
⇒ **默认配置下缺 `.get_state` 完全静默**，而它的实际代价是
`pwm_adjust_config()` 会闪一下屏（§A6.1）。

---

## §B5 device link 与 PM 顺序

`pwm_device_link_add()`（`core.c:691-715`）在两条获取路径上被调：
`of_pwm_get()`（`:773`）和 `pwm_get()` 的查表分支（`:998`）。

```c
dl = device_link_add(dev, pwm->chip->dev, DL_FLAG_AUTOREMOVE_CONSUMER);
```

⇒ 消费者是 supplier 的**消费方**：suspend 时消费者先睡，resume 时后醒。
`DL_FLAG_AUTOREMOVE_CONSUMER` 表示消费者 unbind 时链接自动消失。

⚠️ **link 只管 PM 顺序，不管移除顺序。** provider 被 unbind 时，
device link 不会阻止 `pwmchip_remove()`（§B3）。

⚠️ **`pwm_get(NULL, con_id)` 会丢掉这层保证**并打
`dev_warn "No consumer device specified to create a link to\n"`（`:702-703`）。
这条警告的主体是 **provider 的 dev**（`pwm->chip->dev`），
不是消费者 ⇒ dmesg 里看到的是"PWM 控制器在抱怨"，
容易误判成 provider 的问题。

---

## §B6 可观测面：2 个 tracepoint + 1 个 debugfs 文件

（完整用法在 07 篇，这里只给机制。）

### §B6.1 tracepoint

`include/trace/events/pwm.h`：一个 `DECLARE_EVENT_CLASS(pwm)`（`:11`）
派生两个事件——`pwm_apply`（`:39`）和 `pwm_get`（`:47`）。
`TP_STRUCT__entry`（`:17-23`）有 **5 个字段**：

| 字段 | 来源 |
|---|---|
| `pwm` | `struct pwm_device *` 指针本身（`TP_printk` 用 `%p`，`:33`） |
| `period` / `duty_cycle` | `state->period` / `state->duty_cycle` |
| `polarity` | `state->polarity` |
| `enabled` | `state->enabled` |

⚠️ **`usage_power` 不在 trace 里。** `pwm_state` 有 5 个字段，
tracepoint 记 4 个 + 指针。改 `usage_power` 会穿过全等短路去调 `.apply`，
但 trace 里看不出差别。

⚠️ **两个事件的触发点与名字不对应**：

| 事件 | 触发点 | 语义 |
|---|---|---|
| `pwm_apply` | `pwm_apply_state()` 的 `:577`（**短路之后**）· `pwm_apply_state_debug()` 的 `:523` | "调了 `.apply` 且成功" |
| `pwm_get` | `pwm_device_request()` 的 `:112` · `pwm_apply_state_debug()` 的 `:461` 和 `:526` | ⚠️ **"从硬件读到了 state"，不是"消费者拿到了 pwm"** |

⇒ 两条推论：
1. **`pwm_apply` 事件缺失有两种含义**："值没变（短路）"或"真的没调用"。
   无法从 trace 本身区分。
2. **`pwm_get` 与 `pwm_get()` 函数无关**。驱动没有 `.get_state` 时，
   `pwm_get()` 成功也不产生任何事件；反之开了 `CONFIG_PWM_DEBUG` 后
   每次 apply 会额外产生 **2 个 `pwm_get` 事件**（`:461` 和 `:526`）。

### §B6.2 debugfs

`/sys/kernel/debug/pwm`，**一个全局只读文件**
（`debugfs_create_file("pwm", S_IFREG | 0444, NULL, NULL, ...)`，`core.c:1182-1183`，
`subsys_initcall(pwm_debugfs_init)` 在 `:1187`，整段在 `CONFIG_DEBUG_FS` 下）。

seq 实现遍历 `pwm_chips`：`pwm_seq_start`（`:1137`）**拿 `pwm_lock`**，
`pwm_seq_stop`（`:1152`）放锁 ⇒ **读这个文件期间全系统 pwm 注册/request 被阻塞**。
文件很小，实际影响可忽略，但机制上要知道。

每个 chip 一行（`pwm_seq_show`，`:1161-1164`）：
`"<bus>/<dev-name>, N PWM devices"`（`chip->dev->bus` 为空时打 `"no-bus"`）。
每条通道一行（`pwm_dbg_show`，`:1107-1135`）：

```
 pwm-0   (backlight           ): requested enabled period: 50000 ns duty: 25000 ns polarity: normal
```

⚠️ 三个读法要点：

1. **行首的编号是 `hwpwm`（本地），不是全局编号**（`:1117` 的 `i`）。
2. **显示的是 `pwm->state`，即上次请求值**（`:1115` 调 `pwm_get_state()`）
   ⇒ **不是硬件实际值**。
3. `usage_power` 只在为真时才打一个词（`:1130-1131`）
   ⇒ 这是**唯一能观测 `usage_power` 的地方**（sysfs 和 trace 都没有它）。
4. `label` 用 `%-20.20s` 截断（`:1117`）⇒ **超过 20 字符的消费者名字会被截掉**，
   DT 路径下 label 常是节点名，容易撞上。

---

# Part C：设计评价

先说结论：**pwm 是一个小而干净的框架**（核心 1734 行，17 个导出符号，
一个必填回调），**但它的文档比它的代码旧得多**。
`drivers/pwm/core.c` + `sysfs.c` + `include/linux/pwm.h` 里
`FIXME`/`TODO`/`REVISIT`/`HACK`/`XXX` **一处都没有**——
这不代表没有债，只代表**债全部登记在 `Documentation/driver-api/pwm.rst` 里，
而那篇文档 179 行里有相当一部分描述的是已经不存在的代码**。
读 pwm 的正确顺序是**代码优先，rst 当史料**。

## §C1 官方自陈的设计债

### §C1.1 锁语义（rst 自陈"should be fixed soon"）

`pwm.rst:167-172`：

> Currently the PWM core does not enforce any locking to pwm_enable(),
> pwm_disable() and pwm_config(), so the calling context is currently driver
> specific. This is an issue derived from the former barebone API and
> **should be fixed soon**.

⚠️ 这段话本身就是债的一部分：它点名的三个函数在 6.1 里
**已经不是驱动回调，而是消费者侧的 inline 包装**
（`include/linux/pwm.h:325` / `:351` / `:370`，全部转成 `pwm_apply_state()`）。
所以这句话该读成：**"`pwm_apply_state()` 这条最热的路径不加任何锁，
调用上下文由驱动自己负责"**——这一点今天仍然成立（§B1.1）。

评价：**债是真的，但严重性被高估了**。见 §B1.2——`PWMF_REQUESTED`
保证一条通道只有一个内核消费者，事实上消除了大部分竞争。
真正暴露的是 sysfs 路径（`pwm_export->lock` 只护单条通道）
和"同一消费者多线程 apply"这两种情况。
"should be fixed soon" 至少写了十年，还没修，说明它在实践中不痛。

### §C1.2 消费者拿不到硬件实际值（rst 自陈）

`pwm.rst:68-72` 承认：**"There is currently no way for consumers to get the
actually implemented settings"**。`pwm_get_state()` 返回的是上次请求值
（§A5.3），`.get_state` 只在 request 时被调一次（§A1.1）。

后果链条：
- 消费者无法验证"我要的 50000 ns 周期硬件到底给了多少"；
- 需要精确值的场景（舵机、音频）只能自己按数据手册反推分频器；
- `pwm_apply_state()` 的全等短路（§A5.2）让"重新 apply 一遍看看"这条
  常规排查手段也失效。

⚠️ 唯一能看到实现值的途径是 **`CONFIG_PWM_DEBUG`**（§B4）——
一个调试选项承担了本该属于 API 的能力。**这是 pwm 最实质的一处设计缺口。**

### §C1.3 全局编号层已被弃用但还在

三个还活着的弃用件：

| 件 | 位置 | 状态 |
|---|---|---|
| `pwm_request(int pwm_id, ...)` | `core.c:394` | kerneldoc 明写 `This function is deprecated, use pwm_get() instead.`（`:364`） |
| `pwm_free()` | `core.c:438` | 明写 `deprecated, use pwm_put() instead.`（`:432`） |
| `pwm_tree` radix tree | `core.c:33` | **只为 `pwm_request()` 的 id→device 查找而存在** |
| `chip->base` / `pwmchipN` | `core.c:276` · `sysfs.c:514` | 全局编号本身，rst:17 称之为 "legacy PWM API" |

⚠️ **`pwm_free()` 的处境最尴尬**：它被标为弃用，
却仍然在核心内部被调用（`of_pwm_get()` 的回滚路径 `core.c:776`），
而且它就是 `pwm_put()` 的一行别名（`core.c:434-437`）。
新驱动不该用它，但它不会消失。

**没被弃用却应该被弃用的是 `chip->base`**：它是 `pwmchipN` 目录名的来源
（`sysfs.c:514`），所以只要 sysfs ABI 还在，全局编号就删不掉——
**用户态 ABI 把一个内部遗留设计钉死了**（§B2.1 的第 2 条后果）。

### §C1.4 `request_module()` 是自陈的 workaround

`core.c:979-984` 的注释：

> If the lookup entry specifies a module, load the module and retry the PWM
> chip lookup. This can be used to **work around driver load ordering issues**
> if driver's can't be made to properly support the deferred probe mechanism.

⚠️ 一个框架在自己的查找路径里 `request_module()` 去救驱动的加载顺序，
是明确的设计妥协。仅静态查表路径有（`pwm_get()`），DT 路径靠
`-EPROBE_DEFER`（§A3.1），后者才是正解。
（顺带：这段注释里的 `driver's` 是 `drivers` 的笔误，原样保留。）

## §C2 陈旧文档与不一致

这一节是 pwm 最需要警惕的部分——**五处文档与代码直接矛盾**。

### §C2.1 `pwmchip_remove()` 的 kerneldoc 与原型不符

kerneldoc（`core.c:314-322`）说它"may return busy"并声明
`Returns: 0 on success or a negative error code on failure`，
**但原型是 `void`**（`:323`），函数体里也**没有任何 `PWMF_REQUESTED` 检查**。
详见 §A2.1 / §A2.2。⇒ **以代码为准：注销永不失败，且会 `kfree` 掉通道数组。**

### §C2.2 `.get_state` 的 kerneldoc 把调用时机写错了

`include/linux/pwm.h:267-269` 说它"only called once per PWM device when the
PWM chip is registered"。实际调用点是 `pwm_device_request()`（`core.c:110-111`），
即**每次 request 一次**；`pwmchip_add()` 的初始化循环（`core.c:284-292`）
只填 `chip`/`pwm`/`hwpwm` 并插 radix tree。详见 §A1.1。

⇒ 两条实际影响：`.get_state` **必须可重入**；它运行时 `pwm->args`
**还是全零**（xlate 在 request 之后才填 args）。

### §C2.3 `-ENOSYS` 用错了，而且会漏到用户态

`pwm_capture()` 在 `!chip->ops->capture` 时返回 **`-ENOSYS`**（`core.c:608`，
全子系统唯一一处）。按内核约定 `-ENOSYS` 表示**系统调用未实现**，
"回调未实现"应当用 `-EOPNOTSUPP`（或 `-ENOTSUPP`）。

⚠️ 这不只是洁癖问题，因为 **`capture` 是一个 sysfs 属性**
（`pwm_attrs[]`，`sysfs.c:224-231`）：

```
$ cat /sys/class/pwm/pwmchip0/pwm0/capture
cat: capture: Function not implemented
```

⇒ **用户态 `read(2)` 会拿到 `ENOSYS`**，这在 POSIX 语义下意味着
"这个系统调用不存在"，会误导脚本和 strace 排查。
绝大多数 PWM 驱动都不实现 `.capture`，所以**这是默认可见的行为**，不是边角。

### §C2.4 `CONFIG_PWM=n` 的桩函数签名不一致

| 配置 | `pwmchip_remove()` 原型 | 位置 |
|---|---|---|
| `CONFIG_PWM=y` | `void pwmchip_remove(struct pwm_chip *chip)` | `include/linux/pwm.h:392` |
| `CONFIG_PWM=n` | `static inline int` → `return -EINVAL;` | `include/linux/pwm.h:476-479` |

⇒ **只有 `pwmchip_remove(chip);`（丢弃返回值）在两种配置下都能编译。**
写 `ret = pwmchip_remove(chip);` 在 `PWM=y` 下直接编译失败。
这也解释了为什么树内驱动统一忽略它的返回值。

### §C2.5 rst 的 sysfs 属性清单少一个

`pwm.rst:110-129` 列出 4 个通道属性：`period` · `duty_cycle` ·
`polarity` · `enable`。代码里是 **5 个**——多一个 `capture`
（`sysfs.c:224-231`）。而且 `capture` 是**只读且有读副作用**的属性
（读它会触发一次硬件测量，持全局 `pwm_lock`，见 §B1.1），
这类属性本该重点说明，结果**完全没提**。

### §C2.6 rst 在鼓励实现已经不存在的回调

`pwm.rst:153-157` 说"Drivers are encouraged to implement ->apply() instead of
the legacy ->enable(), ->disable() and ->config() methods"。
6.1 的 `struct pwm_ops`（`include/linux/pwm.h:272-282`）**只有 6 个成员**，
`.enable`/`.disable`/`.config` **早已被删除**——`.apply` 不是"推荐"，
而是**唯一选项且强制**（`pwm_ops_check()`，`core.c:240-241`）。

⇒ 对新驱动作者的实际危害：读了这段会以为存在一个"简单模式"可选，
去找 `.config` 怎么写，浪费时间。

## §C3 需要警惕的行为（不是 bug，但会咬人）

| 行为 | 后果 | 出处 |
|---|---|---|
| `pwmchip_remove()` 不检查占用 + `kfree(chip->pwms)` | ⚠️ **unbind provider 时消费者持有的 `pwm_device *` 悬空，真实 UAF** | §A2.1 · §B3 |
| `pwm_put()` 不关输出 | 消费者释放后 PWM 继续跑（背光常亮） | §A8 |
| sysfs `unexport` 不关输出 | 同上，用户态视角更容易踩 | §A9.2 |
| 缺 `.get_state` 时 `pwm_adjust_config()` 会把 duty 清零 | ⚠️ **接管 bootloader 背光时闪一下黑** | §A6.1 |
| `pwm_apply_args()` 会把 `enabled` 设为 false | 调它会关输出，与 `pwm_adjust_config()` 行为不同 | 02 §11.5 |
| 五字段全等短路 | 无法强制刷新硬件；`pwm_apply` trace 也不产生 | §A5.2 |
| `#pwm-cells` 缺失时默认 2 | ⚠️ **DT 里少写 `<3>` 会静默丢掉极性** | §A1.3 |
| `period = 0` 返回 `-EINVAL` | 不是"关闭"的写法；关闭要用 `enabled=false` | §A5.1 |
| `chip->base` 是输出 | ⚠️ **用户态脚本硬编码 `pwmchip0` 不可靠** | §B2.1 |
| `CONFIG_PWM_DEBUG` 改变时序 | apply 变成 apply→read→apply→read | §B4.2 |
| `PWM_POLARITY_INVERSED`（C）vs `INVERTED`（DT） | 拼写不同，写错就是编译错误 | 02 §9 |
| `pwm_capture` 用 `unsigned int` ns（上限 ≈4.295 s） | 低频信号测量溢出；`pwm_state` 用 `u64` | 02 §11.3 |

## §C4 可质疑的设计选择

1. **`usage_power` 是一个语义危险的布尔。**
   `include/linux/pwm.h:52-57` 允许驱动**在周期内任意重分配占空**，
   只保证平均功率。对 LED/加热器合理，**对舵机、红外遥控、音频是灾难**。
   ⚠️ 而它**既没有 sysfs 属性，也不在 tracepoint 里**（§B6.1），
   唯一的观测点是 debugfs 的一个词（§B6.2）。
   ⇒ 一个能改变波形形状的开关，可观测性最差。
   评价：**默认 false 是对的，但它应该有个 sysfs 属性**。

2. **`pwm_chip` 没有 `void *priv`，没有锁字段，`owner` 放在 `pwm_ops` 里。**
   前两条迫使每个驱动 `container_of` + 自带互斥（02 §1.0.D）；
   第三条使 `chip` 无法在没有 ops 的情况下判断 provider 模块。
   评价：`container_of` 是内核惯例，可以接受；**缺 per-chip 锁是真的欠缺**（§C1.1）。

3. **静态查表用模糊打分。**
   `core.c:943-1007` 的 dev_id +2 / con_id +1 打分表，
   注释自陈 `"shamelessly taken from the clock framework"`（`:933`）。
   ⚠️ 通配条目（`match == 0`）会**兜住所有查不到的请求**，
   把"配置写错"变成"拿到了错误的通道"。
   评价：**在 DT 平台上这条路径基本用不到，属于历史包袱**，
   arm64 上应当只走 DT。

4. **`pwm_lock` 一把全局锁护三样东西**（链表 + 位图 + radix tree），
   还护 `.request`/`.free`/`.capture` 三个驱动回调。
   ⚠️ 后果是 `.capture` 期间**全系统 pwm 注册和 request 都被阻塞**——
   而 `.capture` 的 `timeout` 参数意味着它**可以睡很久**。
   评价：**这是比"apply 不加锁"更值得修的一处**，
   因为它把一个慢操作放进了全局临界区。

5. **调试能力藏在编译选项后面。**
   "驱动应当向下取整"这条契约只写在 `pwm_apply_state_debug()`
   的检查逻辑里（§B4.2），rst 和 kerneldoc 都没有正式声明。
   评价：**规范应当写在文档里，而不是只体现在一个 `dev_warn` 的判据里。**

## §C5 整体评价

**优点**（相对同规模框架）：

- **原子配置**：`pwm_state` 一次性描述完整配置，`.apply` 一次生效，
  没有"先设周期再设占空比"的中间态。这是 pwm 相对老 API 最大的进步，
  也是 rst:153-157 那段话真正想说的事。
- **回调面极小**：6 个成员、1 个必填。写一个能用的 PWM 驱动只需要 `.apply`。
- **`pwm_args` 把"板级参考值"和"当前状态"分开**（02 §4.2），
  背光这类需要"按比例设占空"的消费者不必知道具体纳秒数。
- **devm 覆盖完整**：`devm_pwmchip_add` / `devm_pwm_get` /
  `devm_fwnode_pwm_get` 都有（§A2.3）。

**缺点**：

- **文档腐坏程度高**（§C2 六处），且腐坏点集中在新手最先读的地方
  （kerneldoc、rst 的驱动章节）。
- **生命周期只有模块级保护**，unbind 路径下有真实 UAF（§B3）。
- **消费者读不到硬件实际值**（§C1.2），这是能力缺口而非风格问题。
- **`chip->base` / `pwmchipN` 的全局编号被 sysfs ABI 钉死**（§C1.3）。

**给不同角色的一句话**：

| 角色 | 结论 |
|---|---|
| 写 provider 驱动 | 只实现 `.apply` + `.get_state`，**开发期打开 `CONFIG_PWM_DEBUG` 读完那 100 行的检查判据**，用 `devm_pwmchip_add()` |
| 写 consumer | 用 `devm_pwm_get()`，**`pwm_put()` 前自己关输出**，resume 时自己重配，不要相信 `pwm_get_state()` 是硬件值 |
| 写 DT | ⚠️ **`#pwm-cells = <3>` 必须显式写**，`pwm-names` 只在多路时才需要 |
| 排查现场 | 先看 `/sys/kernel/debug/pwm`（唯一能看 `usage_power` 的地方），再开 `pwm_apply`/`pwm_get` trace，**记住 `pwm_get` 事件 ≠ `pwm_get()` 调用** |
| 评估可靠性 | **不要在生产系统上 unbind PWM provider**（§B3） |
