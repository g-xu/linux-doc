# Kernel Module scsi.md API Documentation

## SCSI Interfaces Guide¶

## Introduction¶

## SCSI upper layer¶

## SCSI mid layer¶

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

## SCSI lower layer¶

## 总数统计

总共提取了 178 个函数+结构。
