# Kernel Module devfreq.md API Documentation

## Device Frequency Scaling

## Introduction

## API

| 名称 | 描述 |
|---|---|
| struct devfreq_dev_status | Data given from devfreq user device to governors. Represents the performance statistics. |
| struct devfreq_dev_profile | Devfreq’s user device profile |
| struct devfreq_stats | Statistics of devfreq device behavior |
| struct devfreq | Device devfreq structure |
| struct devfreq_simple_ondemand_data | void*datafed tostructdevfreqand devfreq_add_device |
| struct devfreq_passive_data | void*datafed tostructdevfreqand devfreq_add_device |
| struct devfreq_event_dev | the devfreq-event device |
| struct devfreq_event_data | the devfreq-event data |
| struct devfreq_event_ops | the operations of devfreq-event device |
| struct devfreq_event_desc | the descriptor of devfreq-event device |
| devfreq_update_status | Update statistics of devfreq behavior |
| devfreq_update_target | Reevaluate the device and configure frequency on the final stage. |
| update_devfreq | Reevaluate the device and configure frequency. |
| devfreq_monitor_start | Start load monitoring of devfreq instance |
| devfreq_monitor_stop | Stop load monitoring of a devfreq instance |
| devfreq_monitor_suspend | Suspend load monitoring of a devfreq instance |
| devfreq_monitor_resume | Resume load monitoring of a devfreq instance |
| devfreq_update_interval | Update device devfreq monitoring interval |
| devfreq_add_device | Add devfreq feature to the device |
| devfreq_remove_device | Remove devfreq feature from a device. |
| devm_devfreq_add_device | Resource-manageddevfreq_add_device() |
| devm_devfreq_remove_device | Resource-manageddevfreq_remove_device() |
| devfreq_suspend_device | Suspend devfreq of a device. |
| devfreq_resume_device | Resume devfreq of a device. |
| devfreq_add_governor | Add devfreq governor |
| devfreq_remove_governor | Remove devfreq feature from a device. |
| devfreq_recommended_opp | Helper function to get proper OPP for the freq value given to target callback. |
| devfreq_register_opp_notifier | Helper function to get devfreq notified for any changes in the OPP availability changes |
| devfreq_unregister_opp_notifier | Helper function to stop getting devfreq notified for any changes in the OPP availability changes anymore. |
| devm_devfreq_register_opp_notifier | Resource-manageddevfreq_register_opp_notifier() |
| devm_devfreq_unregister_opp_notifier | Resource-manageddevfreq_unregister_opp_notifier() |
| devfreq_register_notifier | Register a driver with devfreq |
| devm_devfreq_register_notifier |  |
| devm_devfreq_unregister_notifier |  |
| devfreq_event_enable_edev | Enable the devfreq-event dev and increase the enable_count of devfreq-event dev. |
| devfreq_event_disable_edev | Disable the devfreq-event dev and decrease the enable_count of the devfreq-event dev. |
| devfreq_event_is_enabled | Check whether devfreq-event dev is enabled or not. |
| devfreq_event_set_event | Set event to devfreq-event dev to start. |
| devfreq_event_get_event | Get {load|total}_count from devfreq-event dev. |
| devfreq_event_reset_event | Reset all opeations of devfreq-event dev. |
| devfreq_event_get_edev_by_phandle | Get the devfreq-event dev from devicetree. |
| devfreq_event_get_edev_count | Get the count of devfreq-event dev |
| devfreq_event_add_edev | Add new devfreq-event device. |
| devfreq_event_remove_edev | Remove the devfreq-event device registered. |
| devm_devfreq_event_add_edev | Resource-manageddevfreq_event_add_edev() |
| devm_devfreq_event_remove_edev | Resource-manageddevfreq_event_remove_edev() |

## 总数统计

总共提取了 46 个函数+结构。

# Kernel Module device-io.md API Documentation

## Bus-Independent Device Accesses

## Public Functions Provided

| 名称 | 描述 |
|---|---|
| virt_to_phys | map virtual addresses to physical |
| phys_to_virt | map physical address to virtual |
| ioremap | map bus memory into CPU space |
| iosubmit_cmds512 | copy data to single MMIO location, in 512-bit units |
| pci_iomap_range | create a virtual mapping cookie for a PCI BAR |
| pci_iomap_wc_range | create a virtual WC mapping cookie for a PCI BAR |
| pci_iomap | create a virtual mapping cookie for a PCI BAR |
| pci_iomap_wc | create a virtual WC mapping cookie for a PCI BAR |

## 总数统计

总共提取了 8 个函数+结构。

# Kernel Module dma-buf.md API Documentation

## Buffer Sharing and Synchronization

## Shared DMA Buffers

| 名称 | 描述 |
|---|---|
| struct dma_buf_sync | Synchronize with CPU access. |
| dma_buf_export | Creates a new dma_buf, and associates an anon file with this buffer, so it can be exported. Also connect the allocator specific data and ops to the buffer. Additionally, provide a name string for exporter; useful in debugging. |
| dma_buf_fd | returns a file descriptor for the givenstructdma_buf |
| dma_buf_get | returns thestructdma_bufrelated to an fd |
| dma_buf_put | decreases refcount of the buffer |
| dma_buf_dynamic_attach | Add the device to dma_buf’s attachments list |
| dma_buf_attach | Wrapper for dma_buf_dynamic_attach |
| dma_buf_detach | Remove the given attachment from dmabuf’s attachments list |
| dma_buf_pin | Lock down the DMA-buf |
| dma_buf_unpin | Unpin a DMA-buf |
| dma_buf_map_attachment | Returns the scatterlist table of the attachment; mapped into _device_ address space. Is a wrapper for map_dma_buf() of the dma_buf_ops. |
| dma_buf_unmap_attachment | unmaps and decreases usecount of the buffer;might deallocate the scatterlist associated. Is a wrapper for unmap_dma_buf() of dma_buf_ops. |
| dma_buf_move_notify | notify attachments that DMA-buf is moving |
| dma_buf_begin_cpu_access | Must be called before accessing a dma_buf from the cpu in the kernel context. Calls begin_cpu_access to allow exporter-specific preparations. Coherency is only guaranteed in the specified range for the specified access direction. |
| dma_buf_end_cpu_access | Must be called after accessing a dma_buf from the cpu in the kernel context. Calls end_cpu_access to allow exporter-specific actions. Coherency is only guaranteed in the specified range for the specified access direction. |
| dma_buf_mmap | Setup up a userspace mmap with the given vma |
| dma_buf_vmap | Create virtual mapping for the buffer object into kernel address space. Same restrictions as for vmap and friends apply. |
| dma_buf_vunmap | Unmap a vmap obtained by dma_buf_vmap. |
| struct dma_buf_ops | operations possible onstructdma_buf |
| struct dma_buf | shared buffer object |
| struct dma_buf_attach_ops | importer operations for an attachment |
| struct dma_buf_attachment | holds device-buffer attachment data |
| struct dma_buf_export_info | holds information needed to export a dma_buf |
| DEFINE_DMA_BUF_EXPORT_INFO | helper macro for exporters |
| get_dma_buf | convenience wrapper for get_file. |
| dma_buf_is_dynamic | check if a DMA-buf uses dynamic mappings. |
| dma_buf_attachment_is_dynamic | check if a DMA-buf attachment uses dynamic mappings |
| struct dma_buf_map | Pointer to vmap’ed dma-buf memory. |
| DMA_BUF_MAP_INIT_VADDR | Initializesstructdma_buf_mapto an address in system memory |
| dma_buf_map_set_vaddr | Sets a dma-buf mapping structure to an address in system memory |
| dma_buf_map_set_vaddr_iomem | Sets a dma-buf mapping structure to an address in I/O memory |
| dma_buf_map_is_equal | Compares two dma-buf mapping structures for equality |
| dma_buf_map_is_null | Tests for a dma-buf mapping to be NULL |
| dma_buf_map_is_set | Tests is the dma-buf mapping has been set |
| dma_buf_map_clear | Clears a dma-buf mapping structure |
| dma_buf_map_memcpy_to | Memcpy into dma-buf mapping |
| dma_buf_map_incr | Increments the address stored in a dma-buf mapping |

## Reservation Objects

| 名称 | 描述 |
|---|---|
| dma_resv_init | initialize a reservation object |
| dma_resv_fini | destroys a reservation object |
| dma_resv_reserve_shared | Reserve space to add shared fences to a dma_resv. |
| dma_resv_reset_shared_max | reset shared fences for debugging |
| dma_resv_add_shared_fence | Add a fence to a shared slot |
| dma_resv_add_excl_fence | Add an exclusive fence. |
| dma_resv_copy_fences | Copy all fences from src to dst. |
| dma_resv_get_fences | Get an object’s shared and exclusive fences without update side lock held |
| dma_resv_wait_timeout | Wait on reservation’s objects shared and/or exclusive fences. |
| dma_resv_test_signaled | Test if a reservation object’s fences have been signaled. |
| struct dma_resv_list | a list of shared fences |
| struct dma_resv | a reservation object manages fences for a buffer |
| dma_resv_lock | lock the reservation object |
| dma_resv_lock_interruptible | lock the reservation object |
| dma_resv_lock_slow | slowpath lock the reservation object |
| dma_resv_lock_slow_interruptible | slowpath lock the reservation object, interruptible |
| dma_resv_trylock | trylock the reservation object |
| dma_resv_is_locked | is the reservation object locked |
| dma_resv_locking_ctx | returns the context used to lock the object |
| dma_resv_unlock | unlock the reservation object |
| dma_resv_excl_fence | return the object’s exclusive fence |
| dma_resv_get_excl_unlocked | get the reservation object’s exclusive fence, without lock held. |
| dma_resv_shared_list | get the reservation object’s shared fence list |

## DMA Fences

| 名称 | 描述 |
|---|---|
| dma_fence_get_stub | return a signaled fence |
| dma_fence_allocate_private_stub | return a private, signaled fence |
| dma_fence_context_alloc | allocate an array of fence contexts |
| dma_fence_begin_signalling | begin a critical DMA fence signalling section |
| dma_fence_end_signalling | end a critical DMA fence signalling section |
| dma_fence_signal_timestamp_locked | signal completion of a fence |
| dma_fence_signal_timestamp | signal completion of a fence |
| dma_fence_signal_locked | signal completion of a fence |
| dma_fence_signal | signal completion of a fence |
| dma_fence_wait_timeout | sleep until the fence gets signaled or until timeout elapses |
| dma_fence_release | default relese function for fences |
| dma_fence_free | default release function fordma_fence. |
| dma_fence_enable_sw_signaling | enable signaling on fence |
| dma_fence_add_callback | add a callback to be called when the fence is signaled |
| dma_fence_get_status | returns the status upon completion |
| dma_fence_remove_callback | remove a callback from the signaling list |
| dma_fence_default_wait | default sleep until the fence gets signaled or until timeout elapses |
| dma_fence_wait_any_timeout | sleep until any fence gets signaled or until timeout elapses |
| dma_fence_init | Initialize a custom fence. |
| struct dma_fence | software synchronization primitive |
| struct dma_fence_cb | callback fordma_fence_add_callback() |
| struct dma_fence_ops | operations implemented for fence |
| dma_fence_put | decreases refcount of the fence |
| dma_fence_get | increases refcount of the fence |
| dma_fence_get_rcu | get a fence from a dma_resv_list with rcu read lock |
| dma_fence_get_rcu_safe | acquire a reference to an RCU tracked fence |
| dma_fence_is_signaled_locked | Return an indication if the fence is signaled yet. |
| dma_fence_is_signaled | Return an indication if the fence is signaled yet. |
| __dma_fence_is_later | return if f1 is chronologically later than f2 |
| dma_fence_is_later | return if f1 is chronologically later than f2 |
| dma_fence_later | return the chronologically later fence |
| dma_fence_get_status_locked | returns the status upon completion |
| dma_fence_set_error | flag an error condition on the fence |
| dma_fence_wait | sleep until the fence gets signaled |
| to_seqno_fence | cast a fence to a seqno_fence |
| seqno_fence_init | initialize a seqno fence |
| dma_fence_array_create | Create a custom fence array |
| dma_fence_match_context | Check if all fences are from the given context |
| struct dma_fence_array_cb | callback helper for fence array |
| struct dma_fence_array | fence to represent an array of fences |
| dma_fence_is_array | check if a fence is from the array subsclass |
| to_dma_fence_array | cast a fence to a dma_fence_array |
| dma_fence_chain_walk | chain walking function |
| dma_fence_chain_find_seqno | find fence chain node by seqno |
| dma_fence_chain_init | initialize a fence chain |
| struct dma_fence_chain | fence to represent an node of a fence chain |
| to_dma_fence_chain | cast a fence to a dma_fence_chain |
| dma_fence_chain_alloc |  |
| dma_fence_chain_free |  |
| dma_fence_chain_for_each | iterate over all fences in chain |
| sync_file_create | creates a sync file |
| sync_file_get_fence | get the fence related to the sync_file fd |
| struct sync_file | sync file to export to the userspace |

## 总数统计

总共提取了 113 个函数+结构。

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

# Kernel Module frame-buffer.md API Documentation

## Frame Buffer Library

## Frame Buffer Memory

| 名称 | 描述 |
|---|---|
| remove_conflicting_framebuffers | remove firmware-configured framebuffers |
| remove_conflicting_pci_framebuffers | remove firmware-configured framebuffers for PCI devices |
| register_framebuffer | registers a frame buffer device |
| unregister_framebuffer | releases a frame buffer device |
| fb_set_suspend | low level driver signals suspend |

## Frame Buffer Colormap

| 名称 | 描述 |
|---|---|
| fb_dealloc_cmap | deallocate a colormap |
| fb_copy_cmap | copy a colormap |
| fb_set_cmap | set the colormap |
| fb_default_cmap | get default colormap |
| fb_invert_cmaps | invert all defaults colormaps |

## Frame Buffer Video Mode Database

| 名称 | 描述 |
|---|---|
| fb_try_mode | test a video mode |
| fb_delete_videomode | removed videomode entry from modelist |
| fb_find_mode | finds a valid video mode |
| fb_var_to_videomode | convert fb_var_screeninfo to fb_videomode |
| fb_videomode_to_var | convert fb_videomode to fb_var_screeninfo |
| fb_mode_is_equal | compare 2 videomodes |
| fb_find_best_mode | find best matching videomode |
| fb_find_nearest_mode | find closest videomode |
| fb_match_mode | find a videomode which exactly matches the timings in var |
| fb_add_videomode | adds videomode entry to modelist |
| fb_destroy_modelist | destroy modelist |
| fb_videomode_to_modelist | convert mode array to mode list |

## Frame Buffer Macintosh Video Mode Database

| 名称 | 描述 |
|---|---|
| mac_vmode_to_var | converts vmode/cmode pair to var structure |
| mac_map_monitor_sense | Convert monitor sense to vmode |
| mac_find_mode | find a video mode |

## Frame Buffer Fonts

## 总数统计

总共提取了 25 个函数+结构。

# Kernel Module genericirq.md API Documentation

## Linux generic IRQ handling


## Generic interrupt chip

| 名称 | 描述 |
|---|---|
| irq_gc_mask_set_bit | Mask chip via setting bit in mask register |
| irq_gc_mask_clr_bit | Mask chip via clearing bit in mask register |
| irq_gc_ack_set_bit | Ack pending interrupt via setting bit |
| irq_gc_set_wake | Set/clr wake bit for an interrupt |
| irq_alloc_generic_chip | Allocate a generic chip and initialize it |
| __irq_alloc_domain_generic_chips | Allocate generic chips for an irq domain |
| irq_get_domain_generic_chip | Get a pointer to the generic chip of a hw_irq |
| irq_setup_generic_chip | Setup a range of interrupts with a generic chip |
| irq_setup_alt_chip | Switch to alternative chip |
| irq_remove_generic_chip | Remove a chip |

## Structures

| 名称 | 描述 |
|---|---|
| struct irq_common_data | per irq data shared by all irqchips |
| struct irq_data | per irq chip data passed down to chip functions |
| struct irq_chip | hardware interrupt chip descriptor |
| struct irq_chip_regs | register offsets for struct irq_gci |
| struct irq_chip_type | Generic interrupt chip instance for a flow type |
| struct irq_chip_generic | Generic irq chip data structure |
| struct irq_gc_flags | Initialization flags for generic irq chips |
| struct irqaction | per interrupt action descriptor |
| request_irq | Add a handler for an interrupt line |
| struct irq_affinity_notify | context for notification of IRQ affinity changes |
| struct irq_affinity | Description for automatic irq affinity assignements |
| struct irq_affinity_desc | Interrupt affinity descriptor |

## Public Functions Provided

| 名称 | 描述 |
|---|---|
| synchronize_hardirq | wait for pending hard IRQ handlers (on other CPUs) |
| synchronize_irq | wait for pending IRQ handlers (on other CPUs) |
| irq_can_set_affinity | Check if the affinity of a given irq can be set |
| irq_can_set_affinity_usr | Check if affinity of a irq can be set from user space |
| irq_set_thread_affinity | Notify irq threads to adjust affinity |
| irq_update_affinity_desc | Update affinity management for an interrupt |
| irq_set_affinity | Set the irq affinity of a given irq |
| irq_force_affinity | Force the irq affinity of a given irq |
| irq_set_affinity_notifier | control notification of IRQ affinity changes |
| irq_set_vcpu_affinity | Set vcpu affinity for the interrupt |
| disable_irq_nosync | disable an irq without waiting |
| disable_irq | disable an irq and wait for completion |
| disable_hardirq | disables an irq and waits for hardirq completion |
| disable_nmi_nosync | disable an nmi without waiting |
| enable_irq | enable handling of an irq |
| enable_nmi | enable handling of an nmi |
| irq_set_irq_wake | control irq power management wakeup |
| irq_wake_thread | wake the irq thread for the action identified by dev_id |
| free_irq | free an interrupt allocated with request_irq |
| request_threaded_irq | allocate an interrupt line |
| request_any_context_irq | allocate an interrupt line |
| request_nmi | allocate an interrupt line for NMI delivery |
| irq_percpu_is_enabled | Check whether the per cpu irq is enabled |
| remove_percpu_irq | free a per-cpu interrupt |
| free_percpu_irq | free an interrupt allocated with request_percpu_irq |
| setup_percpu_irq | setup a per-cpu interrupt |
| __request_percpu_irq | allocate a percpu interrupt line |
| request_percpu_nmi | allocate a percpu interrupt line for NMI delivery |
| prepare_percpu_nmi | performs CPU local setup for NMI delivery |
| teardown_percpu_nmi | undoes NMI setup of IRQ line |
| irq_get_irqchip_state | returns the irqchip state of a interrupt. |
| irq_set_irqchip_state | set the state of a forwarded interrupt. |
| irq_has_action | Check whether an interrupt is requested |
| irq_check_status_bit | Check whether bits in the irq descriptor status are set |
| irq_set_chip | set the irq chip for an irq |
| irq_set_irq_type | set the irq trigger type for an irq |
| irq_set_handler_data | set irq handler data for an irq |
| irq_set_chip_data | set irq chip data for an irq |
| handle_simple_irq | Simple and software-decoded IRQs. |
| handle_untracked_irq | Simple and software-decoded IRQs. |
| handle_level_irq | Level type irq handler |
| handle_fasteoi_irq | irq handler for transparent controllers |
| handle_fasteoi_nmi | irq handler for NMI interrupt lines |
| handle_edge_irq | edge type IRQ handler |
| handle_fasteoi_ack_irq | irq handler for edge hierarchy stacked on transparent controllers |
| handle_fasteoi_mask_irq | irq handler for level hierarchy stacked on transparent controllers |
| irq_chip_set_parent_state | set the state of a parent interrupt. |
| irq_chip_get_parent_state | get the state of a parent interrupt. |
| irq_chip_enable_parent | Enable the parent interrupt (defaults to unmask if NULL) |
| irq_chip_disable_parent | Disable the parent interrupt (defaults to mask if NULL) |
| irq_chip_ack_parent | Acknowledge the parent interrupt |
| irq_chip_mask_parent | Mask the parent interrupt |
| irq_chip_mask_ack_parent | Mask and acknowledge the parent interrupt |
| irq_chip_unmask_parent | Unmask the parent interrupt |
| irq_chip_eoi_parent | Invoke EOI on the parent interrupt |
| irq_chip_set_affinity_parent | Set affinity on the parent interrupt |
| irq_chip_set_type_parent | Set IRQ type on the parent interrupt |
| irq_chip_retrigger_hierarchy | Retrigger an interrupt in hardware |
| irq_chip_set_vcpu_affinity_parent | Set vcpu affinity on the parent interrupt |
| irq_chip_set_wake_parent | Set/reset wake-up on the parent interrupt |
| irq_chip_request_resources_parent | Request resources on the parent interrupt |
| irq_chip_release_resources_parent | Release resources on the parent interrupt |

## Internal Functions Provided

| 名称 | 描述 |
|---|---|
| generic_handle_irq | Invoke the handler for a particular irq |
| generic_handle_domain_irq | Invoke the handler for a HW irq belonging to a domain, usually for a non-root interrupt controller |
| handle_domain_irq | Invoke the handler for a HW irq belonging to a domain, usually for a root interrupt controller |
| handle_domain_nmi | Invoke the handler for a HW irq belonging to a domain |
| irq_free_descs | free irq descriptors |
| __irq_alloc_descs | allocate and initialize a range of irq descriptors |
| irq_get_next_irq | get next allocated irq number |
| kstat_irqs_cpu | Get the statistics for an interrupt on a cpu |
| kstat_irqs_usr | Get the statistics for an interrupt from thread context |
| handle_bad_irq | handle spurious and unhandled irqs |
| irq_set_msi_desc_off | set MSI descriptor data for an irq at offset |
| irq_set_msi_desc | set MSI descriptor data for an irq |
| irq_disable | Mark interrupt disabled |
| handle_edge_eoi_irq | edge eoi type IRQ handler |
| handle_percpu_irq | Per CPU local irq handler |
| handle_percpu_devid_irq | Per CPU local irq handler with per cpu dev ids |
| handle_percpu_devid_fasteoi_nmi | Per CPU local NMI handler with per cpu dev ids |
| irq_cpu_online | Invoke all irq_cpu_online functions. |
| irq_cpu_offline | Invoke all irq_cpu_offline functions. |
| irq_chip_compose_msi_msg | Compose msi message for a irq chip |
| irq_chip_pm_get | Enable power for an IRQ chip |
| irq_chip_pm_put | Disable power for an IRQ chip |

## Credits

## 总数统计

总共提取了 106 个函数+结构。

# Kernel Module index.md API Documentation

## General Purpose Input/Output (GPIO)

## Core

