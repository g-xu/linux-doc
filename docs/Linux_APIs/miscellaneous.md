# Kernel Module miscellaneous.md API Documentation

## Parallel Port Devices¶

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

## 16x50 UART Driver¶

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

## Pulse-Width Modulation (PWM)¶

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
