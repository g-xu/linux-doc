# Kernel Module basics.md API Documentation

## Driver Basics

## Driver Entry and Exit points

| 名称 | 描述 |
|---|---|
| module_init | driver initialization entry point |
| module_exit | driver exit entry point |

## Driver device table

| 名称 | 描述 |
|---|---|
| struct usb_device_id | identifies USB devices for probing and hotplugging |
| struct mdio_device_id | identifies PHY devices on an MDIO/MII bus |
| struct amba_id | identifies a device on an AMBA bus |
| struct mips_cdmm_device_id | identifies devices in MIPS CDMM bus |
| struct mei_cl_device_id | MEI client device identifier |
| struct rio_device_id | RIO device identifier |
| struct fsl_mc_device_id | MC object device identifier |
| struct tb_service_id | Thunderbolt service identifiers |
| struct typec_device_id | USB Type-C alternate mode identifiers |
| struct tee_client_device_id | tee based device identifier |
| struct wmi_device_id | WMI device identifier |
| struct mhi_device_id | MHI device identification |
| struct dfl_device_id | dfl device identifier |

## Delaying, scheduling, and timer routines

| 名称 | 描述 |
|---|---|
| struct prev_cputime | snapshot of system and user cputime |
| struct util_est | Estimation utilization of FAIR tasks |
| pid_alive | check that a task structure is not stale |
| is_global_init | check if a task structure is init. Since init is free to have sub-threads we need to check tgid. |
| task_nice | return the nice value of a given task. |
| is_idle_task | is the specified task an idle task? |
| wake_up_process | Wake up a specific process |
| preempt_notifier_register | tell me when current is being preempted & rescheduled |
| preempt_notifier_unregister | no longer interested in preemption notifications |
| preempt_schedule_notrace | preempt_schedule called by tracing |
| yield | yield the current processor to other threads. |
| yield_to | yield the current processor to another thread in your thread group, or accelerate that thread toward the processor it’s on. |
| cpupri_find_fitness | find the best (lowest-pri) CPU in the system |
| cpupri_set | update the CPU priority setting |
| cpupri_init | initialize the cpupri structure |
| cpupri_cleanup | clean up the cpupri structure |
| update_tg_load_avg | update the tg’s load avg |
| update_cfs_rq_load_avg | update the cfs_rq’s load/util averages |
| attach_entity_load_avg | attach this entity to its cfs_rq load avg |
| detach_entity_load_avg | detach this entity from its cfs_rq load avg |
| cpu_util | Estimates the amount of capacity of a CPU used by CFS tasks. |
| update_sg_lb_stats | Update sched_group’s statistics for load balancing. |
| update_sd_pick_busiest | return 1 on busiest group |
| idle_cpu_without | would a given CPU be idle without p ? |
| update_sd_lb_stats | Update sched_domain’s statistics for load balancing. |
| calculate_imbalance | Calculate the amount of imbalance present within the groups of a given sched_domain during load balance. |
| find_busiest_group | Returns the busiest group within the sched_domain if there is an imbalance. |
| DECLARE_COMPLETION | declare and initialize a completion structure |
| DECLARE_COMPLETION_ONSTACK | declare and initialize a completion structure |
| init_completion | Initialize a dynamically allocated completion |
| reinit_completion | reinitialize a completion structure |
| __round_jiffies | function to round jiffies to a full second |
| __round_jiffies_relative | function to round jiffies to a full second |
| round_jiffies | function to round jiffies to a full second |
| round_jiffies_relative | function to round jiffies to a full second |
| __round_jiffies_up | function to round jiffies up to a full second |
| __round_jiffies_up_relative | function to round jiffies up to a full second |
| round_jiffies_up | function to round jiffies up to a full second |
| round_jiffies_up_relative | function to round jiffies up to a full second |
| init_timer_key | initialize a timer |
| mod_timer_pending | modify a pending timer’s timeout |
| mod_timer | modify a timer’s timeout |
| timer_reduce | Modify a timer’s timeout if it would reduce the timeout |
| add_timer | start a timer |
| add_timer_on | start a timer on a particular CPU |
| del_timer | deactivate a timer. |
| try_to_del_timer_sync | Try to deactivate a timer |
| del_timer_sync | deactivate a timer and wait for the handler to finish. |
| schedule_timeout | sleep until timeout |
| msleep | sleep safely even with waitqueue interruptions |
| msleep_interruptible | sleep waiting for signals |
| usleep_range | Sleep for an approximate time |

## Wait queues and Wake events

