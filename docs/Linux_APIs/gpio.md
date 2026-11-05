# Kernel Module index.md API Documentation

## General Purpose Input/Output (GPIO)¶

## Core¶

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

## ACPI support¶

| 名称 | 描述 |
|---|---|
| acpi_get_and_request_gpiod | Translate ACPI GPIO pin to GPIO descriptor and hold a refcount to the GPIO device. |
| acpi_gpio_get_io_resource | Fetch details of an ACPI resource if it is a GPIO I/O resource or return False if not. |
| acpi_gpiochip_request_interrupts | Register isr for gpio chip ACPI events |
| acpi_gpiochip_free_interrupts | Free GPIO ACPI event interrupts. |
| acpi_dev_gpio_irq_get_by | Find GpioInt and translate it to Linux IRQ number |

## Device tree support¶

| 名称 | 描述 |
|---|---|
| gpiod_get_from_of_node | obtain a GPIO from an OF node |
| of_mm_gpiochip_add_data | Add memory mapped GPIO chip (bank) |
| of_mm_gpiochip_remove | Remove memory mapped GPIO chip (bank) |

## Device-managed API¶

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

## sysfs helpers¶

| 名称 | 描述 |
|---|---|
| gpiod_export | export a GPIO through sysfs |
| gpiod_export_link | create a sysfs link to an exported GPIO node |
| gpiod_unexport | reverse effect ofgpiod_export() |

## 总数统计

总共提取了 95 个函数+结构。