| 名称 | 描述 |
|---|---|
| struct gpio_irq_chip | GPIO interrupt controller |
| struct gpio_chip | abstract a GPIO controller |
| for_each_requested_gpio_in_range | iterates over requested GPIOs in a given range |
| gpiochip_add_data | register a gpio_chip |
| struct gpio_pin_range | pin range controlled by a gpio chip |
| gpio_to_desc | Convert a GPIO number to its descriptor |
| gpiochip_get_desc | get the GPIO descriptor corresponding to the given hardware number for this chip |
| desc_to_gpio | convert a GPIO descriptor to the integer namespace |
| gpiod_to_chip | Return the GPIO chip to which a GPIO descriptor belongs |
| gpiod_get_direction | return the current direction of a GPIO |
| gpiochip_get_data | get per-subdriver data for the chip |
| gpiochip_remove | unregister a gpio_chip |
| gpiochip_find | iterator for locating a specific gpio_chip |
| gpiochip_irq_map | maps an IRQ into a GPIO irqchip |
| gpiochip_irq_domain_activate | Lock a GPIO to be used as an IRQ |
| gpiochip_irq_domain_deactivate | Unlock a GPIO used as an IRQ |
| gpiochip_irqchip_add_domain | adds an irqdomain to a gpiochip |
| gpiochip_generic_request | request the gpio function for a pin |
| gpiochip_generic_free | free the gpio function from a pin |
| gpiochip_generic_config | apply configuration for a pin |
| gpiochip_add_pingroup_range | add a range for GPIO &lt;-&gt; pin mapping |
| gpiochip_add_pin_range | add a range for GPIO &lt;-&gt; pin mapping |
| gpiochip_remove_pin_ranges | remove all the GPIO &lt;-&gt; pin mappings |
| gpiochip_is_requested | return string iff signal was requested |
| gpiochip_request_own_desc | Allow GPIO chip to request its own descriptor |
| gpiochip_free_own_desc | Free GPIO requested by the chip driver |
| gpiod_direction_input | set the GPIO direction to input |
| gpiod_direction_output_raw | set the GPIO direction to output |
| gpiod_direction_output | set the GPIO direction to output |
| gpiod_set_config | setsconfigfor a GPIO |
| gpiod_set_debounce | setsdebouncetime for a GPIO |
| gpiod_set_transitory | Lose or retain GPIO state on suspend or reset |
| gpiod_is_active_low | test whether a GPIO is active-low or not |
| gpiod_toggle_active_low | toggle whether a GPIO is active-low or not |
| gpiod_get_raw_value | return a gpio’s raw value |
| gpiod_get_value | return a gpio’s value |
| gpiod_get_raw_array_value | read raw values from an array of GPIOs |
| gpiod_get_array_value | read values from an array of GPIOs |
| gpiod_set_raw_value | assign a gpio’s raw value |
| gpiod_set_value | assign a gpio’s value |
| gpiod_set_raw_array_value | assign values to an array of GPIOs |
| gpiod_set_array_value | assign values to an array of GPIOs |
| gpiod_cansleep | report whether gpio value access may sleep |
| gpiod_set_consumer_name | set the consumer name for the descriptor |
| gpiod_to_irq | return the IRQ corresponding to a GPIO |
| gpiochip_lock_as_irq | lock a GPIO to be used as IRQ |
| gpiochip_unlock_as_irq | unlock a GPIO used as IRQ |
| gpiod_get_raw_value_cansleep | return a gpio’s raw value |
| gpiod_get_value_cansleep | return a gpio’s value |
| gpiod_get_raw_array_value_cansleep | read raw values from an array of GPIOs |
| gpiod_get_array_value_cansleep | read values from an array of GPIOs |
| gpiod_set_raw_value_cansleep | assign a gpio’s raw value |
| gpiod_set_value_cansleep | assign a gpio’s value |
| gpiod_set_raw_array_value_cansleep | assign values to an array of GPIOs |
| gpiod_set_array_value_cansleep | assign values to an array of GPIOs |
| gpiod_add_lookup_table | register GPIO device consumers |
| gpiod_remove_lookup_table | unregister GPIO device consumers |
| gpiod_add_hogs | register a set of GPIO hogs from machine code |
| fwnode_gpiod_get_index | obtain a GPIO from firmware node |
| gpiod_count | return the number of GPIOs associated with a device / function or -ENOENT if no GPIO has been assigned to the requested function |
| gpiod_get | obtain a GPIO for a given GPIO function |
| gpiod_get_optional | obtain an optional GPIO for a given GPIO function |
| gpiod_get_index | obtain a GPIO from a multi-index GPIO function |
| fwnode_get_named_gpiod | obtain a GPIO from firmware node |
| gpiod_get_index_optional | obtain an optional GPIO from a multi-index GPIO function |
| gpiod_get_array | obtain multiple GPIOs from a multi-index GPIO function |
| gpiod_get_array_optional | obtain multiple GPIOs from a multi-index GPIO function |
| gpiod_put | dispose of a GPIO descriptor |
| gpiod_put_array | dispose of multiple GPIO descriptors |

## ACPI support

| 名称 | 描述 |
|---|---|
| acpi_get_and_request_gpiod | Translate ACPI GPIO pin to GPIO descriptor and hold a refcount to the GPIO device. |
| acpi_gpio_get_io_resource | Fetch details of an ACPI resource if it is a GPIO I/O resource or return False if not. |
| acpi_gpiochip_request_interrupts | Register isr for gpio chip ACPI events |
| acpi_gpiochip_free_interrupts | Free GPIO ACPI event interrupts. |
| acpi_dev_gpio_irq_get_by | Find GpioInt and translate it to Linux IRQ number |

## Device tree support

| 名称 | 描述 |
|---|---|
| gpiod_get_from_of_node | obtain a GPIO from an OF node |
| of_mm_gpiochip_add_data | Add memory mapped GPIO chip (bank) |
| of_mm_gpiochip_remove | Remove memory mapped GPIO chip (bank) |

## Device-managed API

| 名称 | 描述 |
|---|---|
| devm_gpiod_get | Resource-managedgpiod_get() |
| devm_gpiod_get_optional | Resource-managedgpiod_get_optional() |
| devm_gpiod_get_index | Resource-managedgpiod_get_index() |
| devm_gpiod_get_from_of_node | obtain a GPIO from an OF node |
| devm_fwnode_gpiod_get_index | get a GPIO descriptor from a given node |
| devm_gpiod_get_index_optional | Resource-managedgpiod_get_index_optional() |
| devm_gpiod_get_array | Resource-managedgpiod_get_array() |
| devm_gpiod_get_array_optional | Resource-managedgpiod_get_array_optional() |
| devm_gpiod_put | Resource-managedgpiod_put() |
| devm_gpiod_unhinge | Remove resource management from a gpio descriptor |
| devm_gpiod_put_array | Resource-managedgpiod_put_array() |
| devm_gpio_request | request a GPIO for a managed device |
| devm_gpio_request_one | request a single GPIO with initial setup |
| devm_gpio_free | free a GPIO |
| devm_gpiochip_add_data_with_key | Resource managed gpiochip_add_data_with_key() |

## sysfs helpers

| 名称 | 描述 |
|---|---|
| gpiod_export | export a GPIO through sysfs |
| gpiod_export_link | create a sysfs link to an exported GPIO node |
| gpiod_unexport | reverse effect ofgpiod_export() |

## 总数统计

总共提取了 95 个函数+结构。

# Kernel Module i2c.md API Documentation

## I2C and SMBus Subsystem

| 名称 | 描述 |
|---|---|
| i2c_master_recv | issue a single I2C message in master receive mode |
| i2c_master_recv_dmasafe | issue a single I2C message in master receive mode using a DMA safe buffer |
| i2c_master_send | issue a single I2C message in master transmit mode |
| i2c_master_send_dmasafe | issue a single I2C message in master transmit mode using a DMA safe buffer |
| struct i2c_device_identity | i2c client device identification |
| struct i2c_driver | represent an I2C device driver |
| struct i2c_client | represent an I2C slave device |
| struct i2c_board_info | template for device creation |
| I2C_BOARD_INFO | macro used to list an i2c device and its address |
| struct i2c_algorithm | represent I2C transfer method |
| struct i2c_lock_operations | represent I2C locking operations |
| struct i2c_timings | I2C timing information |
| struct i2c_bus_recovery_info | I2C bus recovery information |
| struct i2c_adapter_quirks | describe flaws of an i2c adapter |
| i2c_lock_bus | Get exclusive access to an I2C bus segment |
| i2c_trylock_bus | Try to get exclusive access to an I2C bus segment |
| i2c_unlock_bus | Release exclusive access to an I2C bus segment |
| i2c_mark_adapter_suspended | Report suspended state of the adapter to the core |
| i2c_mark_adapter_resumed | Report resumed state of the adapter to the core |
| i2c_check_quirks | Function for checking the quirk flags in an i2c adapter |
| module_i2c_driver | Helper macro for registering a modular I2C driver |
| builtin_i2c_driver | Helper macro for registering a builtin I2C driver |
| i2c_register_board_info | statically declare I2C devices |
| i2c_verify_client | return parameter as i2c_client, or NULL |
| i2c_new_client_device | instantiate an i2c device |
| i2c_unregister_device | reverse effect of i2c_new_*_device() |
| i2c_new_dummy_device | return a new i2c device bound to a dummy driver |
| devm_i2c_new_dummy_device | return a new i2c device bound to a dummy driver |
| i2c_new_ancillary_device | Helper to get the instantiated secondary address and create the associated device |
| i2c_verify_adapter | return parameter as i2c_adapter or NULL |
| i2c_handle_smbus_host_notify | Forward a Host Notify event to the correct I2C client. |
| i2c_add_adapter | declare i2c adapter, use dynamic bus number |
| i2c_add_numbered_adapter | declare i2c adapter, use static bus number |
| i2c_del_adapter | unregister I2C adapter |
| devm_i2c_add_adapter | device-managed variant ofi2c_add_adapter() |
| i2c_parse_fw_timings | get I2C related timing parameters from firmware |
| i2c_del_driver | unregister I2C driver |
| __i2c_transfer | unlocked flavor of i2c_transfer |
| i2c_transfer | execute a single or combined I2C message |
| i2c_transfer_buffer_flags | issue a single I2C message transferring data to/from a buffer |
| i2c_get_device_id | get manufacturer, part id and die revision of a device |
| i2c_get_dma_safe_msg_buf | get a DMA safe buffer for the given i2c_msg |
| i2c_put_dma_safe_msg_buf | release DMA safe buffer and sync with i2c_msg |
| i2c_smbus_pec | Incremental CRC8 over the given input data array |
| i2c_smbus_read_byte | SMBus “receive byte” protocol |
| i2c_smbus_write_byte | SMBus “send byte” protocol |
| i2c_smbus_read_byte_data | SMBus “read byte” protocol |
| i2c_smbus_write_byte_data | SMBus “write byte” protocol |
| i2c_smbus_read_word_data | SMBus “read word” protocol |
| i2c_smbus_write_word_data | SMBus “write word” protocol |
| i2c_smbus_read_block_data | SMBus “block read” protocol |
| i2c_smbus_write_block_data | SMBus “block write” protocol |
| i2c_smbus_xfer | execute SMBus protocol operations |
| i2c_smbus_read_i2c_block_data_or_emulated | read block or emulate |
| i2c_new_smbus_alert_device | get ara client for SMBus alert support |

## 总数统计

总共提取了 55 个函数+结构。

# Kernel Module idr.md API Documentation

## ID Allocation

## Overview

## IDR usage

## IDA usage

## Functions and structures

| 名称 | 描述 |
|---|---|
| IDR_INIT | Initialise an IDR. |
| DEFINE_IDR | Define a statically-allocated IDR. |
| idr_get_cursor | Return the current position of the cyclic allocator |
| idr_set_cursor | Set the current position of the cyclic allocator |
| idr_init_base | Initialise an IDR. |
| idr_init | Initialise an IDR. |
| idr_is_empty | Are there any IDs allocated? |
| idr_preload_end | end preload section started with idr_preload() |
| idr_for_each_entry | Iterate over an IDR’s elements of a given type. |
| idr_for_each_entry_ul | Iterate over an IDR’s elements of a given type. |
| idr_for_each_entry_continue | Continue iteration over an IDR’s elements of a given type |
| idr_for_each_entry_continue_ul | Continue iteration over an IDR’s elements of a given type |
| ida_alloc | Allocate an unused ID. |
| ida_alloc_min | Allocate an unused ID. |
| ida_alloc_max | Allocate an unused ID. |
| idr_alloc_u32 | Allocate an ID. |
| idr_alloc | Allocate an ID. |
| idr_alloc_cyclic | Allocate an ID cyclically. |
| idr_remove | Remove an ID from the IDR. |
| idr_find | Return pointer for given ID. |
| idr_for_each | Iterate through all stored pointers. |
| idr_get_next_ul | Find next populated entry. |
| idr_get_next | Find next populated entry. |
| idr_replace | replace pointer for given ID. |
| ida_alloc_range | Allocate an unused ID. |
| ida_free | Release an allocated ID. |
| ida_destroy | Free all IDs. |

## 总数统计

总共提取了 27 个函数+结构。

# Kernel Module infrastructure.md API Documentation

## Device drivers infrastructure

## The Basic Device Driver-Model Structures

| 名称 | 描述 |
|---|---|
| struct subsys_interface | interfaces to device functions |
| devm_alloc_percpu | Resource-managed alloc_percpu |
| struct dl_dev_state | Device driver presence tracking information. |
| struct device_removable | Whether the device is removable. The criteria for a device to be classified as removable is determined by its subsystem or bus. |
| struct dev_links_info | Device data related to device links. |
| struct device | The basic device structure |
| struct device_link | Device link representation. |
| device_iommu_mapped | Returns true when the device DMA is translated by an IOMMU |
| dev_bus_name | Return a device’s bus/class name, if at all possible |

## Device Drivers Base

| 名称 | 描述 |
|---|---|
| driver_init | initialize driver model. |
| driver_for_each_device | Iterator for devices bound to a driver. |
| driver_find_device | device iterator for locating a particular device. |
| driver_create_file | create sysfs file for driver. |
| driver_remove_file | remove sysfs file for driver. |
| driver_register | register driver with bus |
| driver_unregister | remove driver from system. |
| driver_find | locate driver on a bus by its name. |
| device_link_add | Create a link between two devices. |
| device_link_del | Delete a stateless link between two devices. |
| device_link_remove | Delete a stateless link between two devices. |
| dev_driver_string | Return a device’s driver name, if at all possible |
| devm_device_add_group | given a device, create a managed attribute group |
| devm_device_remove_group | remove a managed group from a device |
| devm_device_add_groups | create a bunch of managed attribute groups |
| devm_device_remove_groups | remove a list of managed groups |
| device_create_file | create sysfs attribute file for device. |
| device_remove_file | remove sysfs attribute file. |
| device_remove_file_self | remove sysfs attribute file from its own method. |
| device_create_bin_file | create sysfs binary attribute file for device. |
| device_remove_bin_file | remove sysfs binary attribute file |
| device_initialize | init device structure. |
| dev_set_name | set a device name |
| device_add | add device to device hierarchy. |
| device_register | register a device with the system. |
| get_device | increment reference count for device. |
| put_device | decrement reference count. |
| device_del | delete device from system. |
| device_unregister | unregister device from system. |
| device_for_each_child | device child iterator. |
| device_for_each_child_reverse | device child iterator in reversed order. |
| device_find_child | device iterator for locating a particular device. |
| device_find_child_by_name | device iterator for locating a child device. |
| __root_device_register | allocate and register a root device |
| root_device_unregister | unregister and free a root device |
| device_create | creates a device and registers it with sysfs |
| device_create_with_groups | creates a device and registers it with sysfs |
| device_destroy | removes a device that was created withdevice_create() |
| device_rename | renames a device |
| device_move | moves a device to a new parent |
| device_change_owner | change the owner of an existing device. |
| dev_err_probe | probe error check and log helper |
| set_primary_fwnode | Change the primary firmware node of a given device. |
| set_secondary_fwnode | Change the secondary firmware node of a given device. |
| device_set_of_node_from_dev | reuse device-tree node of another device |
| register_syscore_ops | Register a set of system core operations. |
| unregister_syscore_ops | Unregister a set of system core operations. |
| syscore_suspend | Execute all the registered system core suspend callbacks. |
| syscore_resume | Execute all the registered system core resume callbacks. |
| __class_create | create a struct class structure |
| class_destroy | destroys a struct class structure |
| class_dev_iter_init | initialize class device iterator |
| class_dev_iter_next | iterate to the next device |
| class_dev_iter_exit | finish iteration |
| class_for_each_device | device iterator |
| class_find_device | device iterator for locating a particular device |
| class_compat_register | register a compatibility class |
| class_compat_unregister | unregister a compatibility class |
| class_compat_create_link | create a compatibility class device link to a bus device |
| class_compat_remove_link | remove a compatibility class device link to a bus device |
| struct node_access_nodes | Access class device to hold user visible relationships to other nodes. |
| node_set_perf_attrs | Set the performance values for given access class |
| struct node_cache_info | Internal tracking for memory node caches |
| node_add_cache | add cache attribute to a memory node |
| unregister_node | unregister a node device |
| register_memory_node_under_compute_node | link memory node to its compute node for a given access class. |
| transport_class_register | register an initial transport class |
| transport_class_unregister | unregister a previously registered class |
| anon_transport_class_register | register an anonymous class |
| anon_transport_class_unregister | unregister an anon class |
| transport_setup_device | declare a new dev for transport class association but don’t make it visible yet. |
| transport_add_device | declare a new dev for transport class association |
| transport_configure_device | configure an already set up device |
| transport_remove_device | remove the visibility of a device |
| transport_destroy_device | destroy a removed device |
| device_bind_driver | bind a driver to one device. |
| wait_for_device_probe |  |
| device_attach | try to attach device to a driver. |
| device_driver_attach | attach a specific driver to a specific device |
| driver_attach | try to bind driver to devices. |
| device_release_driver | manually detach device from driver. |
| platform_device_register_resndata | add a platform-level device with resources and platform-specific data |
| platform_device_register_simple | add a platform-level device and its resources |
| platform_device_register_data | add a platform-level device with platform-specific data |
| platform_get_resource | get a resource for a device |
| devm_platform_get_and_ioremap_resource | call devm_ioremap_resource() for a platform device and get resource |
| devm_platform_ioremap_resource | call devm_ioremap_resource() for a platform device |
| devm_platform_ioremap_resource_byname | call devm_ioremap_resource for a platform device, retrieve the resource by name |
| platform_get_irq_optional | get an optional IRQ for a device |
| platform_get_irq | get an IRQ for a device |
| platform_irq_count | Count the number of IRQs a platform device uses |
| devm_platform_get_irqs_affinity | devm method to get a set of IRQs for a device using an interrupt affinity descriptor |
| platform_get_resource_byname | get a resource for a device by name |
| platform_get_irq_byname | get an IRQ for a device by name |
| platform_get_irq_byname_optional | get an optional IRQ for a device by name |
| platform_add_devices | add a numbers of platform devices |
| platform_device_put | destroy a platform device |
| platform_device_alloc | create a platform device |
| platform_device_add_resources | add resources to a platform device |
| platform_device_add_data | add platform-specific data to a platform device |
| platform_device_add | add a platform device to device hierarchy |
| platform_device_del | remove a platform-level device |
| platform_device_register | add a platform-level device |
| platform_device_unregister | unregister a platform-level device |
| platform_device_register_full | add a platform-level device with resources and platform-specific data |
| __platform_driver_register | register a driver for platform-level devices |
| platform_driver_unregister | unregister a driver for platform-level devices |
| __platform_driver_probe | register driver for non-hotpluggable device |
| __platform_create_bundle | register driver and create corresponding device |
| __platform_register_drivers | register an array of platform drivers |
| platform_unregister_drivers | unregister an array of platform drivers |
| platform_find_device_by_driver | Find a platform device with a given driver. |
| bus_for_each_dev | device iterator. |
| bus_find_device | device iterator for locating a particular device. |
| subsys_find_device_by_id | find a device with a specific enumeration number |
| bus_for_each_drv | driver iterator |
| bus_rescan_devices | rescan devices on the bus for possible drivers |
| device_reprobe | remove driver for a device and probe for a new driver |
| bus_register | register a driver-core subsystem |
| bus_unregister | remove a bus from the system |
| subsys_dev_iter_init | initialize subsys device iterator |
| subsys_dev_iter_next | iterate to the next device |
| subsys_dev_iter_exit | finish iteration |
| subsys_system_register | register a subsystem at /sys/devices/system/ |
| subsys_virtual_register | register a subsystem at /sys/devices/virtual/ |

## Device Drivers DMA Management

| 名称 | 描述 |
|---|---|
| dmam_free_coherent | Managed dma_free_coherent() |
| dmam_alloc_attrs | Managed dma_alloc_attrs() |
| dma_map_sg_attrs | Map the given buffer for DMA |
| dma_map_sgtable | Map the given buffer for DMA |
| dma_can_mmap | check if a given device supports dma_mmap_* |
| dma_mmap_attrs | map a coherent DMA allocation into user space |

## Device drivers PnP support

| 名称 | 描述 |
|---|---|
| pnp_register_protocol | adds a pnp protocol to the pnp layer |
| pnp_unregister_protocol | removes a pnp protocol from the pnp layer |
| pnp_request_card_device | Searches for a PnP device under the specified card |
| pnp_release_card_device | call this when the driver no longer needs the device |
| pnp_register_card_driver | registers a PnP card driver with the PnP Layer |
| pnp_unregister_card_driver | unregisters a PnP card driver from the PnP Layer |
| pnp_add_id | adds an EISA id to the specified device |
| pnp_start_dev | low-level start of the PnP device |
| pnp_stop_dev | low-level disable of the PnP device |
| pnp_activate_dev | activates a PnP device for use |
| pnp_disable_dev | disables device |
| pnp_is_active | Determines if a device is active based on its current resources |

## Userspace IO devices

| 名称 | 描述 |
|---|---|
| uio_event_notify | trigger an interrupt event |
| __uio_register_device | register a new userspace IO device |
| __devm_uio_register_device | Resource manageduio_register_device() |
| uio_unregister_device | unregister a industrial IO device |
| struct uio_mem | description of a UIO memory region |
| struct uio_port | description of a UIO port region |
| struct uio_info | UIO device capabilities |
| uio_register_device | register a new userspace IO device |
| devm_uio_register_device | Resource manageduio_register_device() |

## 总数统计

总共提取了 161 个函数+结构。

# Kernel Module input.md API Documentation

## Input Subsystem

## Input core

| 名称 | 描述 |
|---|---|
| struct input_value | input value representation |
| struct input_dev | represents an input device |
| struct input_handler | implements one of interfaces for input devices |
| struct input_handle | links input device with an input handler |
| input_set_events_per_packet | tell handlers about the driver event rate |
| struct ff_device | force-feedback part of an input device |
| input_event | report new input event |
| input_inject_event | send input event from input handler |
| input_alloc_absinfo | allocates array of input_absinfo structs |
| input_grab_device | grabs device for exclusive use |
| input_release_device | release previously grabbed device |
| input_open_device | open input device |
| input_close_device | close input device |
| input_scancode_to_scalar | converts scancode instructinput_keymap_entry |
| input_get_keycode | retrieve keycode currently mapped to a given scancode |
| input_set_keycode | attribute a keycode to a given scancode |
| input_reset_device | reset/restore the state of input device |
| input_allocate_device | allocate memory for new input device |
| devm_input_allocate_device | allocate managed input device |
| input_free_device | free memory occupied by input_dev structure |
| input_set_timestamp | set timestamp for input events |
| input_get_timestamp | get timestamp for input events |
| input_set_capability | mark device as capable of a certain event |
| input_enable_softrepeat | enable software autorepeat |
| input_register_device | register device with input core |
| input_unregister_device | unregister previously registered device |
| input_register_handler | register a new input handler |
| input_unregister_handler | unregisters an input handler |
| input_handler_for_each_handle | handle iterator |
| input_register_handle | register a new input handle |
| input_unregister_handle | unregister an input handle |
| input_get_new_minor | allocates a new input minor number |
| input_free_minor | release previously allocated minor |
| input_ff_upload | upload effect into force-feedback device |
| input_ff_erase | erase a force-feedback effect from device |
| input_ff_event | generic handler for force-feedback events |
| input_ff_create | create force-feedback device |
| input_ff_destroy | frees force feedback portion of input device |
| input_ff_create_memless | create memoryless force-feedback device |

