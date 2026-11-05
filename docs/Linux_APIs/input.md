# Kernel Module input.md API Documentation

## Input Subsystem¶

## Input core¶

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

## Multitouch Library¶

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

## Matrix keyboards/keypads¶

| 名称 | 描述 |
|---|---|
| struct matrix_keymap_data | keymap for matrix keyboards |
| struct matrix_keypad_platform_data | platform-dependent keypad data |

## Sparse keymap support¶

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
