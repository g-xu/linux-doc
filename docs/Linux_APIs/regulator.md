# Kernel Module regulator.md API Documentation

## Voltage and current regulator API¶

## Introduction¶

## Consumer driver interface¶

## Regulator driver interface¶

## Machine interface¶

## API reference¶

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