## Multitouch Library

| 名称 | 描述 |
|---|---|
| struct input_mt_slot | represents the state of an input MT slot |
| struct input_mt | state of tracked contacts |
| struct input_mt_pos | contact position |
| input_mt_init_slots | initialize MT input slots |
| input_mt_destroy_slots | frees the MT slots of the input device |
| input_mt_report_slot_state | report contact state |
| input_mt_report_finger_count | report contact count |
| input_mt_report_pointer_emulation | common pointer emulation |
| input_mt_drop_unused | Inactivate slots not seen in this frame |
| input_mt_sync_frame | synchronize mt frame |
| input_mt_assign_slots | perform a best-match assignment |
| input_mt_get_slot_by_key | return slot matching key |

## Matrix keyboards/keypads

| 名称 | 描述 |
|---|---|
| struct matrix_keymap_data | keymap for matrix keyboards |
| struct matrix_keypad_platform_data | platform-dependent keypad data |

## Sparse keymap support

| 名称 | 描述 |
|---|---|
| struct key_entry | keymap entry for use in sparse keymap |
| sparse_keymap_entry_from_scancode | perform sparse keymap lookup |
| sparse_keymap_entry_from_keycode | perform sparse keymap lookup |
| sparse_keymap_setup | set up sparse keymap for an input device |
| sparse_keymap_report_entry | report event corresponding to given key entry |
| sparse_keymap_report_event | report event corresponding to given scancode |

## 总数统计

总共提取了 59 个函数+结构。

# Kernel Module kernel-api.md API Documentation

## The Linux Kernel API

## List Management Functions

| 名称 | 描述 |
|---|---|
| INIT_LIST_HEAD | Initialize a list_head structure |
| list_add | add a new entry |
| list_add_tail | add a new entry |
| list_del | deletes entry from list. |
| list_replace | replace old entry by new one |
| list_replace_init | replace old entry by new one and initialize the old one |
| list_swap | replace entry1 with entry2 and re-add entry1 at entry2’s position |
| list_del_init | deletes entry from list and reinitialize it. |
| list_move | delete from one list and add as another’s head |
| list_move_tail | delete from one list and add as another’s tail |
| list_bulk_move_tail | move a subsection of a list to its tail |
| list_is_first | tests whetherlistis the first entry in listhead |
| list_is_last | tests whetherlistis the last entry in listhead |
| list_empty | tests whether a list is empty |
| list_del_init_careful | deletes entry from list and reinitialize it. |
| list_empty_careful | tests whether a list is empty and not being modified |
| list_rotate_left | rotate the list to the left |
| list_rotate_to_front | Rotate list to specific item. |
| list_is_singular | tests whether a list has just one entry. |
| list_cut_position | cut a list into two |
| list_cut_before | cut a list into two, before given entry |
| list_splice | join two lists, this is designed for stacks |
| list_splice_tail | join two lists, each list being a queue |
| list_splice_init | join two lists and reinitialise the emptied list. |
| list_splice_tail_init | join two lists and reinitialise the emptied list |
| list_entry | get the struct for this entry |
| list_first_entry | get the first element from a list |
| list_last_entry | get the last element from a list |
| list_first_entry_or_null | get the first element from a list |
| list_next_entry | get the next element in list |
| list_prev_entry | get the prev element in list |
| list_for_each | iterate over a list |
| list_for_each_continue | continue iteration over a list |
| list_for_each_prev | iterate over a list backwards |
| list_for_each_safe | iterate over a list safe against removal of list entry |
| list_for_each_prev_safe | iterate over a list backwards safe against removal of list entry |
| list_entry_is_head | test if the entry points to the head of the list |
| list_for_each_entry | iterate over list of given type |
| list_for_each_entry_reverse | iterate backwards over list of given type. |
| list_prepare_entry | prepare a pos entry for use inlist_for_each_entry_continue() |
| list_for_each_entry_continue | continue iteration over list of given type |
| list_for_each_entry_continue_reverse | iterate backwards from the given point |
| list_for_each_entry_from | iterate over list of given type from the current point |
| list_for_each_entry_from_reverse | iterate backwards over list of given type from the current point |
| list_for_each_entry_safe | iterate over list of given type safe against removal of list entry |
| list_for_each_entry_safe_continue | continue list iteration safe against removal |
| list_for_each_entry_safe_from | iterate over list from current point safe against removal |
| list_for_each_entry_safe_reverse | iterate backwards over list safe against removal |
| list_safe_reset_next | reset a stale list_for_each_entry_safe loop |
| hlist_unhashed | Has node been removed from list and reinitialized? |
| hlist_unhashed_lockless | Version of hlist_unhashed for lockless use |
| hlist_empty | Is the specified hlist_head structure an empty hlist? |
| hlist_del | Delete the specified hlist_node from its list |
| hlist_del_init | Delete the specified hlist_node from its list and initialize |
| hlist_add_head | add a new entry at the beginning of the hlist |
| hlist_add_before | add a new entry before the one specified |
| hlist_add_behind | add a new entry after the one specified |
| hlist_add_fake | create a fake hlist consisting of a single headless node |
| hlist_fake | Is this node a fake hlist? |
| hlist_is_singular_node | is node the only element of the specified hlist? |
| hlist_move_list | Move an hlist |
| hlist_for_each_entry | iterate over list of given type |
| hlist_for_each_entry_continue | iterate over a hlist continuing after current point |
| hlist_for_each_entry_from | iterate over a hlist continuing from current point |
| hlist_for_each_entry_safe | iterate over list of given type safe against removal of list entry |

## Basic C Library Functions

| 名称 | 描述 |
|---|---|
| simple_strtoull | convert a string to an unsigned long long |
| simple_strtoul | convert a string to an unsigned long |
| simple_strtol | convert a string to a signed long |
| simple_strtoll | convert a string to a signed long long |
| vsnprintf | Format a string and place it in a buffer |
| vscnprintf | Format a string and place it in a buffer |
| snprintf | Format a string and place it in a buffer |
| scnprintf | Format a string and place it in a buffer |
| vsprintf | Format a string and place it in a buffer |
| sprintf | Format a string and place it in a buffer |
| vbin_printf | Parse a format string and place args’ binary value in a buffer |
| bstr_printf | Format a string from binary arguments and place it in a buffer |
| bprintf | Parse a format string and place args’ binary value in a buffer |
| vsscanf | Unformat a buffer into a list of arguments |
| sscanf | Unformat a buffer into a list of arguments |
| kstrtoul | convert a string to an unsigned long |
| kstrtol | convert a string to a long |
| kstrtoull | convert a string to an unsigned long long |
| kstrtoll | convert a string to a long long |
| kstrtouint | convert a string to an unsigned int |
| kstrtoint | convert a string to an int |
| kstrtobool | convert common user inputs into boolean values |
| string_get_size | get the size in the specified units |
| string_unescape | unquote characters in the given string |
| string_escape_mem | quote characters in the given memory buffer |
| kfree_strarray | free a number of dynamically allocated strings contained in an array and the array itself |
| strncasecmp | Case insensitive, length-limited string comparison |
| strcpy | Copy aNULterminated string |
| strncpy | Copy a length-limited, C-string |
| strlcpy | Copy a C-string into a sized buffer |
| strscpy | Copy a C-string into a sized buffer |
| strscpy_pad | Copy a C-string into a sized buffer |
| stpcpy | copy a string from src to dest returning a pointer to the new end of dest, including src’sNUL-terminator. May overrun dest. |
| strcat | Append oneNUL-terminatedstring to another |
| strncat | Append a length-limited, C-string to another |
| strlcat | Append a length-limited, C-string to another |
| strcmp | Compare two strings |
| strncmp | Compare two length-limited strings |
| strchr | Find the first occurrence of a character in a string |
| strchrnul | Find and return a character in a string, or end of string |
| strrchr | Find the last occurrence of a character in a string |
| strnchr | Find a character in a length limited string |
| skip_spaces | Removes leading whitespace fromstr. |
| strim | Removes leading and trailing whitespace froms. |
| strlen | Find the length of a string |
| strnlen | Find the length of a length-limited string |
| strspn | Calculate the length of the initial substring ofswhich only contain letters inaccept |
| strcspn | Calculate the length of the initial substring ofswhich does not contain letters inreject |
| strpbrk | Find the first occurrence of a set of characters |
| strsep | Split a string into tokens |
| sysfs_streq | return true if strings are equal, modulo trailing newline |
| match_string | matches given string in an array |
| __sysfs_match_string | matches given string in an array |
| memset | Fill a region of memory with the given value |
| memset16 | Fill a memory area with a uint16_t |
| memset32 | Fill a memory area with a uint32_t |
| memset64 | Fill a memory area with a uint64_t |
| memcpy | Copy one area of memory to another |
| memmove | Copy one area of memory to another |
| memcmp | Compare two areas of memory |
| bcmp | returns 0 if and only if the buffers have identical contents. |
| memscan | Find a character in an area of memory. |
| strstr | Find the first substring in aNULterminated string |
| strnstr | Find the first substring in a length-limited string |
| memchr | Find a character in an area of memory. |
| memchr_inv | Find an unmatching character in an area of memory. |
| strreplace | Replace all occurrences of character in string. |
| sysfs_match_string | matches given string in an array |
| strstarts | doesstrstart withprefix? |
| memzero_explicit | Fill a region of memory (e.g. sensitive keying data) with 0s. |
| kbasename | return the last part of a pathname. |
| memcpy_and_pad | Copy one buffer to another with padding |
| str_has_prefix | Test if a string has a given prefix |
| kstrdup | allocate space for and copy an existing string |
| kstrdup_const | conditionally duplicate an existing const string |
| kstrndup | allocate space for and copy an existing string |
| kmemdup | duplicate region of memory |
| kmemdup_nul | Create a NUL-terminated string from unterminated data |
| memdup_user | duplicate memory region from user space |
| vmemdup_user | duplicate memory region from user space |
| strndup_user | duplicate an existing string from user space |
| memdup_user_nul | duplicate memory region from user space and NUL-terminate |

## Basic Kernel Library Functions

| 名称 | 描述 |
|---|---|
| set_bit | Atomically set a bit in memory |
| clear_bit | Clears a bit in memory |
| change_bit | Toggle a bit in memory |
| test_and_set_bit | Set a bit and return its old value |
| test_and_clear_bit | Clear a bit and return its old value |
| test_and_change_bit | Change a bit and return its old value |
| __set_bit | Set a bit in memory |
| __clear_bit | Clears a bit in memory |
| __change_bit | Toggle a bit in memory |
| __test_and_set_bit | Set a bit and return its old value |
| __test_and_clear_bit | Clear a bit and return its old value |
| __test_and_change_bit | Change a bit and return its old value |
| test_bit | Determine whether a bit is set |
| clear_bit_unlock | Clear a bit in memory, for unlock |
| __clear_bit_unlock | Clears a bit in memory |
| test_and_set_bit_lock | Set a bit and return its old value, for lock |
| clear_bit_unlock_is_negative_byte | Clear a bit in memory and test if bottom byte is negative, for unlock. |
| __bitmap_shift_right | logical right shift of the bits in a bitmap |
| __bitmap_shift_left | logical left shift of the bits in a bitmap |
| bitmap_cut | remove bit region from bitmap and right shift remaining bits |
| bitmap_find_next_zero_area_off | find a contiguous aligned zero area |
| bitmap_parse_user | convert an ASCII hex string in a user buffer into a bitmap |
| bitmap_print_to_pagebuf | convert bitmap to list or hex format ASCII string |
| bitmap_print_bitmask_to_buf | convert bitmap to hex bitmask format ASCII string |
| bitmap_print_list_to_buf | convert bitmap to decimal list format ASCII string |
| bitmap_parselist | convert list format ASCII string to bitmap |
| bitmap_parselist_user |  |
| bitmap_parse | convert an ASCII hex string into a bitmap. |
| bitmap_remap | Apply map defined by a pair of bitmaps to another bitmap |
| bitmap_bitremap | Apply map defined by a pair of bitmaps to a single bit |
| bitmap_find_free_region | find a contiguous aligned mem region |
| bitmap_release_region | release allocated bitmap region |
| bitmap_allocate_region | allocate bitmap region |
| bitmap_copy_le | copy a bitmap, putting the bits into little-endian order. |
| bitmap_from_arr32 | copy the contents of u32 array of bits to bitmap |
| bitmap_to_arr32 | copy the contents of bitmap to a u32 array of bits |
| bitmap_print_to_buf | convert bitmap to list or hex format ASCII string |
| bitmap_pos_to_ord | find ordinal of set bit at given position in bitmap |
| bitmap_ord_to_pos | find position of n-th set bit in bitmap |
| bitmap_onto | translate one bitmap relative to another |
| bitmap_fold | fold larger bitmap into smaller, modulo specified size |
| bitmap_find_next_zero_area | find a contiguous aligned zero area |
| bitmap_or_equal | Check whether the or of two bitmaps is equal to a third |
| BITMAP_FROM_U64 | Represent u64 value in the format suitable for bitmap. |
| bitmap_from_u64 | Check and swap words within u64. |
| bitmap_get_value8 | get an 8-bit value within a memory region |
| bitmap_set_value8 | set an 8-bit value within a memory region |
| get_option | Parse integer from an option string |
| get_options | Parse a string into a list of integers |
| memparse | parse a string with mem suffixes into a number |
| sort_r | sort an array of elements |
| list_sort | sort a list |
| textsearch_register | register a textsearch module |
| textsearch_unregister | unregister a textsearch module |
| textsearch_find_continuous | search a pattern in continuous/linear data |
| textsearch_prepare | Prepare a search |
| textsearch_destroy | destroy a search configuration |
| textsearch_next | continue searching for a pattern |
| textsearch_find | start searching for a pattern |
| textsearch_get_pattern | return head of the pattern |
| textsearch_get_pattern_len | return length of the pattern |

## CRC and Math Functions in Linux

| 名称 | 描述 |
|---|---|
| crc4 | calculate the 4-bit crc of a value. |
| crc7_be | update the CRC7 for the data buffer |
| crc8_populate_msb | fill crc table for given polynomial in reverse bit order. |
| crc8_populate_lsb | fill crc table for given polynomial in regular bit order. |
| crc8 | calculate a crc8 over the given input data. |
| crc16 | compute the CRC-16 for the data buffer |
| crc32_le_generic | Calculate bitwise little-endian Ethernet AUTODIN II CRC32/CRC32C |
| crc32_generic_shift | Appendlen0 bytes to crc, in logarithmic time |
| crc32_be_generic | Calculate bitwise big-endian Ethernet AUTODIN II CRC32 |
| crc_ccitt | recompute the CRC (CRC-CCITT variant) for the data buffer |
| crc_ccitt_false | recompute the CRC (CRC-CCITT-FALSE variant) for the data buffer |
| crc_itu_t | Compute the CRC-ITU-T for the data buffer |
| is_power_of_2 | check if a value is a power of two |
| __roundup_pow_of_two | round up to nearest power of two |
| __rounddown_pow_of_two | round down to nearest power of two |
| const_ilog2 | log base 2 of 32-bit or a 64-bit constant unsigned value |
| ilog2 | log base 2 of 32-bit or a 64-bit unsigned value |
| roundup_pow_of_two | round the given value up to nearest power of two |
| rounddown_pow_of_two | round the given value down to nearest power of two |
| order_base_2 | calculate the (rounded up) base 2 order of the argument |
| bits_per | calculate the number of bits required for the argument |
| int_pow | computes the exponentiation of the given base and exponent |
| int_sqrt | computes the integer square root |
| int_sqrt64 | strongly typed int_sqrt function when minimum 64 bit input is expected. |
| do_div | returns 2 values: calculate remainder and update new dividend |
| div_u64_rem | unsigned 64bit divide with 32bit divisor with remainder |
| div_u64 | unsigned 64bit divide with 32bit divisor |
| div_s64 | signed 64bit divide with 32bit divisor |
| DIV64_U64_ROUND_CLOSEST | unsigned 64bit divide with 64bit divisor rounded to nearest integer |
| div_s64_rem | signed 64bit divide with 64bit divisor and remainder |
| div64_u64_rem | unsigned 64bit divide with 64bit divisor and remainder |
| div64_u64 | unsigned 64bit divide with 64bit divisor |
| div64_s64 | signed 64bit divide with 64bit divisor |
| gcd | calculate and return the greatest common divisor of 2 unsigned longs |
| generate_random_uuid | generate a random UUID |
| uuid_is_valid | checks if a UUID string is valid |

## Kernel IPC facilities

| 名称 | 描述 |
|---|---|
| ipc_init | initialise ipc subsystem |
| ipc_init_ids | initialise ipc identifiers |
| ipc_init_proc_interface | create a proc interface for sysipc types using a seq_file interface. |
| ipc_findkey | find a key in an ipc identifier set |
| ipc_addid | add an ipc identifier |
| ipcget_new | create a new ipc object |
| ipc_check_perms | check security and permissions for an ipc object |
| ipcget_public | get an ipc object or create a new one |
| ipc_kht_remove | remove an ipc from the key hashtable |
| ipc_search_maxidx | search for the highest assigned index |
| ipc_rmid | remove an ipc identifier |
| ipc_set_key_private | switch the key of an existing ipc to IPC_PRIVATE |
| ipcperms | check ipc permissions |
| kernel_to_ipc64_perm | convert kernel ipc permissions to user |
| ipc64_perm_to_ipc_perm | convert new ipc permissions to old |
| ipc_obtain_object_idr |  |
| ipc_obtain_object_check |  |
| ipcget | Common sys_*get() code |
| ipc_update_perm | update the permissions of an ipc object |
| ipcctl_obtain_check | retrieve an ipc object and check permissions |
| ipc_parse_version | ipc call version |

## FIFO Buffer

| 名称 | 描述 |
|---|---|
| DECLARE_KFIFO_PTR | macro to declare a fifo pointer object |
| DECLARE_KFIFO | macro to declare a fifo object |
| INIT_KFIFO | Initialize a fifo declared by DECLARE_KFIFO |
| DEFINE_KFIFO | macro to define and initialize a fifo |
| kfifo_initialized | Check if the fifo is initialized |
| kfifo_esize | returns the size of the element managed by the fifo |
| kfifo_recsize | returns the size of the record length field |
| kfifo_size | returns the size of the fifo in elements |
| kfifo_reset | removes the entire fifo content |
| kfifo_reset_out | skip fifo content |
| kfifo_len | returns the number of used elements in the fifo |
| kfifo_is_empty | returns true if the fifo is empty |
| kfifo_is_empty_spinlocked | returns true if the fifo is empty using a spinlock for locking |
| kfifo_is_empty_spinlocked_noirqsave | returns true if the fifo is empty using a spinlock for locking, doesn’t disable interrupts |
| kfifo_is_full | returns true if the fifo is full |
| kfifo_avail | returns the number of unused elements in the fifo |
| kfifo_skip | skip output data |
| kfifo_peek_len | gets the size of the next fifo record |
| kfifo_alloc | dynamically allocates a new fifo buffer |
| kfifo_free | frees the fifo |
| kfifo_init | initialize a fifo using a preallocated buffer |
| kfifo_put | put data into the fifo |
| kfifo_get | get data from the fifo |
| kfifo_peek | get data from the fifo without removing |
| kfifo_in | put data into the fifo |
| kfifo_in_spinlocked | put data into the fifo using a spinlock for locking |
| kfifo_in_spinlocked_noirqsave | put data into fifo using a spinlock for locking, don’t disable interrupts |
| kfifo_out | get data from the fifo |
| kfifo_out_spinlocked | get data from the fifo using a spinlock for locking |
| kfifo_out_spinlocked_noirqsave | get data from the fifo using a spinlock for locking, don’t disable interrupts |
| kfifo_from_user | puts some data from user space into the fifo |
| kfifo_to_user | copies data from the fifo into user space |
| kfifo_dma_in_prepare | setup a scatterlist for DMA input |
| kfifo_dma_in_finish | finish a DMA IN operation |
| kfifo_dma_out_prepare | setup a scatterlist for DMA output |
| kfifo_dma_out_finish | finish a DMA OUT operation |
| kfifo_out_peek | gets some data from the fifo |

## relay interface support

| 名称 | 描述 |
|---|---|
| relay_buf_full | boolean, is the channel buffer full? |
| relay_reset | reset the channel |
| relay_open | create a new relay channel |
| relay_late_setup_files | triggers file creation |
| relay_switch_subbuf | switch to a new sub-buffer |
| relay_subbufs_consumed | update the buffer’s sub-buffers-consumed count |
| relay_close | close the channel |
| relay_flush | close the channel |
| relay_mmap_buf | mmap channel buffer to process address space |
| relay_alloc_buf | allocate a channel buffer |
| relay_create_buf | allocate and initialize a channel buffer |
| relay_destroy_channel | free the channel struct |
| relay_destroy_buf | destroy an rchan_buf struct and associated buffer |
| relay_remove_buf | remove a channel buffer |
| relay_buf_empty | boolean, is the channel buffer empty? |
| wakeup_readers | wake up readers waiting on a channel |
| __relay_reset | reset a channel buffer |
| relay_close_buf | close a channel buffer |
| relay_file_open | open file op for relay files |
| relay_file_mmap | mmap file op for relay files |
| relay_file_poll | poll file op for relay files |
| relay_file_release | release file op for relay files |
| relay_file_read_subbuf_avail | return bytes available in sub-buffer |
| relay_file_read_start_pos | find the first available byte to read |
| relay_file_read_end_pos | return the new read position |

## Module Support

| 名称 | 描述 |
|---|---|
| __request_module | try to load a kernel module |

## Hardware Interfaces

| 名称 | 描述 |
|---|---|
| request_dma | request and reserve a system DMA channel |
| free_dma | free a reserved system DMA channel |
| request_resource_conflict | request and reserve an I/O or memory resource |
| find_next_iomem_res | Finds the lowest iomem resource that covers part of [start..**end**]. |
| reallocate_resource | allocate a slot in the resource tree given range & alignment. The resource will be relocated if the new size cannot be reallocated in the current location. |
| lookup_resource | find an existing resource by a resource start address |
| insert_resource_conflict | Inserts resource in the resource tree |
| insert_resource_expand_to_fit | Insert a resource into the resource tree |
| resource_alignment | calculate resource’s alignment |
| release_mem_region_adjustable | release a previously reserved memory region |
| merge_system_ram_resource | mark the System RAM resource mergeable and try to merge it with adjacent, mergeable resources |
| request_resource | request and reserve an I/O or memory resource |
| release_resource | release a previously reserved resource |
| walk_iomem_res_desc | Walks through iomem resources and calls func() with matching resource ranges. * |
| region_intersects | determine intersection of region with known resources |
| allocate_resource | allocate empty slot in the resource tree given range & alignment. The resource will be reallocated with a new size if it was already allocated |
| insert_resource | Inserts a resource in the resource tree |
| remove_resource | Remove a resource in the resource tree |
| adjust_resource | modify a resource’s start and size |
| __request_region | create a new busy resource region |
| __release_region | release a previously reserved resource region |
| devm_request_resource | request and reserve an I/O or memory resource |
| devm_release_resource | release a previously requested resource |
| devm_request_free_mem_region | find free region for device private memory |
| arch_phys_wc_add | add a WC MTRR and handle errors if PAT is unavailable |