| 名称 | 描述 |
|---|---|
| waitqueue_active | locklessly test for waiters on the queue |
| wq_has_single_sleeper | check if there is only one sleeper |
| wq_has_sleeper | check if there are any waiting processes |
| wait_event | sleep until a condition gets true |
| wait_event_freezable | sleep (or freeze) until a condition gets true |
| wait_event_timeout | sleep until a condition gets true or a timeout elapses |
| wait_event_cmd | sleep until a condition gets true |
| wait_event_interruptible | sleep until a condition gets true |
| wait_event_interruptible_timeout | sleep until a condition gets true or a timeout elapses |
| wait_event_hrtimeout | sleep until a condition gets true or a timeout elapses |
| wait_event_interruptible_hrtimeout | sleep until a condition gets true or a timeout elapses |
| wait_event_idle | wait for a condition without contributing to system load |
| wait_event_idle_exclusive | wait for a condition with contributing to system load |
| wait_event_idle_timeout | sleep without load until a condition becomes true or a timeout elapses |
| wait_event_idle_exclusive_timeout | sleep without load until a condition becomes true or a timeout elapses |
| wait_event_interruptible_locked | sleep until a condition gets true |
| wait_event_interruptible_locked_irq | sleep until a condition gets true |
| wait_event_interruptible_exclusive_locked | sleep exclusively until a condition gets true |
| wait_event_interruptible_exclusive_locked_irq | sleep until a condition gets true |
| wait_event_killable | sleep until a condition gets true |
| wait_event_killable_timeout | sleep until a condition gets true or a timeout elapses |
| wait_event_lock_irq_cmd | sleep until a condition gets true. The condition is checked under the lock. This is expected to be called with the lock taken. |
| wait_event_lock_irq | sleep until a condition gets true. The condition is checked under the lock. This is expected to be called with the lock taken. |
| wait_event_interruptible_lock_irq_cmd | sleep until a condition gets true. The condition is checked under the lock. This is expected to be called with the lock taken. |
| wait_event_interruptible_lock_irq | sleep until a condition gets true. The condition is checked under the lock. This is expected to be called with the lock taken. |
| wait_event_interruptible_lock_irq_timeout | sleep until a condition gets true or a timeout elapses. The condition is checked under the lock. This is expected to be called with the lock taken. |
| __wake_up | wake up threads blocked on a waitqueue. |
| __wake_up_sync_key | wake up threads blocked on a waitqueue. |
| __wake_up_locked_sync_key | wake up a thread blocked on a locked waitqueue. |
| finish_wait | clean up after waiting in a queue |

## High-resolution timers

| 名称 | 描述 |
|---|---|
| ktime_set | Set a ktime_t variable from a seconds/nanoseconds value |
| ktime_compare | Compares two ktime_t variables for less, greater or equal |
| ktime_after | Compare if a ktime_t value is bigger than another one. |
| ktime_before | Compare if a ktime_t value is smaller than another one. |
| ktime_to_timespec64_cond | convert a ktime_t variable to timespec64 format only if the variable contains data |
| struct hrtimer | the basic hrtimer structure |
| struct hrtimer_sleeper | simple sleeper structure |
| struct hrtimer_clock_base | the timer base for a specific clock |
| struct hrtimer_cpu_base | the per cpu clock bases |
| hrtimer_start | (re)start an hrtimer |
| hrtimer_get_remaining | get remaining time for the timer |
| hrtimer_is_queued | check, whether the timer is on one of the queues |
| hrtimer_forward_now | forward the timer expiry so it expires after now |
| hrtimer_forward | forward the timer expiry |
| hrtimer_start_range_ns | (re)start an hrtimer |
| hrtimer_try_to_cancel | try to deactivate a timer |
| hrtimer_cancel | cancel a timer and wait for the handler to finish. |
| __hrtimer_get_remaining | get remaining time for the timer |
| hrtimer_init | initialize a timer to the given clock |
| hrtimer_sleeper_start_expires | Start a hrtimer sleeper timer |
| hrtimer_init_sleeper | initialize sleeper to the given clock |
| schedule_hrtimeout_range | sleep until timeout |
| schedule_hrtimeout | sleep until timeout |

## Internal Functions

