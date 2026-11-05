# Kernel Module infrastructure.md API Documentation

## Device drivers infrastructure¶

## The Basic Device Driver-Model Structures¶

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

## Device Drivers Base¶

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

## Device Drivers DMA Management¶

| 名称 | 描述 |
|---|---|
| dmam_free_coherent | Managed dma_free_coherent() |
| dmam_alloc_attrs | Managed dma_alloc_attrs() |
| dma_map_sg_attrs | Map the given buffer for DMA |
| dma_map_sgtable | Map the given buffer for DMA |
| dma_can_mmap | check if a given device supports dma_mmap_* |
| dma_mmap_attrs | map a coherent DMA allocation into user space |

## Device drivers PnP support¶

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

## Userspace IO devices¶

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