## Security Framework

| 名称 | 描述 |
|---|---|
| security_init | initializes the security framework |
| security_add_hooks | Add a modules hooks to the hook lists. |
| lsm_cred_alloc | allocate a composite cred blob |
| lsm_early_cred | during initialization allocate a composite cred blob |
| lsm_file_alloc | allocate a composite file blob |
| lsm_inode_alloc | allocate a composite inode blob |
| lsm_task_alloc | allocate a composite task blob |
| lsm_ipc_alloc | allocate a composite ipc blob |
| lsm_msg_msg_alloc | allocate a composite msg_msg blob |
| lsm_early_task | during initialization allocate a composite task blob |
| lsm_superblock_alloc | allocate a composite superblock blob |
| securityfs_create_file | create a file in the securityfs filesystem |
| securityfs_create_dir | create a directory in the securityfs filesystem |
| securityfs_create_symlink | create a symlink in the securityfs filesystem |
| securityfs_remove | removes a file or directory from the securityfs filesystem |

## Audit Interfaces

| 名称 | 描述 |
|---|---|
| audit_log_start | obtain an audit buffer |
| audit_log_format | format a message into the audit buffer. |
| audit_log_end | end one audit record |
| audit_log | Log an audit record |
| audit_alloc | allocate an audit context block for a task |
| __audit_free | free a per-task audit context |
| __audit_syscall_entry | fill in an audit record at syscall entry |
| __audit_syscall_exit | deallocate audit context after a system call |
| __audit_reusename | fill out filename with info from existing entry |
| __audit_getname | add a name to the list |
| __audit_inode | store the inode and device from a lookup |
| auditsc_get_stamp | get local copies of audit_context values |
| __audit_mq_open | record audit data for a POSIX MQ open |
| __audit_mq_sendrecv | record audit data for a POSIX MQ timed send/receive |
| __audit_mq_notify | record audit data for a POSIX MQ notify |
| __audit_mq_getsetattr | record audit data for a POSIX MQ get/set attribute |
| __audit_ipc_obj | record audit data for ipc object |
| __audit_ipc_set_perm | record audit data for new ipc permissions |
| __audit_socketcall | record audit data for sys_socketcall |
| __audit_fd_pair | record audit data for pipe and socketpair |
| __audit_sockaddr | record audit data for sys_bind, sys_connect, sys_sendto |
| audit_signal_info_syscall | record signal info for syscalls |
| __audit_log_bprm_fcaps | store information about a loading bprm and relevant fcaps |
| __audit_log_capset | store information about the arguments to the capset syscall |
| audit_core_dumps | record information about processes that end abnormally |
| audit_seccomp | record information about a seccomp action |
| audit_rule_change | apply all rules to the specified message type |
| audit_list_rules_send | list the audit rules |
| parent_len | find the length of the parent portion of a pathname |
| audit_compare_dname_path | compare given dentry name with last component in given path. Return of 0 indicates a match. |

## Accounting Framework

| 名称 | 描述 |
|---|---|
| sys_acct | enable/disable process accounting |
| acct_collect | collect accounting information into pacct_struct |
| acct_process | handles process accounting for an exiting task |

## Block Devices

| 名称 | 描述 |
|---|---|
| blk_queue_flag_set | atomically set a queue flag |
| blk_queue_flag_clear | atomically clear a queue flag |
| blk_queue_flag_test_and_set | atomically test and set a queue flag |
| blk_op_str | Return string XXX in the REQ_OP_XXX. |
| blk_sync_queue | cancel any pending callbacks on a queue |
| blk_set_pm_only | increment pm_only counter |
| blk_put_queue | decrement the request_queue refcount |
| blk_cleanup_queue | shutdown a request queue |
| blk_get_queue | increment the request_queue refcount |
| blk_get_request | allocate a request |
| submit_bio_noacct | re-submit a bio to the block device layer for I/O |
| submit_bio | submit a bio to the block device layer for I/O |
| blk_insert_cloned_request | Helper for stacking drivers to submit a request |
| blk_rq_err_bytes | determine number of bytes till the next failure boundary |
| bio_start_io_acct | start I/O accounting for bio based drivers |
| blk_update_request | Complete multiple bytes without completing the request |
| rq_flush_dcache_pages | Helper function to flush all pages in a request |
| blk_lld_busy | Check if underlying low-level drivers of a device are busy |
| blk_rq_unprep_clone | Helper function to free all bios in a cloned request |
| blk_rq_prep_clone | Helper function to setup clone request |
| blk_start_plug | initialize blk_plug and track it inside the task_struct |
| blk_finish_plug | mark the end of a batch of submitted I/O |
| blk_queue_enter | try to increase q->q_usage_counter |
| blk_cloned_rq_check_limits | Helper function to check a cloned request for the new queue limits |
| blk_rq_map_user_iov | map user data to a request, for passthrough requests |
| blk_rq_unmap_user | unmap a request with user data |
| blk_rq_map_kern | map kernel data to a request, for passthrough requests |
| blk_release_queue | releases all allocated resources of the request_queue |
| blk_register_queue | register a block layer queue with sysfs |
| blk_unregister_queue | counterpart ofblk_register_queue() |
| blk_set_default_limits | reset limits to default values |
| blk_set_stacking_limits | set default limits for stacking devices |
| blk_queue_bounce_limit | set bounce buffer limit for queue |
| blk_queue_max_hw_sectors | set max sectors for a request for this queue |
| blk_queue_chunk_sectors | set size of the chunk for this queue |
| blk_queue_max_discard_sectors | set max sectors for a single discard |
| blk_queue_max_write_same_sectors | set max sectors for a single write same |
| blk_queue_max_write_zeroes_sectors | set max sectors for a single write zeroes |
| blk_queue_max_zone_append_sectors | set max sectors for a single zone append |
| blk_queue_max_segments | set max hw segments for a request for this queue |
| blk_queue_max_discard_segments | set max segments for discard requests |
| blk_queue_max_segment_size | set max segment size for blk_rq_map_sg |
| blk_queue_logical_block_size | set logical block size for the queue |
| blk_queue_physical_block_size | set physical block size for the queue |
| blk_queue_zone_write_granularity | set zone write granularity for the queue |
| blk_queue_alignment_offset | set physical block alignment offset |
| blk_limits_io_min | set minimum request size for a device |
| blk_queue_io_min | set minimum request size for the queue |
| blk_limits_io_opt | set optimal request size for a device |
| blk_queue_io_opt | set optimal request size for the queue |
| blk_stack_limits | adjust queue_limits for stacked devices |
| disk_stack_limits | adjust queue limits for stacked drivers |
| blk_queue_update_dma_pad | update pad mask |
| blk_queue_segment_boundary | set boundary rules for segment merging |
| blk_queue_virt_boundary | set boundary rules for bio merging |
| blk_queue_dma_alignment | set dma length and memory alignment |
| blk_queue_update_dma_alignment | update dma length and memory alignment |
| blk_set_queue_depth | tell the block layer about the device queue depth |
| blk_queue_write_cache | configure queue’s write cache |
| blk_queue_required_elevator_features | Set a queue required elevator features |
| blk_queue_can_use_dma_map_merging | configure queue for merging segments. |
| blk_queue_set_zoned | configure a disk queue zoned model. |
| blk_execute_rq_nowait | insert a request to I/O scheduler for execution |
| blk_execute_rq | insert a request into queue for execution |
| blkdev_issue_flush | queue a flush |
| blkdev_issue_discard | queue a discard |
| blkdev_issue_write_same | queue a write same operation |
| __blkdev_issue_zeroout | generate number of zero filed write bios |
| blkdev_issue_zeroout | zero-fill a block range |
| blk_rq_count_integrity_sg | Count number of integrity scatterlist elements |
| blk_rq_map_integrity_sg | Map integrity metadata into a scatterlist |
| blk_integrity_compare | Compare integrity profile of two disks |
| blk_integrity_register | Register a gendisk as being integrity-capable |
| blk_integrity_unregister | Unregister block integrity profile |
| blk_trace_ioctl | handle the ioctls associated with tracing |
| blk_trace_shutdown | stop and cleanup trace structures |
| blk_add_trace_rq | Add a trace for a request oriented action |
| blk_add_trace_bio | Add a trace for a bio oriented action |
| blk_add_trace_bio_remap | Add a trace for a bio-remap operation |
| blk_add_trace_rq_remap | Add a trace for a request-remap operation |
| disk_release | releases all allocated resources of the gendisk |
| __register_blkdev | register a new block device |
| device_add_disk | add disk information to kernel list |
| del_gendisk | remove the gendisk |
| put_disk | decrements the gendisk refcount |
| blk_cleanup_disk | shutdown a gendisk allocated by blk_alloc_disk |
| set_disk_ro | set a gendisk read-only |
| freeze_bdev | lock a filesystem and force it into a consistent state |
| thaw_bdev | unlock filesystem |
| bd_prepare_to_claim | claim a block device |
| bd_abort_claiming | abort claiming of a block device |
| blkdev_get_by_dev | open a block device by device number |
| blkdev_get_by_path | open a block device by name |
| lookup_bdev | lookup a struct block_device by name |

## Char devices

| 名称 | 描述 |
|---|---|
| register_chrdev_region | register a range of device numbers |
| alloc_chrdev_region | register a range of char device numbers |
| __register_chrdev | create and register a cdev occupying a range of minors |
| unregister_chrdev_region | unregister a range of device numbers |
| __unregister_chrdev | unregister and destroy a cdev |
| cdev_add | add a char device to the system |
| cdev_set_parent | set the parent kobject for a char device |
| cdev_device_add | add a char device and it’s correspondingstructdevice, linkink |
| cdev_device_del | inverse of cdev_device_add |
| cdev_del | remove a cdev from the system |
| cdev_alloc | allocate a cdev structure |
| cdev_init | initialize a cdev structure |

## Clock Framework

| 名称 | 描述 |
|---|---|
| struct clk_notifier | associate a clk with a notifier |
| struct clk_notifier_data | rate data to pass to the notifier callback |
| struct clk_bulk_data | Data used for bulk clk operations. |
| clk_notifier_register | register a clock rate-change notifier callback |
| clk_notifier_unregister | unregister a clock rate-change notifier callback |
| devm_clk_notifier_register | register a managed rate-change notifier callback |
| clk_get_accuracy | obtain the clock accuracy in ppb (parts per billion) for a clock source. |
| clk_set_phase | adjust the phase shift of a clock signal |
| clk_get_phase | return the phase shift of a clock signal |
| clk_set_duty_cycle | adjust the duty cycle ratio of a clock signal |
| clk_get_scaled_duty_cycle | return the duty cycle ratio of a clock signal |
| clk_is_match | check if two clk’s point to the same hardware clock |
| clk_prepare | prepare a clock source |
| clk_is_enabled_when_prepared | indicate if preparing a clock also enables it. |
| clk_unprepare | undo preparation of a clock source |
| clk_get | lookup and obtain a reference to a clock producer. |
| clk_bulk_get | lookup and obtain a number of references to clock producer. |
| clk_bulk_get_all | lookup and obtain all available references to clock producer. |
| clk_bulk_get_optional | lookup and obtain a number of references to clock producer |
| devm_clk_bulk_get | managed get multiple clk consumers |
| devm_clk_bulk_get_optional | managed get multiple optional consumer clocks |
| devm_clk_bulk_get_all | managed get multiple clk consumers |
| devm_clk_get | lookup and obtain a managed reference to a clock producer. |
| devm_clk_get_optional | lookup and obtain a managed reference to an optional clock producer. |
| devm_get_clk_from_child | lookup and obtain a managed reference to a clock producer from child node. |
| clk_rate_exclusive_get | get exclusivity over the rate control of a producer |
| clk_rate_exclusive_put | release exclusivity over the rate control of a producer |
| clk_enable | inform the system when the clock source should be running. |
| clk_bulk_enable | inform the system when the set of clks should be running. |
| clk_disable | inform the system when the clock source is no longer required. |
| clk_bulk_disable | inform the system when the set of clks is no longer required. |
| clk_get_rate | obtain the current clock rate (in Hz) for a clock source. This is only valid once the clock source has been enabled. |
| clk_put | “free” the clock source |
| clk_bulk_put | “free” the clock source |
| clk_bulk_put_all | “free” all the clock source |
| devm_clk_put | “free” a managed clock source |
| clk_round_rate | adjust a rate to the exact rate a clock can provide |
| clk_set_rate | set the clock rate for a clock source |
| clk_set_rate_exclusive | set the clock rate and claim exclusivity over clock source |
| clk_has_parent | check if a clock is a possible parent for another |
| clk_set_rate_range | set a rate range for a clock source |
| clk_set_min_rate | set a minimum clock rate for a clock source |
| clk_set_max_rate | set a maximum clock rate for a clock source |
| clk_set_parent | set the parent clock source for this clock |
| clk_get_parent | get the parent clock source for this clock |
| clk_get_sys | get a clock based upon the device name |
| clk_save_context | save clock context for poweroff |
| clk_restore_context | restore clock context after poweroff |
| clk_get_optional | lookup and obtain a reference to an optional clock producer. |

## Synchronization Primitives

| 名称 | 描述 |
|---|---|
| RCU_NONIDLE | Indicate idle-loop code that needs RCU readers |
| cond_resched_tasks_rcu_qs | Report potential quiescent states to RCU |
| RCU_LOCKDEP_WARN | emit lockdep splat if specified condition is met |
| unrcu_pointer | mark a pointer as not being RCU protected |
| RCU_INITIALIZER | statically initialize an RCU-protected global variable |
| rcu_assign_pointer | assign to RCU-protected pointer |
| rcu_replace_pointer | replace an RCU pointer, returning its old value |
| rcu_access_pointer | fetch RCU pointer with no dereferencing |
| rcu_dereference_check | rcu_dereference with debug checking |
| rcu_dereference_bh_check | rcu_dereference_bh with debug checking |
| rcu_dereference_sched_check | rcu_dereference_sched with debug checking |
| rcu_dereference_protected | fetch RCU pointer when updates prevented |
| rcu_dereference | fetch RCU-protected pointer for dereferencing |
| rcu_dereference_bh | fetch an RCU-bh-protected pointer for dereferencing |
| rcu_dereference_sched | fetch RCU-sched-protected pointer for dereferencing |
| rcu_pointer_handoff | Hand off a pointer from RCU to other mechanism |
| rcu_read_lock | mark the beginning of an RCU read-side critical section |
| rcu_read_unlock | marks the end of an RCU read-side critical section. |
| rcu_read_lock_bh | mark the beginning of an RCU-bh critical section |
| rcu_read_unlock_bh | marks the end of a softirq-only RCU critical section |
| rcu_read_lock_sched | mark the beginning of a RCU-sched critical section |
| rcu_read_unlock_sched | marks the end of a RCU-classic critical section |
| RCU_INIT_POINTER | initialize an RCU protected pointer |
| RCU_POINTER_INITIALIZER | statically initialize an RCU protected pointer |
| kfree_rcu | kfree an object after a grace period. |
| kvfree_rcu | kvfree an object after a grace period. |
| rcu_head_init | Initialize rcu_head forrcu_head_after_call_rcu() |
| rcu_head_after_call_rcu | Has this rcu_head been passed tocall_rcu()? |
| rcu_is_cpu_rrupt_from_idle | see if ‘interrupted’ from idle |
| rcu_idle_enter | inform RCU that current CPU is entering idle |
| rcu_user_enter | inform RCU that we are resuming userspace. |
| rcu_nmi_exit | inform RCU of exit from NMI context |
| rcu_irq_exit | inform RCU that current CPU is exiting irq towards idle |
| rcu_irq_exit_check_preempt | Validate that scheduling is possible |
| rcu_idle_exit | inform RCU that current CPU is leaving idle |
| rcu_user_exit | inform RCU that we are exiting userspace. |
| __rcu_irq_enter_check_tick | Enable scheduler tick on CPU if RCU needs it. |
| rcu_nmi_enter | inform RCU of entry to NMI context |
| rcu_irq_enter | inform RCU that current CPU is entering irq away from idle |
| rcu_is_watching | see if RCU thinks that the current CPU is not idle |
| call_rcu | Queue an RCU callback for invocation after a grace period. |
| struct kvfree_rcu_bulk_data | single block to storekvfree_rcu()pointers |
| struct kfree_rcu_cpu_work | single batch ofkfree_rcu()requests |
| struct kfree_rcu_cpu | batch upkfree_rcu()requests for RCU grace period |
| synchronize_rcu | wait until a grace period has elapsed. |
| get_state_synchronize_rcu | Snapshot current RCU state |
| start_poll_synchronize_rcu | Snapshot and start RCU grace period |
| poll_state_synchronize_rcu | Conditionally wait for an RCU grace period |
| cond_synchronize_rcu | Conditionally wait for an RCU grace period |
| rcu_barrier | Wait until all in-flightcall_rcu()callbacks complete. |
| synchronize_rcu_expedited | Brute-force RCU grace period |
| rcu_read_lock_held_common | might we be in RCU-sched read-side critical section? |
| rcu_expedite_gp | Expedite future RCU grace periods |
| rcu_unexpedite_gp | Cancel priorrcu_expedite_gp()invocation |
| rcu_read_lock_held | might we be in RCU read-side critical section? |
| rcu_read_lock_bh_held | might we be in RCU-bh read-side critical section? |
| wakeme_after_rcu | Callback function to awaken a task after grace period |
| init_rcu_head_on_stack | initialize on-stack rcu_head for debugobjects |
| destroy_rcu_head_on_stack | destroy on-stack rcu_head for debugobjects |
| srcu_read_lock_held | might we be in SRCU read-side critical section? |
| srcu_dereference_check | fetch SRCU-protected pointer for later dereferencing |
| srcu_dereference | fetch SRCU-protected pointer for later dereferencing |
| srcu_dereference_notrace | no tracing and no lockdep calls from here |
| srcu_read_lock | register a new reader for an SRCU-protected structure. |
| srcu_read_unlock | unregister a old reader from an SRCU-protected structure. |
| smp_mb__after_srcu_read_unlock | ensure full ordering after srcu_read_unlock |
| init_srcu_struct | initialize a sleep-RCU structure |
| srcu_readers_active | returns true if there are readers. and false otherwise |
| cleanup_srcu_struct | deconstruct a sleep-RCU structure |
| call_srcu | Queue a callback for invocation after an SRCU grace period |
| synchronize_srcu_expedited | Brute-force SRCU grace period |
| synchronize_srcu | wait for prior SRCU read-side critical-section completion |
| get_state_synchronize_srcu | Provide an end-of-grace-period cookie |
| start_poll_synchronize_srcu | Provide cookie and start grace period |
| poll_state_synchronize_srcu | Has cookie’s grace period ended? |
| srcu_barrier | Wait until all in-flightcall_srcu()callbacks complete. |
| srcu_batches_completed | return batches completed. |
| hlist_bl_del_rcu | deletes entry from hash list without re-initialization |
| hlist_bl_add_head_rcu |  |
| hlist_bl_for_each_entry_rcu | iterate over rcu list of given type |
| list_tail_rcu | returns the prev pointer of the head of the list |
| list_add_rcu | add a new entry to rcu-protected list |
| list_add_tail_rcu | add a new entry to rcu-protected list |
| list_del_rcu | deletes entry from list without re-initialization |
| hlist_del_init_rcu | deletes entry from hash list with re-initialization |
| list_replace_rcu | replace old entry by new one |
| __list_splice_init_rcu | join an RCU-protected list into an existing list. |
| list_splice_init_rcu | splice an RCU-protected list into an existing list, designed for stacks. |
| list_splice_tail_init_rcu | splice an RCU-protected list into an existing list, designed for queues. |
| list_entry_rcu | get the struct for this entry |
| list_first_or_null_rcu | get the first element from a list |
| list_next_or_null_rcu | get the first element from a list |
| list_for_each_entry_rcu | iterate over rcu list of given type |
| list_for_each_entry_srcu | iterate over rcu list of given type |
| list_entry_lockless | get the struct for this entry |
| list_for_each_entry_lockless | iterate over rcu list of given type |
| list_for_each_entry_continue_rcu | continue iteration over list of given type |
| list_for_each_entry_from_rcu | iterate over a list from current point |
| hlist_del_rcu | deletes entry from hash list without re-initialization |
| hlist_replace_rcu | replace old entry by new one |
| hlists_swap_heads_rcu | swap the lists the hlist heads point to |
| hlist_add_head_rcu |  |
| hlist_add_tail_rcu |  |
| hlist_add_before_rcu |  |
| hlist_add_behind_rcu |  |
| hlist_for_each_entry_rcu | iterate over rcu list of given type |
| hlist_for_each_entry_srcu | iterate over rcu list of given type |
| hlist_for_each_entry_rcu_notrace | iterate over rcu list of given type (for tracing) |
| hlist_for_each_entry_rcu_bh | iterate over rcu list of given type |
| hlist_for_each_entry_continue_rcu | iterate over a hlist continuing after current point |
| hlist_for_each_entry_continue_rcu_bh | iterate over a hlist continuing after current point |
| hlist_for_each_entry_from_rcu | iterate over a hlist continuing from current point |
| hlist_nulls_del_init_rcu | deletes entry from hash list with re-initialization |
| hlist_nulls_first_rcu | returns the first element of the hash list. |
| hlist_nulls_next_rcu | returns the element of the list afternode. |
| hlist_nulls_del_rcu | deletes entry from hash list without re-initialization |
| hlist_nulls_add_head_rcu |  |
| hlist_nulls_add_tail_rcu |  |
| hlist_nulls_for_each_entry_rcu | iterate over rcu list of given type |
| hlist_nulls_for_each_entry_safe | iterate over list of given type safe against removal of list entry |
| rcu_sync_is_idle | Are readers permitted to use their fastpaths? |
| rcu_sync_init | Initialize an rcu_sync structure |
| rcu_sync_enter_start | Force readers onto slow path for multiple updates |
| rcu_sync_func | Callback function managing reader access to fastpath |
| rcu_sync_enter | Force readers onto slowpath |
| rcu_sync_exit | Allow readers back onto fast path after grace period |
| rcu_sync_dtor | Clean up an rcu_sync structure |

## 总数统计

总共提取了 683 个函数+结构。

# Linux Kernel API Documentation

## Function List

