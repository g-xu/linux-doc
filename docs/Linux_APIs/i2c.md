# Kernel Module i2c.md API Documentation

## I2C and SMBus Subsystem¶

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