| 名称 | 描述 |
|---|---|
| wait_task_stopped | Wait forTASK_STOPPEDorTASK_TRACED |
| task_set_jobctl_pending | set jobctl pending bits |
| task_clear_jobctl_trapping | clear jobctl trapping bit |
| task_clear_jobctl_pending | clear jobctl pending bits |
| task_participate_group_stop | participate in a group stop |
| ptrace_trap_notify | schedule trap to notify ptracer |
| force_sig_seccomp | signals the task to allow in-process syscall emulation |
| do_notify_parent_cldstop | notify parent of stopped/continued state change |
| do_signal_stop | handle group stop for SIGSTOP and other stop signals |
| do_jobctl_trap | take care of ptrace jobctl traps |
| do_freezer_trap | handle the freezer jobctl trap |
| signal_delivered |  |
| sys_restart_syscall | restart a system call |
| set_current_blocked | change current->blocked mask |
| sys_rt_sigprocmask | change the list of currently blocked signals |
| sys_rt_sigpending | examine a pending signal that has been raised while blocked |
| copy_siginfo_to_external32 | copy a kernel siginfo into a compat user siginfo |
| do_sigtimedwait | wait for queued signals specified inwhich |
| sys_rt_sigtimedwait | synchronously wait for queued signals specified inuthese |
| sys_kill | send a signal to a process |
| sys_pidfd_send_signal | Signal a process through a pidfd |
| sys_tgkill | send signal to one specific thread |
| sys_tkill | send signal to one specific task |
| sys_rt_sigqueueinfo | send signal information to a signal |
| sys_sigpending | examine pending signals |
| sys_sigprocmask | examine and change blocked signals |
| sys_rt_sigaction | alter an action taken by a process |
| sys_rt_sigsuspend | replace the signal mask for a value with theunewsetvalue until a signal is received |
| kthread_create | create a kthread on the current node |
| kthread_run | create and wake a thread. |
| kthread_should_stop | should this kthread return now? |
| kthread_should_park | should this kthread park now? |
| kthread_freezable_should_stop | should this freezable kthread return now? |
| kthread_func | return the function specified on kthread creation |
| kthread_data | return data value specified on kthread creation |
| kthread_create_on_node | create a kthread. |
| kthread_bind | bind a just-created kthread to a cpu. |
| kthread_unpark | unpark a thread created bykthread_create(). |
| kthread_park | park a thread created bykthread_create(). |
| kthread_stop | stop a thread created bykthread_create(). |
| kthread_worker_fn | kthread function to process kthread_worker |
| kthread_create_worker | create a kthread worker |
| kthread_create_worker_on_cpu | create a kthread worker and bind it to a given CPU and the associated NUMA node. |
| kthread_queue_work | queue a kthread_work |
| kthread_delayed_work_timer_fn | callback that queues the associated kthread delayed work when the timer expires. |
| kthread_queue_delayed_work | queue the associated kthread work after a delay. |
| kthread_flush_work | flush a kthread_work |
| kthread_mod_delayed_work | modify delay of or queue a kthread delayed work |
| kthread_cancel_work_sync | cancel a kthread work and wait for it to finish |
| kthread_cancel_delayed_work_sync | cancel a kthread delayed work and wait for it to finish. |
| kthread_flush_worker | flush all current works on a kthread_worker |
| kthread_destroy_worker | destroy a kthread worker |
| kthread_use_mm | make the calling kthread operate on an address space |
| kthread_unuse_mm | reverse the effect ofkthread_use_mm() |
| kthread_associate_blkcg | associate blkcg to current kthread |
| kthread_blkcg | get associated blkcg css of current kthread |

## Reference counting

| 名称 | 描述 |
|---|---|
| struct refcount_t | variant of atomic_t specialized for reference counts |
| refcount_set | set a refcount’s value |
| refcount_read | get a refcount’s value |
| refcount_add_not_zero | add a value to a refcount unless it is 0 |
| refcount_add | add a value to a refcount |
| refcount_inc_not_zero | increment a refcount unless it is 0 |
| refcount_inc | increment a refcount |
| refcount_sub_and_test | subtract from a refcount and test if it is 0 |
| refcount_dec_and_test | decrement a refcount and test if it is 0 |
| refcount_dec | decrement a refcount |
| refcount_dec_if_one | decrement a refcount if it is 1 |
| refcount_dec_not_one | decrement a refcount if it is not 1 |
| refcount_dec_and_mutex_lock | return holding mutex if able to decrement refcount to 0 |
| refcount_dec_and_lock | return holding spinlock if able to decrement refcount to 0 |
| refcount_dec_and_lock_irqsave | return holding spinlock with disabled interrupts if able to decrement refcount to 0 |

## Atomics

| 名称 | 描述 |
|---|---|
| arch_atomic_read | read atomic variable |
| arch_atomic_set | set atomic variable |
| arch_atomic_add | add integer to atomic variable |
| arch_atomic_sub | subtract integer from atomic variable |
| arch_atomic_sub_and_test | subtract value from variable and test result |
| arch_atomic_inc | increment atomic variable |
| arch_atomic_dec | decrement atomic variable |
| arch_atomic_dec_and_test | decrement and test |
| arch_atomic_inc_and_test | increment and test |
| arch_atomic_add_negative | add and test if negative |
| arch_atomic_add_return | add integer and return |
| arch_atomic_sub_return | subtract integer and return |

## Kernel objects manipulation