- **INIT_LIST_HEAD**: Initialize a list_head structure
- **list_add**: add a new entry
- **list_add_tail**: add a new entry
- **list_del**: deletes entry from list.
- **list_replace**: replace old entry by new one
- **list_replace_init**: replace old entry by new one and initialize the old one
- **list_swap**: replace entry1 with entry2 and re-add entry1 at entry2’s position
- **list_del_init**: deletes entry from list and reinitialize it.
- **list_move**: delete from one list and add as another’s head
- **list_move_tail**: delete from one list and add as another’s tail
- **list_bulk_move_tail**: move a subsection of a list to its tail
- **list_is_first**: tests whetherlistis the first entry in listhead
- **list_is_last**: tests whetherlistis the last entry in listhead
- **list_empty**: tests whether a list is empty
- **list_del_init_careful**: deletes entry from list and reinitialize it.
- **list_empty_careful**: tests whether a list is empty and not being modified
- **list_rotate_left**: rotate the list to the left
- **list_rotate_to_front**: Rotate list to specific item.
- **list_is_singular**: tests whether a list has just one entry.
- **list_cut_position**: cut a list into two
- **list_cut_before**: cut a list into two, before given entry
- **list_splice**: join two lists, this is designed for stacks
- **list_splice_tail**: join two lists, each list being a queue
- **list_splice_init**: join two lists and reinitialise the emptied list.
- **list_splice_tail_init**: join two lists and reinitialise the emptied list
- **list_entry**: get the struct for this entry
- **list_first_entry**: get the first element from a list
- **list_last_entry**: get the last element from a list
- **list_first_entry_or_null**: get the first element from a list
- **list_next_entry**: get the next element in list
- **list_prev_entry**: get the prev element in list
- **list_for_each**: iterate over a list
- **list_for_each_continue**: continue iteration over a list
- **list_for_each_prev**: iterate over a list backwards
- **list_for_each_safe**: iterate over a list safe against removal of list entry
- **list_for_each_prev_safe**: iterate over a list backwards safe against removal of list entry
- **list_entry_is_head**: test if the entry points to the head of the list
- **list_for_each_entry**: iterate over list of given type
- **list_for_each_entry_reverse**: iterate backwards over list of given type.
- **list_prepare_entry**: prepare a pos entry for use inlist_for_each_entry_continue()
- **list_for_each_entry_continue**: continue iteration over list of given type
- **list_for_each_entry_continue_reverse**: iterate backwards from the given point
- **list_for_each_entry_from**: iterate over list of given type from the current point
- **list_for_each_entry_from_reverse**: iterate backwards over list of given type from the current point
- **list_for_each_entry_safe**: iterate over list of given type safe against removal of list entry
- **list_for_each_entry_safe_continue**: continue list iteration safe against removal
- **list_for_each_entry_safe_from**: iterate over list from current point safe against removal
- **list_for_each_entry_safe_reverse**: iterate backwards over list safe against removal
- **list_safe_reset_next**: reset a stale list_for_each_entry_safe loop
- **hlist_unhashed**: Has node been removed from list and reinitialized?
- **hlist_unhashed_lockless**: Version of hlist_unhashed for lockless use
- **hlist_empty**: Is the specified hlist_head structure an empty hlist?
- **hlist_del**: Delete the specified hlist_node from its list
- **hlist_del_init**: Delete the specified hlist_node from its list and initialize
- **hlist_add_head**: add a new entry at the beginning of the hlist
- **hlist_add_before**: add a new entry before the one specified
- **hlist_add_behind**: add a new entry after the one specified
- **hlist_add_fake**: create a fake hlist consisting of a single headless node
- **hlist_fake**: Is this node a fake hlist?
- **hlist_is_singular_node**: is node the only element of the specified hlist?
- **hlist_move_list**: Move an hlist
- **hlist_for_each_entry**: iterate over list of given type
- **hlist_for_each_entry_continue**: iterate over a hlist continuing after current point
- **hlist_for_each_entry_from**: iterate over a hlist continuing from current point
- **hlist_for_each_entry_safe**: iterate over list of given type safe against removal of list entry
- **simple_strtoull**: convert a string to an unsigned long long
- **simple_strtoul**: convert a string to an unsigned long
- **simple_strtol**: convert a string to a signed long
- **simple_strtoll**: convert a string to a signed long long
- **vsnprintf**: Format a string and place it in a buffer
- **vscnprintf**: Format a string and place it in a buffer
- **snprintf**: Format a string and place it in a buffer
- **scnprintf**: Format a string and place it in a buffer
- **vsprintf**: Format a string and place it in a buffer
- **sprintf**: Format a string and place it in a buffer
- **vbin_printf**: Parse a format string and place args’ binary value in a buffer
- **bstr_printf**: Format a string from binary arguments and place it in a buffer
- **bprintf**: Parse a format string and place args’ binary value in a buffer
- **vsscanf**: Unformat a buffer into a list of arguments
- **sscanf**: Unformat a buffer into a list of arguments
- **kstrtoul**: convert a string to an unsigned long
- **kstrtol**: convert a string to a long
- **kstrtoull**: convert a string to an unsigned long long
- **kstrtoll**: convert a string to a long long
- **kstrtouint**: convert a string to an unsigned int
- **kstrtoint**: convert a string to an int
- **kstrtobool**: convert common user inputs into boolean values
- **string_get_size**: get the size in the specified units
- **string_unescape**: unquote characters in the given string
- **string_escape_mem**: quote characters in the given memory buffer
- **kfree_strarray**: free a number of dynamically allocated strings contained in an array and the array itself
- **strncasecmp**: Case insensitive, length-limited string comparison
- **strcpy**: Copy aNULterminated string
- **strncpy**: Copy a length-limited, C-string
- **strlcpy**: Copy a C-string into a sized buffer
- **strscpy**: Copy a C-string into a sized buffer
- **strscpy_pad**: Copy a C-string into a sized buffer
- **stpcpy**: copy a string from src to dest returning a pointer to the new end of dest, including src’sNUL-terminator. May overrun dest.
- **strcat**: Append oneNUL-terminatedstring to another
- **strncat**: Append a length-limited, C-string to another
- **strlcat**: Append a length-limited, C-string to another
- **strcmp**: Compare two strings
- **strncmp**: Compare two length-limited strings
- **strchr**: Find the first occurrence of a character in a string
- **strchrnul**: Find and return a character in a string, or end of string
- **strrchr**: Find the last occurrence of a character in a string
- **strnchr**: Find a character in a length limited string
- **skip_spaces**: Removes leading whitespace fromstr.
- **strim**: Removes leading and trailing whitespace froms.
- **strlen**: Find the length of a string
- **strnlen**: Find the length of a length-limited string
- **strspn**: Calculate the length of the initial substring ofswhich only contain letters inaccept
- **strcspn**: Calculate the length of the initial substring ofswhich does not contain letters inreject
- **strpbrk**: Find the first occurrence of a set of characters
- **strsep**: Split a string into tokens
- **sysfs_streq**: return true if strings are equal, modulo trailing newline
- **match_string**: matches given string in an array
- **__sysfs_match_string**: matches given string in an array
- **memset**: Fill a region of memory with the given value
- **memset16**: Fill a memory area with a uint16_t
- **memset32**: Fill a memory area with a uint32_t
- **memset64**: Fill a memory area with a uint64_t
- **memcpy**: Copy one area of memory to another
- **memmove**: Copy one area of memory to another
- **memcmp**: Compare two areas of memory
- **bcmp**: returns 0 if and only if the buffers have identical contents.
- **memscan**: Find a character in an area of memory.
- **strstr**: Find the first substring in aNULterminated string
- **strnstr**: Find the first substring in a length-limited string
- **memchr**: Find a character in an area of memory.
- **memchr_inv**: Find an unmatching character in an area of memory.
- **strreplace**: Replace all occurrences of character in string.
- **sysfs_match_string**: matches given string in an array
- **strstarts**: doesstrstart withprefix?
- **memzero_explicit**: Fill a region of memory (e.g. sensitive keying data) with 0s.
- **kbasename**: return the last part of a pathname.
- **memcpy_and_pad**: Copy one buffer to another with padding
- **str_has_prefix**: Test if a string has a given prefix
- **kstrdup**: allocate space for and copy an existing string
- **kstrdup_const**: conditionally duplicate an existing const string
- **kstrndup**: allocate space for and copy an existing string
- **kmemdup**: duplicate region of memory
- **kmemdup_nul**: Create a NUL-terminated string from unterminated data
- **memdup_user**: duplicate memory region from user space
- **vmemdup_user**: duplicate memory region from user space
- **strndup_user**: duplicate an existing string from user space
- **memdup_user_nul**: duplicate memory region from user space and NUL-terminate
- **set_bit**: Atomically set a bit in memory
- **clear_bit**: Clears a bit in memory
- **change_bit**: Toggle a bit in memory
- **test_and_set_bit**: Set a bit and return its old value
- **test_and_clear_bit**: Clear a bit and return its old value
- **test_and_change_bit**: Change a bit and return its old value
- **__set_bit**: Set a bit in memory
- **__clear_bit**: Clears a bit in memory
- **__change_bit**: Toggle a bit in memory
- **__test_and_set_bit**: Set a bit and return its old value
- **__test_and_clear_bit**: Clear a bit and return its old value
- **__test_and_change_bit**: Change a bit and return its old value
- **test_bit**: Determine whether a bit is set
- **clear_bit_unlock**: Clear a bit in memory, for unlock
- **__clear_bit_unlock**: Clears a bit in memory
- **test_and_set_bit_lock**: Set a bit and return its old value, for lock
- **clear_bit_unlock_is_negative_byte**: Clear a bit in memory and test if bottom byte is negative, for unlock.
- **__bitmap_shift_right**: logical right shift of the bits in a bitmap
- **__bitmap_shift_left**: logical left shift of the bits in a bitmap
- **bitmap_cut**: remove bit region from bitmap and right shift remaining bits
- **bitmap_find_next_zero_area_off**: find a contiguous aligned zero area
- **bitmap_parse_user**: convert an ASCII hex string in a user buffer into a bitmap
- **bitmap_print_to_pagebuf**: convert bitmap to list or hex format ASCII string
- **bitmap_print_bitmask_to_buf**: convert bitmap to hex bitmask format ASCII string
- **bitmap_print_list_to_buf**: convert bitmap to decimal list format ASCII string
- **bitmap_parselist**: convert list format ASCII string to bitmap
- **bitmap_parselist_user**: 
- **bitmap_parse**: convert an ASCII hex string into a bitmap.
- **bitmap_remap**: Apply map defined by a pair of bitmaps to another bitmap
- **bitmap_bitremap**: Apply map defined by a pair of bitmaps to a single bit
- **bitmap_find_free_region**: find a contiguous aligned mem region
- **bitmap_release_region**: release allocated bitmap region
- **bitmap_allocate_region**: allocate bitmap region
- **bitmap_copy_le**: copy a bitmap, putting the bits into little-endian order.
- **bitmap_from_arr32**: copy the contents of u32 array of bits to bitmap
- **bitmap_to_arr32**: copy the contents of bitmap to a u32 array of bits
- **bitmap_print_to_buf**: convert bitmap to list or hex format ASCII string
- **bitmap_pos_to_ord**: find ordinal of set bit at given position in bitmap
- **bitmap_ord_to_pos**: find position of n-th set bit in bitmap
- **bitmap_onto**: translate one bitmap relative to another
- **bitmap_fold**: fold larger bitmap into smaller, modulo specified size
- **bitmap_find_next_zero_area**: find a contiguous aligned zero area
- **bitmap_or_equal**: Check whether the or of two bitmaps is equal to a third
- **BITMAP_FROM_U64**: Represent u64 value in the format suitable for bitmap.
- **bitmap_from_u64**: Check and swap words within u64.
- **bitmap_get_value8**: get an 8-bit value within a memory region
- **bitmap_set_value8**: set an 8-bit value within a memory region
- **get_option**: Parse integer from an option string
- **get_options**: Parse a string into a list of integers
- **memparse**: parse a string with mem suffixes into a number
- **sort_r**: sort an array of elements
- **list_sort**: sort a list
- **textsearch_register**: register a textsearch module
- **textsearch_unregister**: unregister a textsearch module
- **textsearch_find_continuous**: search a pattern in continuous/linear data
- **textsearch_prepare**: Prepare a search
- **textsearch_destroy**: destroy a search configuration
- **textsearch_next**: continue searching for a pattern
- **textsearch_find**: start searching for a pattern
- **textsearch_get_pattern**: return head of the pattern
- **textsearch_get_pattern_len**: return length of the pattern
- **crc4**: calculate the 4-bit crc of a value.
- **crc7_be**: update the CRC7 for the data buffer
- **crc8_populate_msb**: fill crc table for given polynomial in reverse bit order.
- **crc8_populate_lsb**: fill crc table for given polynomial in regular bit order.
- **crc8**: calculate a crc8 over the given input data.
- **crc16**: compute the CRC-16 for the data buffer
- **crc32_le_generic**: Calculate bitwise little-endian Ethernet AUTODIN II CRC32/CRC32C
- **crc32_generic_shift**: Appendlen0 bytes to crc, in logarithmic time
- **crc32_be_generic**: Calculate bitwise big-endian Ethernet AUTODIN II CRC32
- **crc_ccitt**: recompute the CRC (CRC-CCITT variant) for the data buffer
- **crc_ccitt_false**: recompute the CRC (CRC-CCITT-FALSE variant) for the data buffer
- **crc_itu_t**: Compute the CRC-ITU-T for the data buffer
- **is_power_of_2**: check if a value is a power of two
- **__roundup_pow_of_two**: round up to nearest power of two
- **__rounddown_pow_of_two**: round down to nearest power of two
- **const_ilog2**: log base 2 of 32-bit or a 64-bit constant unsigned value
- **ilog2**: log base 2 of 32-bit or a 64-bit unsigned value
- **roundup_pow_of_two**: round the given value up to nearest power of two
- **rounddown_pow_of_two**: round the given value down to nearest power of two
- **order_base_2**: calculate the (rounded up) base 2 order of the argument
- **bits_per**: calculate the number of bits required for the argument
- **int_pow**: computes the exponentiation of the given base and exponent
- **int_sqrt**: computes the integer square root
- **int_sqrt64**: strongly typed int_sqrt function when minimum 64 bit input is expected.
- **do_div**: returns 2 values: calculate remainder and update new dividend
- **div_u64_rem**: unsigned 64bit divide with 32bit divisor with remainder
- **div_u64**: unsigned 64bit divide with 32bit divisor
- **div_s64**: signed 64bit divide with 32bit divisor
- **DIV64_U64_ROUND_CLOSEST**: unsigned 64bit divide with 64bit divisor rounded to nearest integer
- **div_s64_rem**: signed 64bit divide with 64bit divisor and remainder
- **div64_u64_rem**: unsigned 64bit divide with 64bit divisor and remainder
- **div64_u64**: unsigned 64bit divide with 64bit divisor
- **div64_s64**: signed 64bit divide with 64bit divisor
- **gcd**: calculate and return the greatest common divisor of 2 unsigned longs
- **generate_random_uuid**: generate a random UUID
- **uuid_is_valid**: checks if a UUID string is valid
- **ipc_init**: initialise ipc subsystem
- **ipc_init_ids**: initialise ipc identifiers
- **ipc_init_proc_interface**: create a proc interface for sysipc types using a seq_file interface.
- **ipc_findkey**: find a key in an ipc identifier set
- **ipc_addid**: add an ipc identifier
- **ipcget_new**: create a new ipc object
- **ipc_check_perms**: check security and permissions for an ipc object
- **ipcget_public**: get an ipc object or create a new one
- **ipc_kht_remove**: remove an ipc from the key hashtable
- **ipc_search_maxidx**: search for the highest assigned index
- **ipc_rmid**: remove an ipc identifier
- **ipc_set_key_private**: switch the key of an existing ipc to IPC_PRIVATE
- **ipcperms**: check ipc permissions
- **kernel_to_ipc64_perm**: convert kernel ipc permissions to user
- **ipc64_perm_to_ipc_perm**: convert new ipc permissions to old
- **ipc_obtain_object_idr**: 
- **ipc_obtain_object_check**: 
- **ipcget**: Common sys_*get() code
- **ipc_update_perm**: update the permissions of an ipc object
- **ipcctl_obtain_check**: retrieve an ipc object and check permissions
- **ipc_parse_version**: ipc call version
- **DECLARE_KFIFO_PTR**: macro to declare a fifo pointer object
- **DECLARE_KFIFO**: macro to declare a fifo object
- **INIT_KFIFO**: Initialize a fifo declared by DECLARE_KFIFO
- **DEFINE_KFIFO**: macro to define and initialize a fifo
- **kfifo_initialized**: Check if the fifo is initialized
- **kfifo_esize**: returns the size of the element managed by the fifo
- **kfifo_recsize**: returns the size of the record length field
- **kfifo_size**: returns the size of the fifo in elements
- **kfifo_reset**: removes the entire fifo content
- **kfifo_reset_out**: skip fifo content
- **kfifo_len**: returns the number of used elements in the fifo
- **kfifo_is_empty**: returns true if the fifo is empty
- **kfifo_is_empty_spinlocked**: returns true if the fifo is empty using a spinlock for locking
- **kfifo_is_empty_spinlocked_noirqsave**: returns true if the fifo is empty using a spinlock for locking, doesn’t disable interrupts
- **kfifo_is_full**: returns true if the fifo is full
- **kfifo_avail**: returns the number of unused elements in the fifo
- **kfifo_skip**: skip output data
- **kfifo_peek_len**: gets the size of the next fifo record
- **kfifo_alloc**: dynamically allocates a new fifo buffer
- **kfifo_free**: frees the fifo
- **kfifo_init**: initialize a fifo using a preallocated buffer
- **kfifo_put**: put data into the fifo
- **kfifo_get**: get data from the fifo
- **kfifo_peek**: get data from the fifo without removing
- **kfifo_in**: put data into the fifo
- **kfifo_in_spinlocked**: put data into the fifo using a spinlock for locking
- **kfifo_in_spinlocked_noirqsave**: put data into fifo using a spinlock for locking, don’t disable interrupts
- **kfifo_out**: get data from the fifo
- **kfifo_out_spinlocked**: get data from the fifo using a spinlock for locking
- **kfifo_out_spinlocked_noirqsave**: get data from the fifo using a spinlock for locking, don’t disable interrupts
- **kfifo_from_user**: puts some data from user space into the fifo
- **kfifo_to_user**: copies data from the fifo into user space
- **kfifo_dma_in_prepare**: setup a scatterlist for DMA input
- **kfifo_dma_in_finish**: finish a DMA IN operation
- **kfifo_dma_out_prepare**: setup a scatterlist for DMA output
- **kfifo_dma_out_finish**: finish a DMA OUT operation
- **kfifo_out_peek**: gets some data from the fifo
- **relay_buf_full**: boolean, is the channel buffer full?
- **relay_reset**: reset the channel
- **relay_open**: create a new relay channel
- **relay_late_setup_files**: triggers file creation
- **relay_switch_subbuf**: switch to a new sub-buffer
- **relay_subbufs_consumed**: update the buffer’s sub-buffers-consumed count
- **relay_close**: close the channel
- **relay_flush**: close the channel
- **relay_mmap_buf**: mmap channel buffer to process address space
- **relay_alloc_buf**: allocate a channel buffer
- **relay_create_buf**: allocate and initialize a channel buffer
- **relay_destroy_channel**: free the channel struct
- **relay_destroy_buf**: destroy an rchan_buf struct and associated buffer
- **relay_remove_buf**: remove a channel buffer
- **relay_buf_empty**: boolean, is the channel buffer empty?
- **wakeup_readers**: wake up readers waiting on a channel
- **__relay_reset**: reset a channel buffer
- **relay_close_buf**: close a channel buffer
- **relay_file_open**: open file op for relay files
- **relay_file_mmap**: mmap file op for relay files
- **relay_file_poll**: poll file op for relay files
- **relay_file_release**: release file op for relay files
- **relay_file_read_subbuf_avail**: return bytes available in sub-buffer
- **relay_file_read_start_pos**: find the first available byte to read
- **relay_file_read_end_pos**: return the new read position
- **__request_module**: try to load a kernel module
- **request_dma**: request and reserve a system DMA channel
- **free_dma**: free a reserved system DMA channel
- **request_resource_conflict**: request and reserve an I/O or memory resource
- **find_next_iomem_res**: Finds the lowest iomem resource that covers part of [start..**end**].
- **reallocate_resource**: allocate a slot in the resource tree given range & alignment. The resource will be relocated if the new size cannot be reallocated in the current location.
- **lookup_resource**: find an existing resource by a resource start address
- **insert_resource_conflict**: Inserts resource in the resource tree
- **insert_resource_expand_to_fit**: Insert a resource into the resource tree
- **resource_alignment**: calculate resource’s alignment
- **release_mem_region_adjustable**: release a previously reserved memory region
- **merge_system_ram_resource**: mark the System RAM resource mergeable and try to merge it with adjacent, mergeable resources
- **request_resource**: request and reserve an I/O or memory resource
- **release_resource**: release a previously reserved resource
- **walk_iomem_res_desc**: Walks through iomem resources and calls func() with matching resource ranges. *
- **region_intersects**: determine intersection of region with known resources
- **allocate_resource**: allocate empty slot in the resource tree given range & alignment. The resource will be reallocated with a new size if it was already allocated
- **insert_resource**: Inserts a resource in the resource tree
- **remove_resource**: Remove a resource in the resource tree
- **adjust_resource**: modify a resource’s start and size
- **__request_region**: create a new busy resource region
- **__release_region**: release a previously reserved resource region
- **devm_request_resource**: request and reserve an I/O or memory resource
- **devm_release_resource**: release a previously requested resource
- **devm_request_free_mem_region**: find free region for device private memory
- **arch_phys_wc_add**: add a WC MTRR and handle errors if PAT is unavailable
- **security_init**: initializes the security framework
- **security_add_hooks**: Add a modules hooks to the hook lists.
- **lsm_cred_alloc**: allocate a composite cred blob
- **lsm_early_cred**: during initialization allocate a composite cred blob
- **lsm_file_alloc**: allocate a composite file blob
- **lsm_inode_alloc**: allocate a composite inode blob
- **lsm_task_alloc**: allocate a composite task blob
- **lsm_ipc_alloc**: allocate a composite ipc blob
- **lsm_msg_msg_alloc**: allocate a composite msg_msg blob
- **lsm_early_task**: during initialization allocate a composite task blob
- **lsm_superblock_alloc**: allocate a composite superblock blob
- **securityfs_create_file**: create a file in the securityfs filesystem
- **securityfs_create_dir**: create a directory in the securityfs filesystem
- **securityfs_create_symlink**: create a symlink in the securityfs filesystem
- **securityfs_remove**: removes a file or directory from the securityfs filesystem
- **audit_log_start**: obtain an audit buffer
- **audit_log_format**: format a message into the audit buffer.
- **audit_log_end**: end one audit record
- **audit_log**: Log an audit record
- **audit_alloc**: allocate an audit context block for a task
- **__audit_free**: free a per-task audit context
- **__audit_syscall_entry**: fill in an audit record at syscall entry
- **__audit_syscall_exit**: deallocate audit context after a system call
- **__audit_reusename**: fill out filename with info from existing entry
- **__audit_getname**: add a name to the list
- **__audit_inode**: store the inode and device from a lookup
- **auditsc_get_stamp**: get local copies of audit_context values
- **__audit_mq_open**: record audit data for a POSIX MQ open
- **__audit_mq_sendrecv**: record audit data for a POSIX MQ timed send/receive
- **__audit_mq_notify**: record audit data for a POSIX MQ notify
- **__audit_mq_getsetattr**: record audit data for a POSIX MQ get/set attribute
- **__audit_ipc_obj**: record audit data for ipc object
- **__audit_ipc_set_perm**: record audit data for new ipc permissions
- **__audit_socketcall**: record audit data for sys_socketcall
- **__audit_fd_pair**: record audit data for pipe and socketpair
- **__audit_sockaddr**: record audit data for sys_bind, sys_connect, sys_sendto
- **audit_signal_info_syscall**: record signal info for syscalls
- **__audit_log_bprm_fcaps**: store information about a loading bprm and relevant fcaps
- **__audit_log_capset**: store information about the arguments to the capset syscall
- **audit_core_dumps**: record information about processes that end abnormally
- **audit_seccomp**: record information about a seccomp action
- **audit_rule_change**: apply all rules to the specified message type
- **audit_list_rules_send**: list the audit rules
- **parent_len**: find the length of the parent portion of a pathname
- **audit_compare_dname_path**: compare given dentry name with last component in given path. Return of 0 indicates a match.
- **sys_acct**: enable/disable process accounting
- **acct_collect**: collect accounting information into pacct_struct
- **acct_process**: handles process accounting for an exiting task
- **blk_queue_flag_set**: atomically set a queue flag
- **blk_queue_flag_clear**: atomically clear a queue flag
- **blk_queue_flag_test_and_set**: atomically test and set a queue flag
- **blk_op_str**: Return string XXX in the REQ_OP_XXX.
- **blk_sync_queue**: cancel any pending callbacks on a queue
- **blk_set_pm_only**: increment pm_only counter
- **blk_put_queue**: decrement the request_queue refcount
- **blk_cleanup_queue**: shutdown a request queue
- **blk_get_queue**: increment the request_queue refcount
- **blk_get_request**: allocate a request
- **submit_bio_noacct**: re-submit a bio to the block device layer for I/O
- **submit_bio**: submit a bio to the block device layer for I/O
- **blk_insert_cloned_request**: Helper for stacking drivers to submit a request
- **blk_rq_err_bytes**: determine number of bytes till the next failure boundary
- **bio_start_io_acct**: start I/O accounting for bio based drivers
- **blk_update_request**: Complete multiple bytes without completing the request
- **rq_flush_dcache_pages**: Helper function to flush all pages in a request
- **blk_lld_busy**: Check if underlying low-level drivers of a device are busy
- **blk_rq_unprep_clone**: Helper function to free all bios in a cloned request
- **blk_rq_prep_clone**: Helper function to setup clone request
- **blk_start_plug**: initialize blk_plug and track it inside the task_struct
- **blk_finish_plug**: mark the end of a batch of submitted I/O
- **blk_queue_enter**: try to increase q->q_usage_counter
- **blk_cloned_rq_check_limits**: Helper function to check a cloned request for the new queue limits
- **blk_rq_map_user_iov**: map user data to a request, for passthrough requests
- **blk_rq_unmap_user**: unmap a request with user data
- **blk_rq_map_kern**: map kernel data to a request, for passthrough requests
- **blk_release_queue**: releases all allocated resources of the request_queue
- **blk_register_queue**: register a block layer queue with sysfs
- **blk_unregister_queue**: counterpart ofblk_register_queue()
- **blk_set_default_limits**: reset limits to default values
- **blk_set_stacking_limits**: set default limits for stacking devices
- **blk_queue_bounce_limit**: set bounce buffer limit for queue
- **blk_queue_max_hw_sectors**: set max sectors for a request for this queue
- **blk_queue_chunk_sectors**: set size of the chunk for this queue
- **blk_queue_max_discard_sectors**: set max sectors for a single discard
- **blk_queue_max_write_same_sectors**: set max sectors for a single write same
- **blk_queue_max_write_zeroes_sectors**: set max sectors for a single write zeroes
- **blk_queue_max_zone_append_sectors**: set max sectors for a single zone append
- **blk_queue_max_segments**: set max hw segments for a request for this queue
- **blk_queue_max_discard_segments**: set max segments for discard requests
- **blk_queue_max_segment_size**: set max segment size for blk_rq_map_sg
- **blk_queue_logical_block_size**: set logical block size for the queue
- **blk_queue_physical_block_size**: set physical block size for the queue
- **blk_queue_zone_write_granularity**: set zone write granularity for the queue
- **blk_queue_alignment_offset**: set physical block alignment offset
- **blk_limits_io_min**: set minimum request size for a device
- **blk_queue_io_min**: set minimum request size for the queue
- **blk_limits_io_opt**: set optimal request size for a device
- **blk_queue_io_opt**: set optimal request size for the queue
- **blk_stack_limits**: adjust queue_limits for stacked devices
- **disk_stack_limits**: adjust queue limits for stacked drivers
- **blk_queue_update_dma_pad**: update pad mask
- **blk_queue_segment_boundary**: set boundary rules for segment merging
- **blk_queue_virt_boundary**: set boundary rules for bio merging
- **blk_queue_dma_alignment**: set dma length and memory alignment
- **blk_queue_update_dma_alignment**: update dma length and memory alignment
- **blk_set_queue_depth**: tell the block layer about the device queue depth
- **blk_queue_write_cache**: configure queue’s write cache
- **blk_queue_required_elevator_features**: Set a queue required elevator features
- **blk_queue_can_use_dma_map_merging**: configure queue for merging segments.
- **blk_queue_set_zoned**: configure a disk queue zoned model.
- **blk_execute_rq_nowait**: insert a request to I/O scheduler for execution
- **blk_execute_rq**: insert a request into queue for execution
- **blkdev_issue_flush**: queue a flush
- **blkdev_issue_discard**: queue a discard
- **blkdev_issue_write_same**: queue a write same operation
- **__blkdev_issue_zeroout**: generate number of zero filed write bios
- **blkdev_issue_zeroout**: zero-fill a block range
- **blk_rq_count_integrity_sg**: Count number of integrity scatterlist elements
- **blk_rq_map_integrity_sg**: Map integrity metadata into a scatterlist
- **blk_integrity_compare**: Compare integrity profile of two disks
- **blk_integrity_register**: Register a gendisk as being integrity-capable
- **blk_integrity_unregister**: Unregister block integrity profile
- **blk_trace_ioctl**: handle the ioctls associated with tracing
- **blk_trace_shutdown**: stop and cleanup trace structures
- **blk_add_trace_rq**: Add a trace for a request oriented action
- **blk_add_trace_bio**: Add a trace for a bio oriented action
- **blk_add_trace_bio_remap**: Add a trace for a bio-remap operation
- **blk_add_trace_rq_remap**: Add a trace for a request-remap operation
- **disk_release**: releases all allocated resources of the gendisk
- **__register_blkdev**: register a new block device
- **device_add_disk**: add disk information to kernel list
- **del_gendisk**: remove the gendisk
- **put_disk**: decrements the gendisk refcount
- **blk_cleanup_disk**: shutdown a gendisk allocated by blk_alloc_disk
- **set_disk_ro**: set a gendisk read-only
- **freeze_bdev**: lock a filesystem and force it into a consistent state
- **thaw_bdev**: unlock filesystem
- **bd_prepare_to_claim**: claim a block device
- **bd_abort_claiming**: abort claiming of a block device
- **blkdev_get_by_dev**: open a block device by device number
- **blkdev_get_by_path**: open a block device by name
- **lookup_bdev**: lookup a struct block_device by name
- **register_chrdev_region**: register a range of device numbers
- **alloc_chrdev_region**: register a range of char device numbers
- **__register_chrdev**: create and register a cdev occupying a range of minors
- **unregister_chrdev_region**: unregister a range of device numbers
- **__unregister_chrdev**: unregister and destroy a cdev
- **cdev_add**: add a char device to the system
- **cdev_set_parent**: set the parent kobject for a char device
- **cdev_device_add**: add a char device and it’s correspondingstructdevice, linkink
- **cdev_device_del**: inverse of cdev_device_add
- **cdev_del**: remove a cdev from the system
- **cdev_alloc**: allocate a cdev structure
- **cdev_init**: initialize a cdev structure
- **clk_notifier_register**: register a clock rate-change notifier callback
- **clk_notifier_unregister**: unregister a clock rate-change notifier callback
- **devm_clk_notifier_register**: register a managed rate-change notifier callback
- **clk_get_accuracy**: obtain the clock accuracy in ppb (parts per billion) for a clock source.
- **clk_set_phase**: adjust the phase shift of a clock signal
- **clk_get_phase**: return the phase shift of a clock signal
- **clk_set_duty_cycle**: adjust the duty cycle ratio of a clock signal
- **clk_get_scaled_duty_cycle**: return the duty cycle ratio of a clock signal
- **clk_is_match**: check if two clk’s point to the same hardware clock
- **clk_prepare**: prepare a clock source
- **clk_is_enabled_when_prepared**: indicate if preparing a clock also enables it.
- **clk_unprepare**: undo preparation of a clock source
- **clk_get**: lookup and obtain a reference to a clock producer.
- **clk_bulk_get**: lookup and obtain a number of references to clock producer.
- **clk_bulk_get_all**: lookup and obtain all available references to clock producer.
- **clk_bulk_get_optional**: lookup and obtain a number of references to clock producer
- **devm_clk_bulk_get**: managed get multiple clk consumers
- **devm_clk_bulk_get_optional**: managed get multiple optional consumer clocks
- **devm_clk_bulk_get_all**: managed get multiple clk consumers
- **devm_clk_get**: lookup and obtain a managed reference to a clock producer.
- **devm_clk_get_optional**: lookup and obtain a managed reference to an optional clock producer.
- **devm_get_clk_from_child**: lookup and obtain a managed reference to a clock producer from child node.
- **clk_rate_exclusive_get**: get exclusivity over the rate control of a producer
- **clk_rate_exclusive_put**: release exclusivity over the rate control of a producer
- **clk_enable**: inform the system when the clock source should be running.
- **clk_bulk_enable**: inform the system when the set of clks should be running.
- **clk_disable**: inform the system when the clock source is no longer required.
- **clk_bulk_disable**: inform the system when the set of clks is no longer required.
- **clk_get_rate**: obtain the current clock rate (in Hz) for a clock source. This is only valid once the clock source has been enabled.
- **clk_put**: “free” the clock source
- **clk_bulk_put**: “free” the clock source
- **clk_bulk_put_all**: “free” all the clock source
- **devm_clk_put**: “free” a managed clock source
- **clk_round_rate**: adjust a rate to the exact rate a clock can provide
- **clk_set_rate**: set the clock rate for a clock source
- **clk_set_rate_exclusive**: set the clock rate and claim exclusivity over clock source
- **clk_has_parent**: check if a clock is a possible parent for another
- **clk_set_rate_range**: set a rate range for a clock source
- **clk_set_min_rate**: set a minimum clock rate for a clock source
- **clk_set_max_rate**: set a maximum clock rate for a clock source
- **clk_set_parent**: set the parent clock source for this clock
- **clk_get_parent**: get the parent clock source for this clock
- **clk_get_sys**: get a clock based upon the device name
- **clk_save_context**: save clock context for poweroff
- **clk_restore_context**: restore clock context after poweroff
- **clk_get_optional**: lookup and obtain a reference to an optional clock producer.
- **RCU_NONIDLE**: Indicate idle-loop code that needs RCU readers
- **cond_resched_tasks_rcu_qs**: Report potential quiescent states to RCU
- **RCU_LOCKDEP_WARN**: emit lockdep splat if specified condition is met
- **unrcu_pointer**: mark a pointer as not being RCU protected
- **RCU_INITIALIZER**: statically initialize an RCU-protected global variable
- **rcu_assign_pointer**: assign to RCU-protected pointer
- **rcu_replace_pointer**: replace an RCU pointer, returning its old value
- **rcu_access_pointer**: fetch RCU pointer with no dereferencing
- **rcu_dereference_check**: rcu_dereference with debug checking
- **rcu_dereference_bh_check**: rcu_dereference_bh with debug checking
- **rcu_dereference_sched_check**: rcu_dereference_sched with debug checking
- **rcu_dereference_protected**: fetch RCU pointer when updates prevented
- **rcu_dereference**: fetch RCU-protected pointer for dereferencing
- **rcu_dereference_bh**: fetch an RCU-bh-protected pointer for dereferencing
- **rcu_dereference_sched**: fetch RCU-sched-protected pointer for dereferencing
- **rcu_pointer_handoff**: Hand off a pointer from RCU to other mechanism
- **rcu_read_lock**: mark the beginning of an RCU read-side critical section
- **rcu_read_unlock**: marks the end of an RCU read-side critical section.
- **rcu_read_lock_bh**: mark the beginning of an RCU-bh critical section
- **rcu_read_unlock_bh**: marks the end of a softirq-only RCU critical section
- **rcu_read_lock_sched**: mark the beginning of a RCU-sched critical section
- **rcu_read_unlock_sched**: marks the end of a RCU-classic critical section
- **RCU_INIT_POINTER**: initialize an RCU protected pointer
- **RCU_POINTER_INITIALIZER**: statically initialize an RCU protected pointer
- **kfree_rcu**: kfree an object after a grace period.
- **kvfree_rcu**: kvfree an object after a grace period.
- **rcu_head_init**: Initialize rcu_head forrcu_head_after_call_rcu()
- **rcu_head_after_call_rcu**: Has this rcu_head been passed tocall_rcu()?
- **rcu_is_cpu_rrupt_from_idle**: see if ‘interrupted’ from idle
- **rcu_idle_enter**: inform RCU that current CPU is entering idle
- **rcu_user_enter**: inform RCU that we are resuming userspace.
- **rcu_nmi_exit**: inform RCU of exit from NMI context
- **rcu_irq_exit**: inform RCU that current CPU is exiting irq towards idle
- **rcu_irq_exit_check_preempt**: Validate that scheduling is possible
- **rcu_idle_exit**: inform RCU that current CPU is leaving idle
- **rcu_user_exit**: inform RCU that we are exiting userspace.
- **__rcu_irq_enter_check_tick**: Enable scheduler tick on CPU if RCU needs it.
- **rcu_nmi_enter**: inform RCU of entry to NMI context
- **rcu_irq_enter**: inform RCU that current CPU is entering irq away from idle
- **rcu_is_watching**: see if RCU thinks that the current CPU is not idle
- **call_rcu**: Queue an RCU callback for invocation after a grace period.
- **synchronize_rcu**: wait until a grace period has elapsed.
- **get_state_synchronize_rcu**: Snapshot current RCU state
- **start_poll_synchronize_rcu**: Snapshot and start RCU grace period
- **poll_state_synchronize_rcu**: Conditionally wait for an RCU grace period
- **cond_synchronize_rcu**: Conditionally wait for an RCU grace period
- **rcu_barrier**: Wait until all in-flightcall_rcu()callbacks complete.
- **synchronize_rcu_expedited**: Brute-force RCU grace period
- **rcu_read_lock_held_common**: might we be in RCU-sched read-side critical section?
- **rcu_expedite_gp**: Expedite future RCU grace periods
- **rcu_unexpedite_gp**: Cancel priorrcu_expedite_gp()invocation
- **rcu_read_lock_held**: might we be in RCU read-side critical section?
- **rcu_read_lock_bh_held**: might we be in RCU-bh read-side critical section?
- **wakeme_after_rcu**: Callback function to awaken a task after grace period
- **init_rcu_head_on_stack**: initialize on-stack rcu_head for debugobjects
- **destroy_rcu_head_on_stack**: destroy on-stack rcu_head for debugobjects
- **srcu_read_lock_held**: might we be in SRCU read-side critical section?
- **srcu_dereference_check**: fetch SRCU-protected pointer for later dereferencing
- **srcu_dereference**: fetch SRCU-protected pointer for later dereferencing
- **srcu_dereference_notrace**: no tracing and no lockdep calls from here
- **srcu_read_lock**: register a new reader for an SRCU-protected structure.
- **srcu_read_unlock**: unregister a old reader from an SRCU-protected structure.
- **smp_mb__after_srcu_read_unlock**: ensure full ordering after srcu_read_unlock
- **init_srcu_struct**: initialize a sleep-RCU structure
- **srcu_readers_active**: returns true if there are readers. and false otherwise
- **cleanup_srcu_struct**: deconstruct a sleep-RCU structure
- **call_srcu**: Queue a callback for invocation after an SRCU grace period
- **synchronize_srcu_expedited**: Brute-force SRCU grace period
- **synchronize_srcu**: wait for prior SRCU read-side critical-section completion
- **get_state_synchronize_srcu**: Provide an end-of-grace-period cookie
- **start_poll_synchronize_srcu**: Provide cookie and start grace period
- **poll_state_synchronize_srcu**: Has cookie’s grace period ended?
- **srcu_barrier**: Wait until all in-flightcall_srcu()callbacks complete.
- **srcu_batches_completed**: return batches completed.
- **hlist_bl_del_rcu**: deletes entry from hash list without re-initialization
- **hlist_bl_add_head_rcu**: 
- **hlist_bl_for_each_entry_rcu**: iterate over rcu list of given type
- **list_tail_rcu**: returns the prev pointer of the head of the list
- **list_add_rcu**: add a new entry to rcu-protected list
- **list_add_tail_rcu**: add a new entry to rcu-protected list
- **list_del_rcu**: deletes entry from list without re-initialization
- **hlist_del_init_rcu**: deletes entry from hash list with re-initialization
- **list_replace_rcu**: replace old entry by new one
- **__list_splice_init_rcu**: join an RCU-protected list into an existing list.
- **list_splice_init_rcu**: splice an RCU-protected list into an existing list, designed for stacks.
- **list_splice_tail_init_rcu**: splice an RCU-protected list into an existing list, designed for queues.
- **list_entry_rcu**: get the struct for this entry
- **list_first_or_null_rcu**: get the first element from a list
- **list_next_or_null_rcu**: get the first element from a list
- **list_for_each_entry_rcu**: iterate over rcu list of given type
- **list_for_each_entry_srcu**: iterate over rcu list of given type
- **list_entry_lockless**: get the struct for this entry
- **list_for_each_entry_lockless**: iterate over rcu list of given type
- **list_for_each_entry_continue_rcu**: continue iteration over list of given type
- **list_for_each_entry_from_rcu**: iterate over a list from current point
- **hlist_del_rcu**: deletes entry from hash list without re-initialization
- **hlist_replace_rcu**: replace old entry by new one
- **hlists_swap_heads_rcu**: swap the lists the hlist heads point to
- **hlist_add_head_rcu**: 
- **hlist_add_tail_rcu**: 
- **hlist_add_before_rcu**: 
- **hlist_add_behind_rcu**: 
- **hlist_for_each_entry_rcu**: iterate over rcu list of given type
- **hlist_for_each_entry_srcu**: iterate over rcu list of given type
- **hlist_for_each_entry_rcu_notrace**: iterate over rcu list of given type (for tracing)
- **hlist_for_each_entry_rcu_bh**: iterate over rcu list of given type
- **hlist_for_each_entry_continue_rcu**: iterate over a hlist continuing after current point
- **hlist_for_each_entry_continue_rcu_bh**: iterate over a hlist continuing after current point
- **hlist_for_each_entry_from_rcu**: iterate over a hlist continuing from current point
- **hlist_nulls_del_init_rcu**: deletes entry from hash list with re-initialization
- **hlist_nulls_first_rcu**: returns the first element of the hash list.
- **hlist_nulls_next_rcu**: returns the element of the list afternode.
- **hlist_nulls_del_rcu**: deletes entry from hash list without re-initialization
- **hlist_nulls_add_head_rcu**: 
- **hlist_nulls_add_tail_rcu**: 
- **hlist_nulls_for_each_entry_rcu**: iterate over rcu list of given type
- **hlist_nulls_for_each_entry_safe**: iterate over list of given type safe against removal of list entry
- **rcu_sync_is_idle**: Are readers permitted to use their fastpaths?
- **rcu_sync_init**: Initialize an rcu_sync structure
- **rcu_sync_enter_start**: Force readers onto slow path for multiple updates
- **rcu_sync_func**: Callback function managing reader access to fastpath
- **rcu_sync_enter**: Force readers onto slowpath
- **rcu_sync_exit**: Allow readers back onto fast path after grace period
- **rcu_sync_dtor**: Clean up an rcu_sync structure

