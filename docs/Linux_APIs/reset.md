# Kernel Module reset.md API Documentation

## Reset controller API¶

## Introduction¶

## Consumer driver interface¶

## Reset controller driver interface¶

## API reference¶

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