| 名称 | 描述 |
|---|---|
| kobject_get_path | Allocate memory and fill in the path forkobj. |
| kobject_set_name | Set the name of a kobject. |
| kobject_init | Initialize a kobject structure. |
| kobject_add | The main kobject add function. |
| kobject_init_and_add | Initialize a kobject structure and add it to the kobject hierarchy. |
| kobject_rename | Change the name of an object. |
| kobject_move | Move object to another parent. |
| kobject_del | Unlink kobject from hierarchy. |
| kobject_get | Increment refcount for object. |
| kobject_put | Decrement refcount for object. |
| kobject_create_and_add | Create a struct kobject dynamically and register it with sysfs. |
| kset_register | Initialize and add a kset. |
| kset_unregister | Remove a kset. |
| kset_find_obj | Search for object in kset. |
| kset_create_and_add | Create a struct kset dynamically and add it to sysfs. |

## Kernel utility functions

| 名称 | 描述 |
|---|---|
| REPEAT_BYTE | repeat the valuexmultiple times as an unsigned long value |
| ARRAY_SIZE | get the number of elements in arrayarr |
| upper_32_bits | return bits 32-63 of a number |
| lower_32_bits | return bits 0-31 of a number |
| upper_16_bits | return bits 16-31 of a number |
| lower_16_bits | return bits 0-15 of a number |
| might_sleep | annotation for functions that can sleep |
| cant_sleep | annotation for functions that cannot sleep |
| cant_migrate | annotation for functions that cannot migrate |
| non_block_start | annotate the start of section where sleeping is prohibited |
| non_block_end | annotate the end of section where sleeping is prohibited |
| trace_printk | printf formatting in the ftrace buffer |
| trace_puts | write a string into the ftrace buffer |
| container_of | cast a member of a structure out to the containing structure |
| container_of_safe | cast a member of a structure out to the containing structure |
| console_lock | lock the console system for exclusive use. |
| console_trylock | try to lock the console system for exclusive use. |
| console_unlock | unlock the console system |
| console_conditional_schedule | yield the CPU if required |
| printk_timed_ratelimit | caller-controlled printk ratelimiting |
| kmsg_dump_register | register a kernel log dumper. |
| kmsg_dump_unregister | unregister a kmsg dumper. |
| kmsg_dump_get_line | retrieve one kmsg log line |
| kmsg_dump_get_buffer | copy kmsg log lines |
| kmsg_dump_rewind | reset the iterator |
| __printk_wait_on_cpu_lock | Busy wait until the printk cpu-reentrant spinning lock is not owned by any CPU. |
| __printk_cpu_trylock | Try to acquire the printk cpu-reentrant spinning lock. |
| __printk_cpu_unlock | Release the printk cpu-reentrant spinning lock. |
| panic | halt the system |
| add_taint | add a taint flag if not already set. |
| array_size | Calculate size of 2-dimensional array. |
| array3_size | Calculate size of 3-dimensional array. |
| struct_size | Calculate size of structure with trailing array. |
| flex_array_size | Calculate size of a flexible array member within an enclosing structure. |

## Device Resource Management

| 名称 | 描述 |
|---|---|
| __devres_alloc_node | Allocate device resource data |
| devres_for_each_res | Resource iterator |
| devres_free | Free device resource data |
| devres_add | Register device resource |
| devres_find | Find device resource |
| devres_get | Find devres, if non-existent, add one atomically |
| devres_remove | Find a device resource and remove it |
| devres_destroy | Find a device resource and destroy it |
| devres_release | Find a device resource and destroy it, calling release |
| devres_open_group | Open a new devres group |
| devres_close_group | Close a devres group |
| devres_remove_group | Remove a devres group |
| devres_release_group | Release resources in a devres group |
| devm_add_action | add a custom action to list of managed resources |
| devm_remove_action | removes previously added custom action |
| devm_release_action | release previously added custom action |
| devm_kmalloc | Resource-managed kmalloc |
| devm_krealloc | Resource-managedkrealloc() |
| devm_kstrdup | Allocate resource managed space and copy an existing string into that. |
| devm_kstrdup_const | resource managed conditional string duplication |
| devm_kvasprintf | Allocate resource managed space and format a string into that. |
| devm_kasprintf | Allocate resource managed space and format a string into that. |
| devm_kfree | Resource-managed kfree |
| devm_kmemdup | Resource-managed kmemdup |
| devm_get_free_pages | Resource-managed __get_free_pages |
| devm_free_pages | Resource-managed free_pages |
| __devm_alloc_percpu | Resource-managed alloc_percpu |
| devm_free_percpu | Resource-managed free_percpu |

## 总数统计

总共提取了 280 个函数+结构。