# Kernel Module miscellaneous.md API Documentation

## Parallel Port Devices

| 名称 | 描述 |
|---|---|
| parport_register_driver | register a parallel port device driver |
| module_parport_driver | Helper macro for registering a modular parport driver |
| parport_yield | relinquish a parallel port temporarily |
| parport_yield_blocking | relinquish a parallel port temporarily |
| parport_wait_event | wait for an event on a parallel port |
| parport_wait_peripheral | wait for status lines to change in 35ms |
| parport_negotiate | negotiate an IEEE 1284 mode |
| parport_write | write a block of data to a parallel port |
| parport_read | read a block of data from a parallel port |
| parport_set_timeout | set the inactivity timeout for a device |
| __parport_register_driver | register a parallel port device driver |
| parport_unregister_driver | deregister a parallel port device driver |
| parport_get_port | increment a port’s reference count |
| parport_put_port | decrement a port’s reference count |
| parport_register_port | register a parallel port |
| parport_announce_port | tell device drivers about a parallel port |
| parport_remove_port | deregister a parallel port |
| parport_register_dev_model | register a device on a parallel port |
| parport_unregister_device | deregister a device on a parallel port |
| parport_find_number | find a parallel port by number |
| parport_find_base | find a parallel port by base address |
| parport_claim | claim access to a parallel port device |
| parport_claim_or_block | claim access to a parallel port device |
| parport_release | give up access to a parallel port device |
| parport_open | find a device by canonical device number |
| parport_close | close a device opened withparport_open() |

## 16x50 UART Driver

| 名称 | 描述 |
|---|---|
| uart_update_timeout | update per-port FIFO timeout. |
| uart_get_baud_rate | return baud rate for a particular port |
| uart_get_divisor | return uart clock divisor |
| uart_console_write | write a console message to a serial port |
| uart_parse_earlycon | Parse earlycon options |
| uart_parse_options | Parse serial port baud/parity/bits/flow control. |
| uart_set_options | setup the serial console parameters |
| uart_register_driver | register a driver with the uart core layer |
| uart_unregister_driver | remove a driver from the uart core layer |
| uart_add_one_port | attach a driver-defined port structure |
| uart_remove_one_port | detach a driver defined port structure |
| uart_handle_dcd_change | handle a change of carrier detect state |
| uart_handle_cts_change | handle a change of clear-to-send state |
| uart_insert_char | push a char to the uart layer |
| uart_try_toggle_sysrq | Enables SysRq from serial line |
| uart_get_rs485_mode | retrieve rs485 properties for given uart |
| serial8250_get_port | retrieve struct uart_8250_port |
| serial8250_suspend_port | suspend one serial port |
| serial8250_resume_port | resume one serial port |
| serial8250_register_8250_port | register a serial port |
| serial8250_unregister_port | remove a 16x50 serial port at runtime |

