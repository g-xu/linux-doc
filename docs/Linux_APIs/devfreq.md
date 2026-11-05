# Kernel Module devfreq.md API Documentation

## Device Frequency Scaling¶

## Introduction¶

## API¶

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
