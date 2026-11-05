# Kernel Module genericirq.md API Documentation

## Generic interrupt chip¶

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

## Structures¶

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

## Public Functions Provided¶

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

## Internal Functions Provided¶

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

## Credits¶

## 总数统计

总共提取了 106 个函数+结构。