## Pulse-Width Modulation (PWM)

| 名称 | 描述 |
|---|---|
| struct pwm_polarity | polarity of a PWM signal |
| struct pwm_args | board-dependent PWM arguments |
| struct pwm_device | PWM channel object |
| pwm_get_state | retrieve the current PWM state |
| pwm_init_state | prepare a new state to be applied withpwm_apply_state() |
| pwm_get_relative_duty_cycle | Get a relative duty cycle value |
| pwm_set_relative_duty_cycle | Set a relative duty cycle value |
| struct pwm_ops | PWM controller operations |
| struct pwm_chip | abstract a PWM controller |
| struct pwm_capture | PWM capture data |
| pwm_config | change a PWM device configuration |
| pwm_enable | start a PWM output toggling |
| pwm_disable | stop a PWM output toggling |
| pwm_set_chip_data | set private chip data for a PWM |
| pwm_get_chip_data | get private chip data for a PWM |
| pwmchip_add | register a new PWM chip |
| pwmchip_remove | remove a PWM chip |
| pwm_request | request a PWM device |
| pwm_request_from_chip | request a PWM device relative to a PWM chip |
| pwm_free | free a PWM device |
| pwm_apply_state | atomically apply a new state to a PWM device |
| pwm_capture | capture and report a PWM signal |
| pwm_adjust_config | adjust the current PWM config to the PWM arguments |
| of_pwm_get | request a PWM via the PWM framework |
| pwm_get | look up and request a PWM device |
| pwm_put | release a PWM device |
| devm_pwm_get | resource managedpwm_get() |
| devm_of_pwm_get | resource managedof_pwm_get() |
| devm_fwnode_pwm_get | request a resource managed PWM from firmware node |

## 总数统计

总共提取了 76 个函数+结构。

# Kernel Module misc_devices.md API Documentation

## Miscellaneous Devices

| 名称 | 描述 |
|---|---|
| misc_register | register a miscellaneous device |
| misc_deregister | unregister a miscellaneous device |

## 总数统计

总共提取了 2 个函数+结构。

# Kernel Module mm-api.md API Documentation

## Memory Management APIs

## User Space Memory Access

| 名称 | 描述 |
|---|---|
| access_ok | Checks if a user space pointer is valid |
| get_user | Get a simple variable from user space. |
| __get_user | Get a simple variable from user space, with less checking. |
| put_user | Write a simple value into user space. |
| __put_user | Write a simple value into user space, with less checking. |
| clear_user | Zero a block of memory in user space. |
| __clear_user | Zero a block of memory in user space, with less checking. |
| get_user_pages_fast | pin user pages in memory |

## Memory Allocation Controls

| 名称 | 描述 |
|---|---|
| struct gfp_t | Memory allocation flags. |
| gfpflags_normal_context | is gfp_flags a normal sleepable context? |

## The Slab Cache

| 名称 | 描述 |
|---|---|
| kmalloc | allocate memory |
| kmalloc_array | allocate memory for an array. |
| krealloc_array | reallocate memory for an array. |
| kcalloc | allocate memory for an array. The memory is set to zero. |
| kzalloc | allocate memory. The memory is set to zero. |
| kzalloc_node | allocate zeroed memory from a particular memory node. |
| kmem_cache_alloc | Allocate an object |
| kmem_cache_alloc_node | Allocate an object on the specified node |
| kmem_cache_free | Deallocate an object |
| kfree | free previously allocated memory |
| __ksize | Uninstrumented ksize. |
| kmem_cache_create_usercopy | Create a cache with a region suitable for copying to userspace |
| kmem_cache_create | Create a cache. |
| kmem_cache_shrink | Shrink a cache. |
| kmem_valid_obj | does the pointer reference a valid slab object? |
| kmem_dump_obj | Print available slab provenance information |
| krealloc | reallocate memory. The contents will remain unchanged. |
| kfree_sensitive | Clear sensitive information in memory before freeing |
| ksize | get the actual amount of memory allocated for a given object |
| kfree_const | conditionally free memory |
| kvmalloc_node | attempt to allocate physically contiguous memory, but upon failure, fall back to non-contiguous (vmalloc) allocation. |
| kvfree | Free memory. |

## Virtually Contiguous Mappings

| 名称 | 描述 |
|---|---|
| vm_unmap_aliases | unmap outstanding lazy aliases in the vmap layer |
| vm_unmap_ram | unmap linear kernel address space set up by vm_map_ram |
| vm_map_ram | map pages linearly into kernel virtual address (vmalloc space) |
| vfree | Release memory allocated byvmalloc() |
| vunmap | release virtual mapping obtained byvmap() |
| vmap | map an array of pages into virtually contiguous space |
| vmap_pfn | map an array of PFNs into virtually contiguous space |
| __vmalloc_node | allocate virtually contiguous memory |
| vmalloc | allocate virtually contiguous memory |
| vmalloc_no_huge | allocate virtually contiguous memory using small pages |
| vzalloc | allocate virtually contiguous memory with zero fill |
| vmalloc_user | allocate zeroed virtually contiguous memory for userspace |
| vmalloc_node | allocate memory on a specific node |
| vzalloc_node | allocate memory on a specific node with zero fill |
| vmalloc_32 | allocate virtually contiguous memory (32bit addressable) |
| vmalloc_32_user | allocate zeroed virtually contiguous 32bit memory |
| remap_vmalloc_range | map vmalloc pages to userspace |

## File Mapping and Page Cache

| 名称 | 描述 |
|---|---|
| read_cache_pages | populate an address space with some pages & start reads against them |
| page_cache_ra_unbounded | Start unchecked readahead. |
| readahead_expand | Expand a readahead request |
| delete_from_page_cache | delete page from page cache |
| filemap_fdatawrite_wbc | start writeback on mapping dirty pages in range |
| filemap_flush | mostly a non-blocking flush |
| filemap_range_has_page | check if a page exists in range. |
| filemap_fdatawait_range | wait for writeback to complete |
| filemap_fdatawait_range_keep_errors | wait for writeback to complete |
| file_fdatawait_range | wait for writeback to complete |
| filemap_fdatawait_keep_errors | wait for writeback without clearing errors |
| filemap_range_needs_writeback | check if range potentially needs writeback |
| filemap_write_and_wait_range | write out & wait on a file range |
| file_check_and_advance_wb_err | report wb error (if any) that was previously and advance wb_err to current one |
| file_write_and_wait_range | write out & wait on a file range |
| replace_page_cache_page | replace a pagecache page with a new one |
| add_to_page_cache_locked | add a locked page to the pagecache |
| add_page_wait_queue | Add an arbitrary waiter to a page’s wait queue |
| unlock_page | unlock a locked page |
| end_page_private_2 | Clear PG_private_2 and release any waiters |
| wait_on_page_private_2 | Wait for PG_private_2 to be cleared on a page |
| wait_on_page_private_2_killable | Wait for PG_private_2 to be cleared on a page |
| end_page_writeback | end writeback against a page |
| __lock_page | get a lock on the page, assuming we need to sleep to get it |
| page_cache_next_miss | Find the next gap in the page cache. |
| page_cache_prev_miss | Find the previous gap in the page cache. |
| pagecache_get_page | Find and get a reference to a page. |
| find_get_pages_contig | gang contiguous pagecache lookup |
| find_get_pages_range_tag | Find and return head pages matchingtag. |
| filemap_read | Read data from the page cache. |
| generic_file_read_iter | generic filesystem read routine |
| filemap_fault | read in file data for page fault handling |
| read_cache_page | read into page cache, fill it if needed |
| read_cache_page_gfp | read into page cache, using specified page allocation flags. |
| __generic_file_write_iter | write data to a file |
| generic_file_write_iter | write data to a file |
| try_to_release_page | release old fs-specific metadata on a page |
| balance_dirty_pages_ratelimited | balance dirty memory state |
| tag_pages_for_writeback | tag pages to be written by write_cache_pages |
| write_cache_pages | walk the list of dirty pages of the given address space and write all of them. |
| generic_writepages | walk the list of dirty pages of the given address space and writepage() all of them. |
| write_one_page | write out a single page and wait on I/O |
| wait_for_stable_page | wait for writeback to finish, if necessary. |
| truncate_inode_pages_range | truncate range of pages specified by start & end byte offsets |
| truncate_inode_pages | truncateallthe pages from an offset |
| truncate_inode_pages_final | truncateallpages before inode dies |
| invalidate_mapping_pages | Invalidate all clean, unlocked cache of one inode |
| invalidate_inode_pages2_range | remove range of pages from an address_space |
| invalidate_inode_pages2 | remove all pages from an address_space |
| truncate_pagecache | unmap and remove pagecache that has been truncated |
| truncate_setsize | update inode and pagecache for a new file size |
| pagecache_isize_extended | update pagecache after extension of i_size |
| truncate_pagecache_range | unmap and remove pagecache that is hole-punched |
| mapping_set_error | record a writeback error in the address_space |
| attach_page_private | Attach private data to a page. |
| detach_page_private | Detach private data from a page. |
| find_get_page | find and get a page reference |
| find_lock_page | locate, pin and lock a pagecache page |
| find_lock_head | Locate, pin and lock a pagecache page. |
| find_or_create_page | locate or add a pagecache page |
| grab_cache_page_nowait | returns locked page at given index in given cache |
| set_page_private_2 | Set PG_private_2 on a page and take a ref |
| struct readahead_control | Describes a readahead request. |
| page_cache_sync_readahead | generic file readahead |
| page_cache_async_readahead | file readahead for marked pages |
| readahead_page | Get the next page to read. |
| readahead_page_batch | Get a batch of pages to read. |
| readahead_pos | The byte offset into the file of this readahead request. |
| readahead_length | The number of bytes in this readahead request. |
| readahead_index | The index of the first page in this readahead request. |
| readahead_count | The number of pages in this readahead request. |
| readahead_batch_length | The number of bytes in the current batch. |
| page_mkwrite_check_truncate | check if page was truncated |
| i_blocks_per_page | How many blocks fit in this page. |

## Memory pools

| 名称 | 描述 |
|---|---|
| mempool_exit | exit a mempool initialized withmempool_init() |
| mempool_destroy | deallocate a memory pool |
| mempool_init | initialize a memory pool |
| mempool_create | create a memory pool |
| mempool_resize | resize an existing memory pool |
| mempool_alloc | allocate an element from a specific memory pool |
| mempool_free | return an element to the pool. |

## DMA pools

| 名称 | 描述 |
|---|---|
| dma_pool_create | Creates a pool of consistent memory blocks, for dma. |
| dma_pool_destroy | destroys a pool of dma memory blocks. |
| dma_pool_alloc | get a block of consistent memory |
| dma_pool_free | put block back into dma pool |
| dmam_pool_create | Manageddma_pool_create() |
| dmam_pool_destroy | Manageddma_pool_destroy() |

## More Memory Management Functions

| 名称 | 描述 |
|---|---|
| zap_vma_ptes | remove ptes mapping the vma |
| vm_insert_pages | insert multiple pages into user vma, batching the pmd lock. |
| vm_insert_page | insert single page into user vma |
| vm_map_pages | maps range of kernel pages starts with non zero offset |
| vm_map_pages_zero | map range of kernel pages starts with zero offset |
| vmf_insert_pfn_prot | insert single pfn into user vma with specified pgprot |
| vmf_insert_pfn | insert single pfn into user vma |
| vmf_insert_mixed_prot | insert single pfn into user vma with specified pgprot |
| remap_pfn_range | remap kernel memory to userspace |
| vm_iomap_memory | remap memory to userspace |
| unmap_mapping_pages | Unmap pages from processes. |
| unmap_mapping_range | unmap the portion of all mmaps in the specified address_space corresponding to the specified byte range in the underlying file. |
| follow_pte | look up PTE at a user virtual address |
| follow_pfn | look up PFN at a user virtual address |
| generic_access_phys | generic implementation for iomem mmap access |
| get_pfnblock_flags_mask | Return the requested group of flags for the pageblock_nr_pages block of pages |
| set_pfnblock_flags_mask | Set the requested group of flags for a pageblock_nr_pages block of pages |
| __putback_isolated_page | Return a now-isolated page back where we got it |
| __free_pages | Free pages allocated withalloc_pages(). |
| alloc_pages_exact | allocate an exact number physically-contiguous pages. |
| alloc_pages_exact_nid | allocate an exact number of physically-contiguous pages on a node. |
| free_pages_exact | release memory allocated viaalloc_pages_exact() |
| nr_free_zone_pages | count number of pages beyond high watermark |
| nr_free_buffer_pages | count number of pages beyond high watermark |
| find_next_best_node | find the next node that should appear in a given node’s fallback list |
| get_pfn_range_for_nid | Return the start and end page frames for a node |
| absent_pages_in_range | Return number of page frames in holes within a range |
| node_map_pfn_alignment | determine the maximum internode alignment |
| find_min_pfn_with_active_regions | Find the minimum PFN registered |
| free_area_init | Initialise all pg_data_t and zone data |
| set_dma_reserve | set the specified number of pages reserved in the first zone |
| setup_per_zone_wmarks | called when min_free_kbytes changes or when memory is hot-{added|removed} |
| alloc_contig_range | tries to allocate given range of pages |
| alloc_contig_pages | tries to find and allocate contiguous range of pages |
| numa_map_to_online_node | Find closest online node |
| alloc_pages_vma | Allocate a page for a VMA. |
| alloc_pages | Allocate pages. |
| mpol_misplaced | check whether current page node is valid in policy |
| mpol_shared_policy_init | initialize shared policy for inode |
| mpol_parse_str | parse string to mempolicy, for tmpfs mpol mount option. |
| mpol_to_str | format a mempolicy structure for printing |
| struct vm_fault_t | Return type for page fault handlers. |
| struct vm_fault_reason | Page fault handlers return a bitmask of these values to tell the core VM what happened when handling the fault. Used to decide whether a process gets delivered SIGBUS or just gets major/minor fault counters bumped up. |
| struct fault_flag | Fault flag definitions. |
| fault_flag_allow_retry_first | check ALLOW_RETRY the first time |
| page_maybe_dma_pinned | Report if a page is pinned for DMA. |
| find_vma_intersection | Look up the first VMA which intersects the interval |
| vma_lookup | Find a VMA at a specific address |
| vma_is_special_huge | Are transhuge page-table entries considered special? |
| seal_check_future_write | Check for F_SEAL_FUTURE_WRITE flag and handle it |
| is_highmem | helper function to quickly check if a struct zone is a highmem zone or not. This is an attempt to keep references to ZONE_{DMA/NORMAL/HIGHMEM/etc} in general code to a minimum. |
| for_each_online_pgdat | helper macro to iterate over all online nodes |
| for_each_zone | helper macro to iterate over all memory zones |
| next_zones_zonelist | Returns the next zone at or below highest_zoneidx within the allowed nodemask using a cursor within a zonelist as a starting point |
| first_zones_zonelist | Returns the first zone at or below highest_zoneidx within the allowed nodemask in a zonelist |
| for_each_zone_zonelist_nodemask | helper macro to iterate over valid zones in a zonelist at or below a given zone index and within a nodemask |
| for_each_zone_zonelist | helper macro to iterate over valid zones in a zonelist at or below a given zone index |
| pfn_valid | check if there is a valid memory map entry for a PFN |

## 总数统计

总共提取了 194 个函数+结构。

# Kernel Module regulator.md API Documentation


## API reference

| 名称 | 描述 |
|---|---|
| struct pre_voltage_change_data | Data sent with PRE_VOLTAGE_CHANGE event |
| struct regulator_bulk_data | Data used for bulk regulator operations. |
| struct regulator_state | regulator state during low power system states |
| struct regulation_constraints | regulator operating constraints. |
| struct regulator_consumer_supply | supply -> device mapping |
| struct regulator_init_data | regulator platform initialisation data. |
| struct regulator_ops | regulator operations. |
| struct regulator_desc | Static regulator descriptor |
| struct regulator_config | Dynamic regulator descriptor |
| struct regulator_err_state | regulator error/notification status |
| struct regulator_irq_data | regulator error/notification status data |
| struct regulator_irq_desc | notification sender for IRQ based events. |
| regulator_get | lookup and obtain a reference to a regulator. |
| regulator_get_exclusive | obtain exclusive access to a regulator. |
| regulator_get_optional | obtain optional access to a regulator. |
| regulator_put | “free” the regulator source |
| regulator_register_supply_alias | Provide device alias for supply lookup |
| regulator_unregister_supply_alias | Remove device alias |
| regulator_bulk_register_supply_alias | register multiple aliases |
| regulator_bulk_unregister_supply_alias | unregister multiple aliases |
| regulator_enable | enable regulator output |
| regulator_disable | disable regulator output |
| regulator_force_disable | force disable regulator output |
| regulator_disable_deferred | disable regulator output with delay |
| regulator_is_enabled | is the regulator output enabled |
| regulator_count_voltages | countregulator_list_voltage()selectors |
| regulator_list_voltage | enumerate supported voltages |
| regulator_get_hardware_vsel_register | get the HW voltage selector register |
| regulator_list_hardware_vsel | get the HW-specific register value for a selector |
| regulator_get_linear_step | return the voltage step size between VSEL values |
| regulator_is_supported_voltage | check if a voltage range can be supported |
| regulator_set_voltage | set regulator output voltage |
| regulator_set_voltage_time | get raise/fall time |
| regulator_set_voltage_time_sel | get raise/fall time |
| regulator_sync_voltage | re-apply last regulator output voltage |
| regulator_get_voltage | get regulator output voltage |
| regulator_set_current_limit | set regulator output current limit |
| regulator_get_current_limit | get regulator output current |
| regulator_set_mode | set regulator operating mode |
| regulator_get_mode | get regulator operating mode |
| regulator_get_error_flags | get regulator error information |
| regulator_set_load | set regulator load |
| regulator_allow_bypass | allow the regulator to go into bypass mode |
| regulator_register_notifier | register regulator event notifier |
| regulator_unregister_notifier | unregister regulator event notifier |
| regulator_bulk_get | get multiple regulator consumers |
| regulator_bulk_enable | enable multiple regulator consumers |
| regulator_bulk_disable | disable multiple regulator consumers |
| regulator_bulk_force_disable | force disable multiple regulator consumers |
| regulator_bulk_free | free multiple regulator consumers |
| regulator_notifier_call_chain | call regulator event notifier |
| regulator_mode_to_status | convert a regulator mode into a status |
| regulator_register | register regulator |
| regulator_unregister | unregister regulator |
| regulator_has_full_constraints | the system has fully specified constraints |
| rdev_get_drvdata | get rdev regulator driver data |
| regulator_get_drvdata | get regulator driver data |
| regulator_set_drvdata | set regulator driver data |
| rdev_get_id | get regulator ID |

## 总数统计

总共提取了 59 个函数+结构。

# Kernel Module reset.md API Documentation

## Reset controller API

## Introduction

## Consumer driver interface

## Reset controller driver interface

## API reference

| 名称 | 描述 |
|---|---|
| struct reset_control_bulk_data | Data used for bulk reset control operations. |
| reset_control_get_exclusive | Lookup and obtain an exclusive reference to a reset controller. |
| reset_control_bulk_get_exclusive | Lookup and obtain exclusive references to multiple reset controllers. |
| reset_control_get_exclusive_released | Lookup and obtain a temoprarily exclusive reference to a reset controller. |
| reset_control_bulk_get_exclusive_released | Lookup and obtain temporarily exclusive references to multiple reset controllers. |
| reset_control_bulk_get_optional_exclusive_released | Lookup and obtain optional temporarily exclusive references to multiple reset controllers. |
| reset_control_get_shared | Lookup and obtain a shared reference to a reset controller. |
| reset_control_bulk_get_shared | Lookup and obtain shared references to multiple reset controllers. |
| reset_control_get_optional_exclusive | optionalreset_control_get_exclusive() |
| reset_control_bulk_get_optional_exclusive | optionalreset_control_bulk_get_exclusive() |
| reset_control_get_optional_shared | optionalreset_control_get_shared() |
| reset_control_bulk_get_optional_shared | optionalreset_control_bulk_get_shared() |
| of_reset_control_get_exclusive | Lookup and obtain an exclusive reference to a reset controller. |
| of_reset_control_get_shared | Lookup and obtain a shared reference to a reset controller. |
| of_reset_control_get_exclusive_by_index | Lookup and obtain an exclusive reference to a reset controller by index. |
| of_reset_control_get_shared_by_index | Lookup and obtain a shared reference to a reset controller by index. |
| devm_reset_control_get_exclusive | resource managedreset_control_get_exclusive() |
| devm_reset_control_bulk_get_exclusive | resource managedreset_control_bulk_get_exclusive() |
| devm_reset_control_get_exclusive_released | resource managedreset_control_get_exclusive_released() |
| devm_reset_control_bulk_get_exclusive_released | resource managedreset_control_bulk_get_exclusive_released() |
| devm_reset_control_get_optional_exclusive_released | resource managed reset_control_get_optional_exclusive_released() |
| devm_reset_control_bulk_get_optional_exclusive_released | resource managed reset_control_bulk_optional_get_exclusive_released() |
| devm_reset_control_get_shared | resource managedreset_control_get_shared() |
| devm_reset_control_bulk_get_shared | resource managedreset_control_bulk_get_shared() |
| devm_reset_control_get_optional_exclusive | resource managedreset_control_get_optional_exclusive() |
| devm_reset_control_bulk_get_optional_exclusive | resource managedreset_control_bulk_get_optional_exclusive() |
| devm_reset_control_get_optional_shared | resource managedreset_control_get_optional_shared() |
| devm_reset_control_bulk_get_optional_shared | resource managedreset_control_bulk_get_optional_shared() |
| devm_reset_control_get_exclusive_by_index | resource managedreset_control_get_exclusive() |
| devm_reset_control_get_shared_by_index | resource managed reset_control_get_shared |
| reset_control_reset | reset the controlled device |
| reset_control_rearm | allow shared reset line to be re-triggered” |
| reset_control_assert | asserts the reset line |
| reset_control_deassert | deasserts the reset line |
| reset_control_status | returns a negative errno if not supported, a positive value if the reset line is asserted, or zero if the reset line is not asserted or if the desc is NULL (optional reset). |
| reset_control_acquire | acquires a reset control for exclusive use |
| reset_control_release | releases exclusive access to a reset control |
| reset_control_put | free the reset controller |
| of_reset_control_get_count | Count number of resets available with a device |
| of_reset_control_array_get | Get a list of reset controls using device node. |
| devm_reset_control_array_get | Resource managed reset control array get |
| reset_control_get_count | Count number of resets available with a device |
| struct reset_control_ops | reset controller driver callbacks |
| struct reset_control_lookup | represents a single lookup entry |
| struct reset_controller_dev | reset controller entity that might provide multiple reset controls |
| of_reset_simple_xlate | translate reset_spec to the reset line number |
| reset_controller_register | register a reset controller device |
| reset_controller_unregister | unregister a reset controller device |
| devm_reset_controller_register | resource managedreset_controller_register() |
| reset_controller_add_lookup | register a set of lookup entries |

## 总数统计

总共提取了 50 个函数+结构。

# Kernel Module scsi.md API Documentation

## SCSI Interfaces Guide

## Introduction

## SCSI upper layer

## SCSI mid layer

| 名称 | 描述 |
|---|---|
| struct scsi_vpd | SCSI Vital Product Data |
| shost_for_each_device | iterate over all devices of a host |
| __shost_for_each_device | iterate over all devices of a host (UNLOCKED) |
| scsi_device_supports_vpd | test if a device supports VPD pages |
| scsi_change_queue_depth | change a device’s queue depth |
| scsi_track_queue_full | track QUEUE_FULL events to adjust queue depth |
| scsi_get_vpd_page | Get Vital Product Data from a SCSI device |
| scsi_report_opcode | Find out if a given command opcode is supported |
| scsi_device_get | get an additional reference to a scsi_device |
| scsi_device_put | release a reference to a scsi_device |
| starget_for_each_device | helper to walk all devices of a target |
| __starget_for_each_device | helper to walk all devices of a target (UNLOCKED) |
| __scsi_device_lookup_by_target | find a device given the target (UNLOCKED) |
| scsi_device_lookup_by_target | find a device given the target |
| __scsi_device_lookup | find a device given the host (UNLOCKED) |
| scsi_device_lookup | find a device given the host |
| scsi_bios_ptable | Read PC partition table out of first sector of device. |
| scsi_partsize | Parse cylinders/heads/sectors from PC partition table |
| scsicam_bios_param | Determine geometry of a disk in cylinders/heads/sectors. |
| scsi_schedule_eh | schedule EH for SCSI host |
| scsi_block_when_processing_errors | Prevent cmds from being queued. |
| scsi_check_sense | Examine scsi cmd sense |
| scsi_eh_prep_cmnd | Save a scsi command info as part of error recovery |
| scsi_eh_restore_cmnd | Restore a scsi command info as part of error recovery |
| scsi_eh_finish_cmd | Handle a cmd that eh is finished with. |
| scsi_eh_get_sense | Get device sense data. |
| scsi_eh_ready_devs | check device ready state and recover if not. |
| scsi_eh_flush_done_q | finish processed commands or retry them. |
| scsi_get_sense_info_fld | get information field from sense data (either fixed or descriptor format) |
| scsi_dev_info_list_add | add one dev_info list entry. |
| scsi_dev_info_list_find | find a matching dev_info list entry. |
| scsi_dev_info_list_add_str | parse dev_list and add to the scsi_dev_info_list. |
| scsi_get_device_flags | get device specific flags from the dynamic device list. |
| scsi_exit_devinfo | remove /proc/scsi/device_info & the scsi_dev_info_list |
| scsi_init_devinfo | set up the dynamic device list. |
| scsi_ioctl | Dispatch ioctl to scsi device |
| __scsi_execute | insert request and wait for the result |
| scsi_alloc_sgtables | Allocate and initialize data and integrity scatterlists |
| scsi_block_requests | Utility function used by low-level drivers to prevent further commands from being queued to the device. |
| scsi_unblock_requests | Utility function used by low-level drivers to allow further commands to be queued to the device. |
| scsi_mode_select | issue a mode select |
| scsi_mode_sense | issue a mode sense, falling back from 10 to six bytes if necessary. |
| scsi_test_unit_ready | test if unit is ready |
| scsi_device_set_state | Take the given device through the device state model. |
| sdev_evt_send | send asserted event to uevent thread |
| sdev_evt_alloc | allocate a new scsi event |
| sdev_evt_send_simple | send asserted event to uevent thread |
| scsi_device_quiesce | Block all commands except power management. |
| scsi_device_resume | Restart user issued commands to a quiesced device. |
| scsi_internal_device_block_nowait | try to transition to the SDEV_BLOCK state |
| scsi_internal_device_unblock_nowait | resume a device after a block request |
| scsi_kmap_atomic_sg | find and atomically map an sg-elemnt |
| scsi_kunmap_atomic_sg | atomically unmap a virtual address, previously mapped with scsi_kmap_atomic_sg |
| scsi_vpd_lun_id | return a unique device identification |
| scsi_build_sense | build sense data for a command |
| scsi_dma_map | perform DMA mapping against command’s sg lists |
| scsi_dma_unmap | unmap command’s sg lists mapped by scsi_dma_map |
| scsi_proc_hostdir_add | Create directory in /proc for a scsi host |
| scsi_proc_hostdir_rm | remove directory in /proc for a scsi host |
| scsi_proc_host_add | Add entry for this host to appropriate /proc dir |
| scsi_proc_host_rm | remove this host’s entry from /proc |
| proc_print_scsidevice | return data about this host |
| scsi_add_single_device | Respond to user request to probe for/add device |
| scsi_remove_single_device | Respond to user request to remove a device |
| proc_scsi_write | handle writes to /proc/scsi/scsi |
| proc_scsi_open | glue function |
| scsi_init_procfs | create scsi and scsi/scsi in procfs |
| scsi_exit_procfs | Remove scsi/scsi and scsi from procfs |
| scsi_nl_rcv_msg | Receive message handler. |
| scsi_netlink_init | Called by SCSI subsystem to initialize the SCSI transport netlink interface |
| scsi_netlink_exit | Called by SCSI subsystem to disable the SCSI transport netlink interface |
| scsi_complete_async_scans | Wait for asynchronous scans to complete |
| scsi_unlock_floptical | unlock device via a special MODE SENSE command |
| scsi_alloc_sdev | allocate and setup a scsi_Device |
| scsi_target_reap_ref_release | remove target from visibility |
| scsi_alloc_target | allocate a new or find an existing target |
| scsi_target_reap | check to see if target is in use and destroy if not |
| scsi_probe_lun | probe a single LUN using a SCSI INQUIRY |
| scsi_add_lun | allocate and fully initialze a scsi_device |
| scsi_inq_str | print INQUIRY data from min to max index, strip trailing whitespace |
| scsi_probe_and_add_lun | probe a LUN, if a LUN is found add it |
| scsi_sequential_lun_scan | sequentially scan a SCSI target |
| scsi_report_lun_scan | Scan using SCSI REPORT LUN results |
| scsi_prep_async_scan | prepare for an async scan |
| scsi_finish_async_scan | asynchronous scan has finished |
| scsi_remove_device | unregister a device from the scsi bus |
| scsi_remove_target | try to remove a target and all its devices |
| scsi_remove_host | remove a scsi host |
| scsi_add_host_with_dma | add a scsi host with dma device |
| scsi_host_alloc | register a scsi host adapter instance. |
| scsi_host_lookup | get a reference to a Scsi_Host by host no |
| scsi_host_get | inc a Scsi_Host ref count |
| scsi_host_busy | Return the host busy counter |
| scsi_host_put | dec a Scsi_Host ref count |
| scsi_queue_work | Queue work to the Scsi_Host workqueue. |
| scsi_flush_work | Flush a Scsi_Host’s workqueue. |
| scsi_host_complete_all_commands | Terminate all running commands |
| scsi_host_busy_iter | Iterate over all busy commands |
| scsi_device_type | Return 17-char string indicating device type. |
| scsilun_to_int | convert a scsi_lun to an int |
| int_to_scsilun | reverts an int into a scsi_lun |
| scsi_normalize_sense | normalize main elements from either fixed or descriptor sense data format into a common format. |
| scsi_sense_desc_find | search for a given descriptor type in descriptor sense data format. |
| scsi_build_sense_buffer | build sense data in a buffer |
| scsi_set_sense_information | set the information field in a formatted sense data buffer |
| scsi_set_sense_field_pointer | set the field pointer sense key specific information in a formatted sense data buffer |
| fc_get_event_number | Obtain the next sequential FC event number |
| fc_host_post_fc_event | routine to do the work of posting an event on an fc_host. |
| fc_host_post_event | called to post an even on an fc_host. |
| fc_host_post_vendor_event | called to post a vendor unique event on an fc_host |
| fc_find_rport_by_wwpn | find the fc_rport pointer for a given wwpn |
| fc_host_fpin_rcv | routine to process a received FPIN. |
| fc_eh_timed_out | FC Transport I/O timeout intercept handler |
| fc_remove_host | called to terminate any fc_transport-related elements for a scsi host. |
| fc_remote_port_add | notify fc transport of the existence of a remote FC port. |
| fc_remote_port_delete | notifies the fc transport that a remote port is no longer in existence. |
| fc_remote_port_rolechg | notifies the fc transport that the roles on a remote may have changed. |
| fc_block_rport | Block SCSI eh thread for blocked fc_rport. |
| fc_block_scsi_eh | Block SCSI eh thread for blocked fc_rport |
| fc_vport_create | Admin App or LLDD requests creation of a vport |
| fc_vport_terminate | Admin App or LLDD requests termination of a vport |
| iscsi_lookup_endpoint | get ep from handle |
| iscsi_create_flashnode_sess | Add flashnode session entry in sysfs |
| iscsi_create_flashnode_conn | Add flashnode conn entry in sysfs |
| iscsi_find_flashnode_sess | finds flashnode session entry |
| iscsi_find_flashnode_conn | finds flashnode connection entry |
| iscsi_destroy_flashnode_sess | destroy flashnode session entry |
| iscsi_destroy_all_flashnode | destroy all flashnode session entries |
| iscsi_scan_finished | helper to report when running scans are done |
| iscsi_block_scsi_eh | block scsi eh until session state has transistioned |
| iscsi_unblock_session | set a session as logged in and start IO. |
| iscsi_create_session | create iscsi class session |
| iscsi_create_conn | create iscsi class connection |
| iscsi_destroy_conn | destroy iscsi class connection |
| iscsi_session_event | send session destr. completion event |
| sas_remove_children | tear down a devices SAS data structures |
| sas_remove_host | tear down a Scsi_Host’s SAS data structures |
| sas_get_address | return the SAS address of the device |
| sas_tlr_supported | checking TLR bit in vpd 0x90 |
| sas_disable_tlr | setting TLR flags |
| sas_enable_tlr | setting TLR flags |
| sas_phy_alloc | allocates and initialize a SAS PHY structure |
| sas_phy_add | add a SAS PHY to the device hierarchy |
| sas_phy_free | free a SAS PHY |
| sas_phy_delete | remove SAS PHY |
| scsi_is_sas_phy | check if astructdevicerepresents a SAS PHY |
| sas_port_add | add a SAS port to the device hierarchy |
| sas_port_free | free a SAS PORT |
| sas_port_delete | remove SAS PORT |
| scsi_is_sas_port | check if astructdevicerepresents a SAS port |
| sas_port_get_phy | try to take a reference on a port member |
| sas_port_add_phy | add another phy to a port to form a wide port |
| sas_port_delete_phy | remove a phy from a port or wide port |
| sas_end_device_alloc | allocate an rphy for an end device |
| sas_expander_alloc | allocate an rphy for an end device |
| sas_rphy_add | add a SAS remote PHY to the device hierarchy |
| sas_rphy_free | free a SAS remote PHY |
| sas_rphy_delete | remove and free SAS remote PHY |
| sas_rphy_unlink | unlink SAS remote PHY |
| sas_rphy_remove | remove SAS remote PHY |
| scsi_is_sas_rphy | check if astructdevicerepresents a SAS remote PHY |
| sas_attach_transport | instantiate SAS transport template |
| sas_release_transport | release SAS transport template instance |
| spi_schedule_dv_device | schedule domain validation to occur on the device |
| spi_display_xfer_agreement | Print the current target transfer agreement |
| spi_populate_tag_msg | place a tag message in a buffer |
| srp_tmo_valid | check timeout combination validity |
| srp_start_tl_fail_timers | start the transport layer failure timers |
| srp_reconnect_rport | reconnect to an SRP target port |
| srp_timed_out | SRP transport intercept of the SCSI timeout EH |
| srp_rport_get | increment rport reference count |
| srp_rport_put | decrement rport reference count |
| srp_rport_add | add a SRP remote port to the device hierarchy |
| srp_rport_del | remove a SRP remote port |
| srp_remove_host | tear down a Scsi_Host’s SRP data structures |
| srp_stop_rport_timers | stop the transport layer recovery timers |
| srp_attach_transport | instantiate SRP transport template |
| srp_release_transport | release SRP transport template instance |

## SCSI lower layer

## 总数统计

总共提取了 178 个函数+结构。

# Kernel Module spi.md API Documentation

## Serial Peripheral Interface (SPI)

| 名称 | 描述 |
|---|---|
| struct spi_statistics | statistics for spi transfers |
| struct spi_delay | SPI delay information |
| struct spi_device | Controller side proxy for an SPI slave device |
| struct spi_driver | Host side “protocol” driver |
| spi_unregister_driver | reverse effect of spi_register_driver |
| module_spi_driver | Helper macro for registering a SPI driver |
| struct spi_controller | interface to SPI master or slave controller |
| struct spi_res | spi resource management structure |
| struct spi_transfer | a read/write buffer pair |
| struct spi_message | one multi-segment SPI transaction |
| spi_message_init_with_transfers | Initialize spi_message and append transfers |
| spi_is_bpw_supported | Check if bits per word is supported |
| struct spi_replaced_transfers | structure describing the spi_transfer replacements that have occurred so that they can get reverted |
| spi_sync_transfer | synchronous SPI data transfer |
| spi_write | SPI synchronous write |
| spi_read | SPI synchronous read |
| spi_w8r8 | SPI synchronous 8 bit write followed by 8 bit read |
| spi_w8r16 | SPI synchronous 8 bit write followed by 16 bit read |
| spi_w8r16be | SPI synchronous 8 bit write followed by 16 bit big-endian read |
| struct spi_board_info | board-specific template for a SPI device |
| spi_register_board_info | register SPI devices for a given board |
| __spi_register_driver | register a SPI driver |
| spi_alloc_device | Allocate a new SPI device |
| spi_add_device | Add spi_device allocated with spi_alloc_device |
| spi_new_device | instantiate one new SPI device |
| spi_unregister_device | unregister a single SPI device |
| spi_finalize_current_transfer | report completion of a transfer |
| spi_take_timestamp_pre | helper for drivers to collect the beginning of the TX timestamp for the requested byte from the SPI transfer. The frequency with which this function must be called (once per word, once for the whole transfer, once per batch of words etc) is arbitrary as long as thetxbuffer offset is greater than or equal to the requested byte at the time of the call. The timestamp is only taken once, at the first such call. It is assumed that the driver advances itstxbuffer pointer monotonically. |
| spi_take_timestamp_post | helper for drivers to collect the end of the TX timestamp for the requested byte from the SPI transfer. Can be called with an arbitrary frequency: only the first call wheretxexceeds or is equal to the requested word will be timestamped. |
| spi_get_next_queued_message | called by driver to check for queued messages |
| spi_finalize_current_message | the current message is complete |
| spi_new_ancillary_device | Register ancillary SPI device |
| spi_slave_abort | abort the ongoing transfer request on an SPI slave controller |
| __spi_alloc_controller | allocate an SPI master or slave controller |
| __devm_spi_alloc_controller | resource-managed__spi_alloc_controller() |
| spi_register_controller | register SPI master or slave controller |
| devm_spi_register_controller | register managed SPI master or slave controller |
| spi_unregister_controller | unregister SPI master or slave controller |
| spi_busnum_to_master | look up master associated with bus_num |
| spi_res_alloc | allocate a spi resource that is life-cycle managed during the processing of a spi_message while using spi_transfer_one |
| spi_res_free | free an spi resource |
| spi_res_add | add a spi_res to the spi_message |
| spi_res_release | release all spi resources for this message |
| spi_replace_transfers | replace transfers with several transfers and register change with spi_message.resources |
| spi_split_transfers_maxsize | split spi transfers into multiple transfers when an individual transfer exceeds a certain size |
| spi_setup | setup SPI mode and clock rate |
| spi_async | asynchronous SPI transfer |
| spi_async_locked | version of spi_async with exclusive bus usage |
| spi_sync | blocking/synchronous SPI data transfers |
| spi_sync_locked | version of spi_sync with exclusive bus usage |
| spi_bus_lock | obtain a lock for exclusive SPI bus usage |
| spi_bus_unlock | release the lock for exclusive SPI bus usage |
| spi_write_then_read | SPI synchronous write followed by read |

## 总数统计

总共提取了 53 个函数+结构。

# Kernel Module tracepoint.md API Documentation

## The Linux Kernel Tracepoint API

## Introduction

## IRQ

| 名称 | 描述 |
|---|---|
| trace_irq_handler_entry | called immediately before the irq action handler |
| trace_irq_handler_exit | called immediately after the irq action handler returns |
| trace_softirq_entry | called immediately before the softirq handler |
| trace_softirq_exit | called immediately after the softirq handler returns |
| trace_softirq_raise | called immediately when a softirq is raised |

## SIGNAL

| 名称 | 描述 |
|---|---|
| trace_signal_generate | called when a signal is generated |
| trace_signal_deliver | called when a signal is delivered |

## Block IO

| 名称 | 描述 |
|---|---|
| trace_block_touch_buffer | mark a buffer accessed |
| trace_block_dirty_buffer | mark a buffer dirty |
| trace_block_rq_requeue | place block IO request back on a queue |
| trace_block_rq_complete | block IO operation completed by device driver |
| trace_block_rq_insert | insert block operation request into queue |
| trace_block_rq_issue | issue pending block IO request operation to device driver |
| trace_block_rq_merge | merge request with another one in the elevator |
| trace_block_bio_complete | completed all work on the block operation |
| trace_block_bio_bounce | used bounce buffer when processing block operation |
| trace_block_bio_backmerge | merging block operation to the end of an existing operation |
| trace_block_bio_frontmerge | merging block operation to the beginning of an existing operation |
| trace_block_bio_queue | putting new block IO operation in queue |
| trace_block_getrq | get a free request entry in queue for block IO operations |
| trace_block_plug | keep operations requests in request queue |
| trace_block_unplug | release of operations requests in request queue |
| trace_block_split | split a single bio struct into two bio structs |
| trace_block_bio_remap | map request for a logical device to the raw device |
| trace_block_rq_remap | map request for a block operation request |

## Workqueue

| 名称 | 描述 |
|---|---|
| trace_workqueue_queue_work | called when a work gets queued |
| trace_workqueue_activate_work | called when a work gets activated |
| trace_workqueue_execute_start | called immediately before the workqueue callback |
| trace_workqueue_execute_end | called immediately after the workqueue callback |

## 总数统计

总共提取了 29 个函数+结构。

# Kernel Module v4l2-dev.md API Documentation

## 2.4.Video device’ s internal representation

## 2.4.1.ioctls and locking

## 2.4.2.Video device registration

## 2.4.3.video device debugging

## 2.4.4.Video device cleanup

## 2.4.5.helper functions

## 2.4.6.video_device functions and data structures

| 名称 | 描述 |
|---|---|
| struct vfl_devnode_type | type of V4L2 device node |
| struct vfl_devnode_direction | Identifies if astructvideo_devicecorresponds to a receiver, a transmitter or a mem-to-mem device. |
| struct v4l2_video_device_flags | Flags used bystructvideo_device |
| struct v4l2_prio_state | stores the priority states |
| v4l2_prio_init | initializes astructv4l2_prio_state |
| v4l2_prio_change | changes the v4l2 file handler priority |
| v4l2_prio_open | Implements the priority logic for a file handler open |
| v4l2_prio_close | Implements the priority logic for a file handler close |
| v4l2_prio_max | Return the maximum priority, as stored at theglobalarray. |
| v4l2_prio_check | Implements the priority logic for a file handler close |
| struct v4l2_file_operations | fs operations used by a V4L2 device |
| struct video_device | Structure used to create and manage the V4L2 device nodes. |
| media_entity_to_video_device | Returns astructvideo_devicefrom thestructmedia_entityembedded on it. |
| to_video_device | Returns astructvideo_devicefrom thestructdeviceembedded on it. |
| __video_register_device | register video4linux devices |
| video_register_device | register video4linux devices |
| video_register_device_no_warn | register video4linux devices |
| video_unregister_device | Unregister video devices. |
| video_device_alloc | helper function to allocstructvideo_device |
| video_device_release | helper function to releasestructvideo_device |
| video_device_release_empty | helper function to implement the video_device->release() callback. |
| v4l2_disable_ioctl | mark that a given command isn’t implemented. shouldn’t use core locking |
| video_get_drvdata | gets private data fromstructvideo_device. |
| video_set_drvdata | sets private data fromstructvideo_device. |
| video_devdata | getsstructvideo_devicefrom struct file. |
| video_drvdata | gets private data fromstructvideo_deviceusing the struct file. |
| video_device_node_name | returns the video device name |
| video_is_registered | returns true if thestructvideo_deviceis registered. |

## 总数统计

总共提取了 28 个函数+结构。

# Kernel Module v4l2-device.md API Documentation

## 2.5.V4L2 device instance

## 2.5.1.v4l2_device functions and data structures

| 名称 | 描述 |
|---|---|
| struct v4l2_device | main struct to for V4L2 device drivers |
| v4l2_device_get | gets a V4L2 device reference |
| v4l2_device_put | puts a V4L2 device reference |
| v4l2_device_register | Initialize v4l2_dev and makedev->driver_datapoint tov4l2_dev. |
| v4l2_device_set_name | Optional function to initialize the name field of structv4l2_device |
| v4l2_device_disconnect | Change V4L2 device state to disconnected. |
| v4l2_device_unregister | Unregister all sub-devices and any other resources related tov4l2_dev. |
| v4l2_device_register_subdev | Registers a subdev with a v4l2 device. |
| v4l2_device_unregister_subdev | Unregisters a subdev with a v4l2 device. |
| __v4l2_device_register_subdev_nodes | Registers device nodes for all subdevs of the v4l2 device that are marked with theV4L2_SUBDEV_FL_HAS_DEVNODEflag. |
| v4l2_device_register_subdev_nodes | Registers subdevices device nodes with unrestricted access to the subdevice userspace operations |
| v4l2_device_register_ro_subdev_nodes | Registers subdevices device nodes in read-only mode |
| v4l2_subdev_notify | Sends a notification to v4l2_device. |
| v4l2_device_supports_requests | Test if requests are supported. |
| v4l2_device_for_each_subdev | Helper macro that interates over all sub-devices of a givenv4l2_device. |
| __v4l2_device_call_subdevs_p | Calls the specified operation for all subdevs matching the condition. |
| __v4l2_device_call_subdevs | Calls the specified operation for all subdevs matching the condition. |
| __v4l2_device_call_subdevs_until_err_p | Calls the specified operation for all subdevs matching the condition. |
| __v4l2_device_call_subdevs_until_err | Calls the specified operation for all subdevs matching the condition. |
| v4l2_device_call_all | Calls the specified operation for all subdevs matching thev4l2_subdev.grp_id, as assigned by the bridge driver. |
| v4l2_device_call_until_err | Calls the specified operation for all subdevs matching thev4l2_subdev.grp_id, as assigned by the bridge driver, until an error occurs. |
| v4l2_device_mask_call_all | Calls the specified operation for all subdevices where a group ID matches a specified bitmask. |
| v4l2_device_mask_call_until_err | Calls the specified operation for all subdevices where a group ID matches a specified bitmask. |
| v4l2_device_has_op | checks if any subdev with matching grpid has a given ops. |
| v4l2_device_mask_has_op | checks if any subdev with matching group mask has a given ops. |

## 总数统计

总共提取了 25 个函数+结构。

