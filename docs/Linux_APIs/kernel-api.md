# Kernel Module kernel-api.md API Documentation

## The Linux Kernel API

## List Management Functions

| 名称 | 描述 |
|---|---|
| INIT_LIST_HEAD | Initialize a list_head structure |
| list_add | add a new entry |
| list_add_tail | add a new entry |
| list_del | deletes entry from list. |
| list_replace | replace old entry by new one |
| list_replace_init | replace old entry by new one and initialize the old one |
| list_swap | replace entry1 with entry2 and re-add entry1 at entry2’s position |
| list_del_init | deletes entry from list and reinitialize it. |
| list_move | delete from one list and add as another’s head |
| list_move_tail | delete from one list and add as another’s tail |
| list_bulk_move_tail | move a subsection of a list to its tail |
| list_is_first | tests whetherlistis the first entry in listhead |
| list_is_last | tests whetherlistis the last entry in listhead |
| list_empty | tests whether a list is empty |
| list_del_init_careful | deletes entry from list and reinitialize it. |
| list_empty_careful | tests whether a list is empty and not being modified |
| list_rotate_left | rotate the list to the left |
| list_rotate_to_front | Rotate list to specific item. |
| list_is_singular | tests whether a list has just one entry. |
| list_cut_position | cut a list into two |
| list_cut_before | cut a list into two, before given entry |
| list_splice | join two lists, this is designed for stacks |
| list_splice_tail | join two lists, each list being a queue |
| list_splice_init | join two lists and reinitialise the emptied list. |
| list_splice_tail_init | join two lists and reinitialise the emptied list |
| list_entry | get the struct for this entry |
| list_first_entry | get the first element from a list |
| list_last_entry | get the last element from a list |
| list_first_entry_or_null | get the first element from a list |
| list_next_entry | get the next element in list |
| list_prev_entry | get the prev element in list |
| list_for_each | iterate over a list |
| list_for_each_continue | continue iteration over a list |
| list_for_each_prev | iterate over a list backwards |
| list_for_each_safe | iterate over a list safe against removal of list entry |
| list_for_each_prev_safe | iterate over a list backwards safe against removal of list entry |
| list_entry_is_head | test if the entry points to the head of the list |
| list_for_each_entry | iterate over list of given type |
| list_for_each_entry_reverse | iterate backwards over list of given type. |
| list_prepare_entry | prepare a pos entry for use inlist_for_each_entry_continue() |
| list_for_each_entry_continue | continue iteration over list of given type |
| list_for_each_entry_continue_reverse | iterate backwards from the given point |
| list_for_each_entry_from | iterate over list of given type from the current point |
| list_for_each_entry_from_reverse | iterate backwards over list of given type from the current point |
| list_for_each_entry_safe | iterate over list of given type safe against removal of list entry |
| list_for_each_entry_safe_continue | continue list iteration safe against removal |
| list_for_each_entry_safe_from | iterate over list from current point safe against removal |
| list_for_each_entry_safe_reverse | iterate backwards over list safe against removal |
| list_safe_reset_next | reset a stale list_for_each_entry_safe loop |
| hlist_unhashed | Has node been removed from list and reinitialized? |
| hlist_unhashed_lockless | Version of hlist_unhashed for lockless use |
| hlist_empty | Is the specified hlist_head structure an empty hlist? |
| hlist_del | Delete the specified hlist_node from its list |
| hlist_del_init | Delete the specified hlist_node from its list and initialize |
| hlist_add_head | add a new entry at the beginning of the hlist |
| hlist_add_before | add a new entry before the one specified |
| hlist_add_behind | add a new entry after the one specified |
| hlist_add_fake | create a fake hlist consisting of a single headless node |
| hlist_fake | Is this node a fake hlist? |
| hlist_is_singular_node | is node the only element of the specified hlist? |
| hlist_move_list | Move an hlist |
| hlist_for_each_entry | iterate over list of given type |
| hlist_for_each_entry_continue | iterate over a hlist continuing after current point |
| hlist_for_each_entry_from | iterate over a hlist continuing from current point |
| hlist_for_each_entry_safe | iterate over list of given type safe against removal of list entry |

## Basic C Library Functions

| 名称                   | 描述                                                                                                                          |
| -------------------- | --------------------------------------------------------------------------------------------------------------------------- |
| simple_strtoull      | convert a string to an unsigned long long                                                                                   |
| simple_strtoul       | convert a string to an unsigned long                                                                                        |
| simple_strtol        | convert a string to a signed long                                                                                           |
| simple_strtoll       | convert a string to a signed long long                                                                                      |
| vsnprintf            | Format a string and place it in a buffer                                                                                    |
| vscnprintf           | Format a string and place it in a buffer                                                                                    |
| snprintf             | Format a string and place it in a buffer                                                                                    |
| scnprintf            | Format a string and place it in a buffer                                                                                    |
| vsprintf             | Format a string and place it in a buffer                                                                                    |
| sprintf              | Format a string and place it in a buffer                                                                                    |
| vbin_printf          | Parse a format string and place args’ binary value in a buffer                                                              |
| bstr_printf          | Format a string from binary arguments and place it in a buffer                                                              |
| bprintf              | Parse a format string and place args’ binary value in a buffer                                                              |
| vsscanf              | Unformat a buffer into a list of arguments                                                                                  |
| sscanf               | Unformat a buffer into a list of arguments                                                                                  |
| kstrtoul             | convert a string to an unsigned long                                                                                        |
| kstrtol              | convert a string to a long                                                                                                  |
| kstrtoull            | convert a string to an unsigned long long                                                                                   |
| kstrtoll             | convert a string to a long long                                                                                             |
| kstrtouint           | convert a string to an unsigned int                                                                                         |
| kstrtoint            | convert a string to an int                                                                                                  |
| kstrtobool           | convert common user inputs into boolean values                                                                              |
| string_get_size      | get the size in the specified units                                                                                         |
| string_unescape      | unquote characters in the given string                                                                                      |
| string_escape_mem    | quote characters in the given memory buffer                                                                                 |
| kfree_strarray       | free a number of dynamically allocated strings contained in an array and the array itself                                   |
| strncasecmp          | Case insensitive, length-limited string comparison                                                                          |
| strcpy               | Copy aNULterminated string                                                                                                  |
| strncpy              | Copy a length-limited, C-string                                                                                             |
| strlcpy              | Copy a C-string into a sized buffer                                                                                         |
| strscpy              | Copy a C-string into a sized buffer                                                                                         |
| strscpy_pad          | Copy a C-string into a sized buffer                                                                                         |
| stpcpy               | copy a string from src to dest returning a pointer to the new end of dest, including src’sNUL-terminator. May overrun dest. |
| strcat               | Append oneNUL-terminatedstring to another                                                                                   |
| strncat              | Append a length-limited, C-string to another                                                                                |
| strlcat              | Append a length-limited, C-string to another                                                                                |
| strcmp               | Compare two strings                                                                                                         |
| strncmp              | Compare two length-limited strings                                                                                          |
| strchr               | Find the first occurrence of a character in a string                                                                        |
| strchrnul            | Find and return a character in a string, or end of string                                                                   |
| strrchr              | Find the last occurrence of a character in a string                                                                         |
| strnchr              | Find a character in a length limited string                                                                                 |
| skip_spaces          | Removes leading whitespace fromstr.                                                                                         |
| strim                | Removes leading and trailing whitespace froms.                                                                              |
| strlen               | Find the length of a string                                                                                                 |
| strnlen              | Find the length of a length-limited string                                                                                  |
| strspn               | Calculate the length of the initial substring ofswhich only contain letters inaccept                                        |
| strcspn              | Calculate the length of the initial substring ofswhich does not contain letters inreject                                    |
| strpbrk              | Find the first occurrence of a set of characters                                                                            |
| strsep               | Split a string into tokens                                                                                                  |
| sysfs_streq          | return true if strings are equal, modulo trailing newline                                                                   |
| match_string         | matches given string in an array                                                                                            |
| __sysfs_match_string | matches given string in an array                                                                                            |
| memset               | Fill a region of memory with the given value                                                                                |
| memset16             | Fill a memory area with a uint16_t                                                                                          |
| memset32             | Fill a memory area with a uint32_t                                                                                          |
| memset64             | Fill a memory area with a uint64_t                                                                                          |
| memcpy               | Copy one area of memory to another                                                                                          |
| memmove              | Copy one area of memory to another                                                                                          |
| memcmp               | Compare two areas of memory                                                                                                 |
| bcmp                 | returns 0 if and only if the buffers have identical contents.                                                               |
| memscan              | Find a character in an area of memory.                                                                                      |
| strstr               | Find the first substring in aNULterminated string                                                                           |
| strnstr              | Find the first substring in a length-limited string                                                                         |
| memchr               | Find a character in an area of memory.                                                                                      |
| memchr_inv           | Find an unmatching character in an area of memory.                                                                          |
| strreplace           | Replace all occurrences of character in string.                                                                             |
| sysfs_match_string   | matches given string in an array                                                                                            |
| strstarts            | doesstrstart withprefix?                                                                                                    |
| memzero_explicit     | Fill a region of memory (e.g. sensitive keying data) with 0s.                                                               |
| kbasename            | return the last part of a pathname.                                                                                         |
| memcpy_and_pad       | Copy one buffer to another with padding                                                                                     |
| str_has_prefix       | Test if a string has a given prefix                                                                                         |
| kstrdup              | allocate space for and copy an existing string                                                                              |
| kstrdup_const        | conditionally duplicate an existing const string                                                                            |
| kstrndup             | allocate space for and copy an existing string                                                                              |
| kmemdup              | duplicate region of memory                                                                                                  |
| kmemdup_nul          | Create a NUL-terminated string from unterminated data                                                                       |
| memdup_user          | duplicate memory region from user space                                                                                     |
| vmemdup_user         | duplicate memory region from user space                                                                                     |
| strndup_user         | duplicate an existing string from user space                                                                                |
| memdup_user_nul      | duplicate memory region from user space and NUL-terminate                                                                   |

## Basic Kernel Library Functions

| 名称 | 描述 |
|---|---|
| set_bit | Atomically set a bit in memory |
| clear_bit | Clears a bit in memory |
| change_bit | Toggle a bit in memory |
| test_and_set_bit | Set a bit and return its old value |
| test_and_clear_bit | Clear a bit and return its old value |
| test_and_change_bit | Change a bit and return its old value |
| __set_bit | Set a bit in memory |
| __clear_bit | Clears a bit in memory |
| __change_bit | Toggle a bit in memory |
| __test_and_set_bit | Set a bit and return its old value |
| __test_and_clear_bit | Clear a bit and return its old value |
| __test_and_change_bit | Change a bit and return its old value |
| test_bit | Determine whether a bit is set |
| clear_bit_unlock | Clear a bit in memory, for unlock |
| __clear_bit_unlock | Clears a bit in memory |
| test_and_set_bit_lock | Set a bit and return its old value, for lock |
| clear_bit_unlock_is_negative_byte | Clear a bit in memory and test if bottom byte is negative, for unlock. |
| __bitmap_shift_right | logical right shift of the bits in a bitmap |
| __bitmap_shift_left | logical left shift of the bits in a bitmap |
| bitmap_cut | remove bit region from bitmap and right shift remaining bits |
| bitmap_find_next_zero_area_off | find a contiguous aligned zero area |
| bitmap_parse_user | convert an ASCII hex string in a user buffer into a bitmap |
| bitmap_print_to_pagebuf | convert bitmap to list or hex format ASCII string |
| bitmap_print_bitmask_to_buf | convert bitmap to hex bitmask format ASCII string |
| bitmap_print_list_to_buf | convert bitmap to decimal list format ASCII string |
| bitmap_parselist | convert list format ASCII string to bitmap |
| bitmap_parselist_user |  |
| bitmap_parse | convert an ASCII hex string into a bitmap. |
| bitmap_remap | Apply map defined by a pair of bitmaps to another bitmap |
| bitmap_bitremap | Apply map defined by a pair of bitmaps to a single bit |
| bitmap_find_free_region | find a contiguous aligned mem region |
| bitmap_release_region | release allocated bitmap region |
| bitmap_allocate_region | allocate bitmap region |
| bitmap_copy_le | copy a bitmap, putting the bits into little-endian order. |
| bitmap_from_arr32 | copy the contents of u32 array of bits to bitmap |
| bitmap_to_arr32 | copy the contents of bitmap to a u32 array of bits |
| bitmap_print_to_buf | convert bitmap to list or hex format ASCII string |
| bitmap_pos_to_ord | find ordinal of set bit at given position in bitmap |
| bitmap_ord_to_pos | find position of n-th set bit in bitmap |
| bitmap_onto | translate one bitmap relative to another |
| bitmap_fold | fold larger bitmap into smaller, modulo specified size |
| bitmap_find_next_zero_area | find a contiguous aligned zero area |
| bitmap_or_equal | Check whether the or of two bitmaps is equal to a third |
| BITMAP_FROM_U64 | Represent u64 value in the format suitable for bitmap. |
| bitmap_from_u64 | Check and swap words within u64. |
| bitmap_get_value8 | get an 8-bit value within a memory region |
| bitmap_set_value8 | set an 8-bit value within a memory region |
| get_option | Parse integer from an option string |
| get_options | Parse a string into a list of integers |
| memparse | parse a string with mem suffixes into a number |
| sort_r | sort an array of elements |
| list_sort | sort a list |
| textsearch_register | register a textsearch module |
| textsearch_unregister | unregister a textsearch module |
| textsearch_find_continuous | search a pattern in continuous/linear data |
| textsearch_prepare | Prepare a search |
| textsearch_destroy | destroy a search configuration |
| textsearch_next | continue searching for a pattern |
| textsearch_find | start searching for a pattern |
| textsearch_get_pattern | return head of the pattern |
| textsearch_get_pattern_len | return length of the pattern |

## CRC and Math Functions in Linux

| 名称 | 描述 |
|---|---|
| crc4 | calculate the 4-bit crc of a value. |
| crc7_be | update the CRC7 for the data buffer |
| crc8_populate_msb | fill crc table for given polynomial in reverse bit order. |
| crc8_populate_lsb | fill crc table for given polynomial in regular bit order. |
| crc8 | calculate a crc8 over the given input data. |
| crc16 | compute the CRC-16 for the data buffer |
| crc32_le_generic | Calculate bitwise little-endian Ethernet AUTODIN II CRC32/CRC32C |
| crc32_generic_shift | Appendlen0 bytes to crc, in logarithmic time |
| crc32_be_generic | Calculate bitwise big-endian Ethernet AUTODIN II CRC32 |
| crc_ccitt | recompute the CRC (CRC-CCITT variant) for the data buffer |
| crc_ccitt_false | recompute the CRC (CRC-CCITT-FALSE variant) for the data buffer |
| crc_itu_t | Compute the CRC-ITU-T for the data buffer |
| is_power_of_2 | check if a value is a power of two |
| __roundup_pow_of_two | round up to nearest power of two |
| __rounddown_pow_of_two | round down to nearest power of two |
| const_ilog2 | log base 2 of 32-bit or a 64-bit constant unsigned value |
| ilog2 | log base 2 of 32-bit or a 64-bit unsigned value |
| roundup_pow_of_two | round the given value up to nearest power of two |
| rounddown_pow_of_two | round the given value down to nearest power of two |
| order_base_2 | calculate the (rounded up) base 2 order of the argument |
| bits_per | calculate the number of bits required for the argument |
| int_pow | computes the exponentiation of the given base and exponent |
| int_sqrt | computes the integer square root |
| int_sqrt64 | strongly typed int_sqrt function when minimum 64 bit input is expected. |
| do_div | returns 2 values: calculate remainder and update new dividend |
| div_u64_rem | unsigned 64bit divide with 32bit divisor with remainder |
| div_u64 | unsigned 64bit divide with 32bit divisor |
| div_s64 | signed 64bit divide with 32bit divisor |
| DIV64_U64_ROUND_CLOSEST | unsigned 64bit divide with 64bit divisor rounded to nearest integer |
| div_s64_rem | signed 64bit divide with 64bit divisor and remainder |
| div64_u64_rem | unsigned 64bit divide with 64bit divisor and remainder |
| div64_u64 | unsigned 64bit divide with 64bit divisor |
| div64_s64 | signed 64bit divide with 64bit divisor |
| gcd | calculate and return the greatest common divisor of 2 unsigned longs |
| generate_random_uuid | generate a random UUID |
| uuid_is_valid | checks if a UUID string is valid |

## Kernel IPC facilities

| 名称 | 描述 |
|---|---|
| ipc_init | initialise ipc subsystem |
| ipc_init_ids | initialise ipc identifiers |
| ipc_init_proc_interface | create a proc interface for sysipc types using a seq_file interface. |
| ipc_findkey | find a key in an ipc identifier set |
| ipc_addid | add an ipc identifier |
| ipcget_new | create a new ipc object |
| ipc_check_perms | check security and permissions for an ipc object |
| ipcget_public | get an ipc object or create a new one |
| ipc_kht_remove | remove an ipc from the key hashtable |
| ipc_search_maxidx | search for the highest assigned index |
| ipc_rmid | remove an ipc identifier |
| ipc_set_key_private | switch the key of an existing ipc to IPC_PRIVATE |
| ipcperms | check ipc permissions |
| kernel_to_ipc64_perm | convert kernel ipc permissions to user |
| ipc64_perm_to_ipc_perm | convert new ipc permissions to old |
| ipc_obtain_object_idr |  |
| ipc_obtain_object_check |  |
| ipcget | Common sys_*get() code |
| ipc_update_perm | update the permissions of an ipc object |
| ipcctl_obtain_check | retrieve an ipc object and check permissions |
| ipc_parse_version | ipc call version |

## FIFO Buffer

| 名称 | 描述 |
|---|---|
| DECLARE_KFIFO_PTR | macro to declare a fifo pointer object |
| DECLARE_KFIFO | macro to declare a fifo object |
| INIT_KFIFO | Initialize a fifo declared by DECLARE_KFIFO |
| DEFINE_KFIFO | macro to define and initialize a fifo |
| kfifo_initialized | Check if the fifo is initialized |
| kfifo_esize | returns the size of the element managed by the fifo |
| kfifo_recsize | returns the size of the record length field |
| kfifo_size | returns the size of the fifo in elements |
| kfifo_reset | removes the entire fifo content |
| kfifo_reset_out | skip fifo content |
| kfifo_len | returns the number of used elements in the fifo |
| kfifo_is_empty | returns true if the fifo is empty |
| kfifo_is_empty_spinlocked | returns true if the fifo is empty using a spinlock for locking |
| kfifo_is_empty_spinlocked_noirqsave | returns true if the fifo is empty using a spinlock for locking, doesn’t disable interrupts |
| kfifo_is_full | returns true if the fifo is full |
| kfifo_avail | returns the number of unused elements in the fifo |
| kfifo_skip | skip output data |
| kfifo_peek_len | gets the size of the next fifo record |
| kfifo_alloc | dynamically allocates a new fifo buffer |
| kfifo_free | frees the fifo |
| kfifo_init | initialize a fifo using a preallocated buffer |
| kfifo_put | put data into the fifo |
| kfifo_get | get data from the fifo |
| kfifo_peek | get data from the fifo without removing |
| kfifo_in | put data into the fifo |
| kfifo_in_spinlocked | put data into the fifo using a spinlock for locking |
| kfifo_in_spinlocked_noirqsave | put data into fifo using a spinlock for locking, don’t disable interrupts |
| kfifo_out | get data from the fifo |
| kfifo_out_spinlocked | get data from the fifo using a spinlock for locking |
| kfifo_out_spinlocked_noirqsave | get data from the fifo using a spinlock for locking, don’t disable interrupts |
| kfifo_from_user | puts some data from user space into the fifo |
| kfifo_to_user | copies data from the fifo into user space |
| kfifo_dma_in_prepare | setup a scatterlist for DMA input |
| kfifo_dma_in_finish | finish a DMA IN operation |
| kfifo_dma_out_prepare | setup a scatterlist for DMA output |
| kfifo_dma_out_finish | finish a DMA OUT operation |
| kfifo_out_peek | gets some data from the fifo |

## relay interface support

| 名称 | 描述 |
|---|---|
| relay_buf_full | boolean, is the channel buffer full? |
| relay_reset | reset the channel |
| relay_open | create a new relay channel |
| relay_late_setup_files | triggers file creation |
| relay_switch_subbuf | switch to a new sub-buffer |
| relay_subbufs_consumed | update the buffer’s sub-buffers-consumed count |
| relay_close | close the channel |
| relay_flush | close the channel |
| relay_mmap_buf | mmap channel buffer to process address space |
| relay_alloc_buf | allocate a channel buffer |
| relay_create_buf | allocate and initialize a channel buffer |
| relay_destroy_channel | free the channel struct |
| relay_destroy_buf | destroy an rchan_buf struct and associated buffer |
| relay_remove_buf | remove a channel buffer |
| relay_buf_empty | boolean, is the channel buffer empty? |
| wakeup_readers | wake up readers waiting on a channel |
| __relay_reset | reset a channel buffer |
| relay_close_buf | close a channel buffer |
| relay_file_open | open file op for relay files |
| relay_file_mmap | mmap file op for relay files |
| relay_file_poll | poll file op for relay files |
| relay_file_release | release file op for relay files |
| relay_file_read_subbuf_avail | return bytes available in sub-buffer |
| relay_file_read_start_pos | find the first available byte to read |
| relay_file_read_end_pos | return the new read position |

## Module Support

| 名称 | 描述 |
|---|---|
| __request_module | try to load a kernel module |

## Hardware Interfaces

| 名称 | 描述 |
|---|---|
| request_dma | request and reserve a system DMA channel |
| free_dma | free a reserved system DMA channel |
| request_resource_conflict | request and reserve an I/O or memory resource |
| find_next_iomem_res | Finds the lowest iomem resource that covers part of [start..**end**]. |
| reallocate_resource | allocate a slot in the resource tree given range & alignment. The resource will be relocated if the new size cannot be reallocated in the current location. |
| lookup_resource | find an existing resource by a resource start address |
| insert_resource_conflict | Inserts resource in the resource tree |
| insert_resource_expand_to_fit | Insert a resource into the resource tree |
| resource_alignment | calculate resource’s alignment |
| release_mem_region_adjustable | release a previously reserved memory region |
| merge_system_ram_resource | mark the System RAM resource mergeable and try to merge it with adjacent, mergeable resources |
| request_resource | request and reserve an I/O or memory resource |
| release_resource | release a previously reserved resource |
| walk_iomem_res_desc | Walks through iomem resources and calls func() with matching resource ranges. * |
| region_intersects | determine intersection of region with known resources |
| allocate_resource | allocate empty slot in the resource tree given range & alignment. The resource will be reallocated with a new size if it was already allocated |
| insert_resource | Inserts a resource in the resource tree |
| remove_resource | Remove a resource in the resource tree |
| adjust_resource | modify a resource’s start and size |
| __request_region | create a new busy resource region |
| __release_region | release a previously reserved resource region |
| devm_request_resource | request and reserve an I/O or memory resource |
| devm_release_resource | release a previously requested resource |
| devm_request_free_mem_region | find free region for device private memory |
| arch_phys_wc_add | add a WC MTRR and handle errors if PAT is unavailable |

## Security Framework

| 名称 | 描述 |
|---|---|
| security_init | initializes the security framework |
| security_add_hooks | Add a modules hooks to the hook lists. |
| lsm_cred_alloc | allocate a composite cred blob |
| lsm_early_cred | during initialization allocate a composite cred blob |
| lsm_file_alloc | allocate a composite file blob |
| lsm_inode_alloc | allocate a composite inode blob |
| lsm_task_alloc | allocate a composite task blob |
| lsm_ipc_alloc | allocate a composite ipc blob |
| lsm_msg_msg_alloc | allocate a composite msg_msg blob |
| lsm_early_task | during initialization allocate a composite task blob |
| lsm_superblock_alloc | allocate a composite superblock blob |
| securityfs_create_file | create a file in the securityfs filesystem |
| securityfs_create_dir | create a directory in the securityfs filesystem |
| securityfs_create_symlink | create a symlink in the securityfs filesystem |
| securityfs_remove | removes a file or directory from the securityfs filesystem |

## Audit Interfaces

| 名称 | 描述 |
|---|---|
| audit_log_start | obtain an audit buffer |
| audit_log_format | format a message into the audit buffer. |
| audit_log_end | end one audit record |
| audit_log | Log an audit record |
| audit_alloc | allocate an audit context block for a task |
| __audit_free | free a per-task audit context |
| __audit_syscall_entry | fill in an audit record at syscall entry |
| __audit_syscall_exit | deallocate audit context after a system call |
| __audit_reusename | fill out filename with info from existing entry |
| __audit_getname | add a name to the list |
| __audit_inode | store the inode and device from a lookup |
| auditsc_get_stamp | get local copies of audit_context values |
| __audit_mq_open | record audit data for a POSIX MQ open |
| __audit_mq_sendrecv | record audit data for a POSIX MQ timed send/receive |
| __audit_mq_notify | record audit data for a POSIX MQ notify |
| __audit_mq_getsetattr | record audit data for a POSIX MQ get/set attribute |
| __audit_ipc_obj | record audit data for ipc object |
| __audit_ipc_set_perm | record audit data for new ipc permissions |
| __audit_socketcall | record audit data for sys_socketcall |
| __audit_fd_pair | record audit data for pipe and socketpair |
| __audit_sockaddr | record audit data for sys_bind, sys_connect, sys_sendto |
| audit_signal_info_syscall | record signal info for syscalls |
| __audit_log_bprm_fcaps | store information about a loading bprm and relevant fcaps |
| __audit_log_capset | store information about the arguments to the capset syscall |
| audit_core_dumps | record information about processes that end abnormally |
| audit_seccomp | record information about a seccomp action |
| audit_rule_change | apply all rules to the specified message type |
| audit_list_rules_send | list the audit rules |
| parent_len | find the length of the parent portion of a pathname |
| audit_compare_dname_path | compare given dentry name with last component in given path. Return of 0 indicates a match. |

## Accounting Framework

| 名称 | 描述 |
|---|---|
| sys_acct | enable/disable process accounting |
| acct_collect | collect accounting information into pacct_struct |
| acct_process | handles process accounting for an exiting task |

## Block Devices

| 名称 | 描述 |
|---|---|
| blk_queue_flag_set | atomically set a queue flag |
| blk_queue_flag_clear | atomically clear a queue flag |
| blk_queue_flag_test_and_set | atomically test and set a queue flag |
| blk_op_str | Return string XXX in the REQ_OP_XXX. |
| blk_sync_queue | cancel any pending callbacks on a queue |
| blk_set_pm_only | increment pm_only counter |
| blk_put_queue | decrement the request_queue refcount |
| blk_cleanup_queue | shutdown a request queue |
| blk_get_queue | increment the request_queue refcount |
| blk_get_request | allocate a request |
| submit_bio_noacct | re-submit a bio to the block device layer for I/O |
| submit_bio | submit a bio to the block device layer for I/O |
| blk_insert_cloned_request | Helper for stacking drivers to submit a request |
| blk_rq_err_bytes | determine number of bytes till the next failure boundary |
| bio_start_io_acct | start I/O accounting for bio based drivers |
| blk_update_request | Complete multiple bytes without completing the request |
| rq_flush_dcache_pages | Helper function to flush all pages in a request |
| blk_lld_busy | Check if underlying low-level drivers of a device are busy |
| blk_rq_unprep_clone | Helper function to free all bios in a cloned request |
| blk_rq_prep_clone | Helper function to setup clone request |
| blk_start_plug | initialize blk_plug and track it inside the task_struct |
| blk_finish_plug | mark the end of a batch of submitted I/O |
| blk_queue_enter | try to increase q->q_usage_counter |
| blk_cloned_rq_check_limits | Helper function to check a cloned request for the new queue limits |
| blk_rq_map_user_iov | map user data to a request, for passthrough requests |
| blk_rq_unmap_user | unmap a request with user data |
| blk_rq_map_kern | map kernel data to a request, for passthrough requests |
| blk_release_queue | releases all allocated resources of the request_queue |
| blk_register_queue | register a block layer queue with sysfs |
| blk_unregister_queue | counterpart ofblk_register_queue() |
| blk_set_default_limits | reset limits to default values |
| blk_set_stacking_limits | set default limits for stacking devices |
| blk_queue_bounce_limit | set bounce buffer limit for queue |
| blk_queue_max_hw_sectors | set max sectors for a request for this queue |
| blk_queue_chunk_sectors | set size of the chunk for this queue |
| blk_queue_max_discard_sectors | set max sectors for a single discard |
| blk_queue_max_write_same_sectors | set max sectors for a single write same |
| blk_queue_max_write_zeroes_sectors | set max sectors for a single write zeroes |
| blk_queue_max_zone_append_sectors | set max sectors for a single zone append |
| blk_queue_max_segments | set max hw segments for a request for this queue |
| blk_queue_max_discard_segments | set max segments for discard requests |
| blk_queue_max_segment_size | set max segment size for blk_rq_map_sg |
| blk_queue_logical_block_size | set logical block size for the queue |
| blk_queue_physical_block_size | set physical block size for the queue |
| blk_queue_zone_write_granularity | set zone write granularity for the queue |
| blk_queue_alignment_offset | set physical block alignment offset |
| blk_limits_io_min | set minimum request size for a device |
| blk_queue_io_min | set minimum request size for the queue |
| blk_limits_io_opt | set optimal request size for a device |
| blk_queue_io_opt | set optimal request size for the queue |
| blk_stack_limits | adjust queue_limits for stacked devices |
| disk_stack_limits | adjust queue limits for stacked drivers |
| blk_queue_update_dma_pad | update pad mask |
| blk_queue_segment_boundary | set boundary rules for segment merging |
| blk_queue_virt_boundary | set boundary rules for bio merging |
| blk_queue_dma_alignment | set dma length and memory alignment |
| blk_queue_update_dma_alignment | update dma length and memory alignment |
| blk_set_queue_depth | tell the block layer about the device queue depth |
| blk_queue_write_cache | configure queue’s write cache |
| blk_queue_required_elevator_features | Set a queue required elevator features |
| blk_queue_can_use_dma_map_merging | configure queue for merging segments. |
| blk_queue_set_zoned | configure a disk queue zoned model. |
| blk_execute_rq_nowait | insert a request to I/O scheduler for execution |
| blk_execute_rq | insert a request into queue for execution |
| blkdev_issue_flush | queue a flush |
| blkdev_issue_discard | queue a discard |
| blkdev_issue_write_same | queue a write same operation |
| __blkdev_issue_zeroout | generate number of zero filed write bios |
| blkdev_issue_zeroout | zero-fill a block range |
| blk_rq_count_integrity_sg | Count number of integrity scatterlist elements |
| blk_rq_map_integrity_sg | Map integrity metadata into a scatterlist |
| blk_integrity_compare | Compare integrity profile of two disks |
| blk_integrity_register | Register a gendisk as being integrity-capable |
| blk_integrity_unregister | Unregister block integrity profile |
| blk_trace_ioctl | handle the ioctls associated with tracing |
| blk_trace_shutdown | stop and cleanup trace structures |
| blk_add_trace_rq | Add a trace for a request oriented action |
| blk_add_trace_bio | Add a trace for a bio oriented action |
| blk_add_trace_bio_remap | Add a trace for a bio-remap operation |
| blk_add_trace_rq_remap | Add a trace for a request-remap operation |
| disk_release | releases all allocated resources of the gendisk |
| __register_blkdev | register a new block device |
| device_add_disk | add disk information to kernel list |
| del_gendisk | remove the gendisk |
| put_disk | decrements the gendisk refcount |
| blk_cleanup_disk | shutdown a gendisk allocated by blk_alloc_disk |
| set_disk_ro | set a gendisk read-only |
| freeze_bdev | lock a filesystem and force it into a consistent state |
| thaw_bdev | unlock filesystem |
| bd_prepare_to_claim | claim a block device |
| bd_abort_claiming | abort claiming of a block device |
| blkdev_get_by_dev | open a block device by device number |
| blkdev_get_by_path | open a block device by name |
| lookup_bdev | lookup a struct block_device by name |

## Char devices

| 名称 | 描述 |
|---|---|
| register_chrdev_region | register a range of device numbers |
| alloc_chrdev_region | register a range of char device numbers |
| __register_chrdev | create and register a cdev occupying a range of minors |
| unregister_chrdev_region | unregister a range of device numbers |
| __unregister_chrdev | unregister and destroy a cdev |
| cdev_add | add a char device to the system |
| cdev_set_parent | set the parent kobject for a char device |
| cdev_device_add | add a char device and it’s correspondingstructdevice, linkink |
| cdev_device_del | inverse of cdev_device_add |
| cdev_del | remove a cdev from the system |
| cdev_alloc | allocate a cdev structure |
| cdev_init | initialize a cdev structure |

## Clock Framework

| 名称 | 描述 |
|---|---|
| struct clk_notifier | associate a clk with a notifier |
| struct clk_notifier_data | rate data to pass to the notifier callback |
| struct clk_bulk_data | Data used for bulk clk operations. |
| clk_notifier_register | register a clock rate-change notifier callback |
| clk_notifier_unregister | unregister a clock rate-change notifier callback |
| devm_clk_notifier_register | register a managed rate-change notifier callback |
| clk_get_accuracy | obtain the clock accuracy in ppb (parts per billion) for a clock source. |
| clk_set_phase | adjust the phase shift of a clock signal |
| clk_get_phase | return the phase shift of a clock signal |
| clk_set_duty_cycle | adjust the duty cycle ratio of a clock signal |
| clk_get_scaled_duty_cycle | return the duty cycle ratio of a clock signal |
| clk_is_match | check if two clk’s point to the same hardware clock |
| clk_prepare | prepare a clock source |
| clk_is_enabled_when_prepared | indicate if preparing a clock also enables it. |
| clk_unprepare | undo preparation of a clock source |
| clk_get | lookup and obtain a reference to a clock producer. |
| clk_bulk_get | lookup and obtain a number of references to clock producer. |
| clk_bulk_get_all | lookup and obtain all available references to clock producer. |
| clk_bulk_get_optional | lookup and obtain a number of references to clock producer |
| devm_clk_bulk_get | managed get multiple clk consumers |
| devm_clk_bulk_get_optional | managed get multiple optional consumer clocks |
| devm_clk_bulk_get_all | managed get multiple clk consumers |
| devm_clk_get | lookup and obtain a managed reference to a clock producer. |
| devm_clk_get_optional | lookup and obtain a managed reference to an optional clock producer. |
| devm_get_clk_from_child | lookup and obtain a managed reference to a clock producer from child node. |
| clk_rate_exclusive_get | get exclusivity over the rate control of a producer |
| clk_rate_exclusive_put | release exclusivity over the rate control of a producer |
| clk_enable | inform the system when the clock source should be running. |
| clk_bulk_enable | inform the system when the set of clks should be running. |
| clk_disable | inform the system when the clock source is no longer required. |
| clk_bulk_disable | inform the system when the set of clks is no longer required. |
| clk_get_rate | obtain the current clock rate (in Hz) for a clock source. This is only valid once the clock source has been enabled. |
| clk_put | “free” the clock source |
| clk_bulk_put | “free” the clock source |
| clk_bulk_put_all | “free” all the clock source |
| devm_clk_put | “free” a managed clock source |
| clk_round_rate | adjust a rate to the exact rate a clock can provide |
| clk_set_rate | set the clock rate for a clock source |
| clk_set_rate_exclusive | set the clock rate and claim exclusivity over clock source |
| clk_has_parent | check if a clock is a possible parent for another |
| clk_set_rate_range | set a rate range for a clock source |
| clk_set_min_rate | set a minimum clock rate for a clock source |
| clk_set_max_rate | set a maximum clock rate for a clock source |
| clk_set_parent | set the parent clock source for this clock |
| clk_get_parent | get the parent clock source for this clock |
| clk_get_sys | get a clock based upon the device name |
| clk_save_context | save clock context for poweroff |
| clk_restore_context | restore clock context after poweroff |
| clk_get_optional | lookup and obtain a reference to an optional clock producer. |

## Synchronization Primitives

| 名称 | 描述 |
|---|---|
| RCU_NONIDLE | Indicate idle-loop code that needs RCU readers |
| cond_resched_tasks_rcu_qs | Report potential quiescent states to RCU |
| RCU_LOCKDEP_WARN | emit lockdep splat if specified condition is met |
| unrcu_pointer | mark a pointer as not being RCU protected |
| RCU_INITIALIZER | statically initialize an RCU-protected global variable |
| rcu_assign_pointer | assign to RCU-protected pointer |
| rcu_replace_pointer | replace an RCU pointer, returning its old value |
| rcu_access_pointer | fetch RCU pointer with no dereferencing |
| rcu_dereference_check | rcu_dereference with debug checking |
| rcu_dereference_bh_check | rcu_dereference_bh with debug checking |
| rcu_dereference_sched_check | rcu_dereference_sched with debug checking |
| rcu_dereference_protected | fetch RCU pointer when updates prevented |
| rcu_dereference | fetch RCU-protected pointer for dereferencing |
| rcu_dereference_bh | fetch an RCU-bh-protected pointer for dereferencing |
| rcu_dereference_sched | fetch RCU-sched-protected pointer for dereferencing |
| rcu_pointer_handoff | Hand off a pointer from RCU to other mechanism |
| rcu_read_lock | mark the beginning of an RCU read-side critical section |
| rcu_read_unlock | marks the end of an RCU read-side critical section. |
| rcu_read_lock_bh | mark the beginning of an RCU-bh critical section |
| rcu_read_unlock_bh | marks the end of a softirq-only RCU critical section |
| rcu_read_lock_sched | mark the beginning of a RCU-sched critical section |
| rcu_read_unlock_sched | marks the end of a RCU-classic critical section |
| RCU_INIT_POINTER | initialize an RCU protected pointer |
| RCU_POINTER_INITIALIZER | statically initialize an RCU protected pointer |
| kfree_rcu | kfree an object after a grace period. |
| kvfree_rcu | kvfree an object after a grace period. |
| rcu_head_init | Initialize rcu_head forrcu_head_after_call_rcu() |
| rcu_head_after_call_rcu | Has this rcu_head been passed tocall_rcu()? |
| rcu_is_cpu_rrupt_from_idle | see if ‘interrupted’ from idle |
| rcu_idle_enter | inform RCU that current CPU is entering idle |
| rcu_user_enter | inform RCU that we are resuming userspace. |
| rcu_nmi_exit | inform RCU of exit from NMI context |
| rcu_irq_exit | inform RCU that current CPU is exiting irq towards idle |
| rcu_irq_exit_check_preempt | Validate that scheduling is possible |
| rcu_idle_exit | inform RCU that current CPU is leaving idle |
| rcu_user_exit | inform RCU that we are exiting userspace. |
| __rcu_irq_enter_check_tick | Enable scheduler tick on CPU if RCU needs it. |
| rcu_nmi_enter | inform RCU of entry to NMI context |
| rcu_irq_enter | inform RCU that current CPU is entering irq away from idle |
| rcu_is_watching | see if RCU thinks that the current CPU is not idle |
| call_rcu | Queue an RCU callback for invocation after a grace period. |
| struct kvfree_rcu_bulk_data | single block to storekvfree_rcu()pointers |
| struct kfree_rcu_cpu_work | single batch ofkfree_rcu()requests |
| struct kfree_rcu_cpu | batch upkfree_rcu()requests for RCU grace period |
| synchronize_rcu | wait until a grace period has elapsed. |
| get_state_synchronize_rcu | Snapshot current RCU state |
| start_poll_synchronize_rcu | Snapshot and start RCU grace period |
| poll_state_synchronize_rcu | Conditionally wait for an RCU grace period |
| cond_synchronize_rcu | Conditionally wait for an RCU grace period |
| rcu_barrier | Wait until all in-flightcall_rcu()callbacks complete. |
| synchronize_rcu_expedited | Brute-force RCU grace period |
| rcu_read_lock_held_common | might we be in RCU-sched read-side critical section? |
| rcu_expedite_gp | Expedite future RCU grace periods |
| rcu_unexpedite_gp | Cancel priorrcu_expedite_gp()invocation |
| rcu_read_lock_held | might we be in RCU read-side critical section? |
| rcu_read_lock_bh_held | might we be in RCU-bh read-side critical section? |
| wakeme_after_rcu | Callback function to awaken a task after grace period |
| init_rcu_head_on_stack | initialize on-stack rcu_head for debugobjects |
| destroy_rcu_head_on_stack | destroy on-stack rcu_head for debugobjects |
| srcu_read_lock_held | might we be in SRCU read-side critical section? |
| srcu_dereference_check | fetch SRCU-protected pointer for later dereferencing |
| srcu_dereference | fetch SRCU-protected pointer for later dereferencing |
| srcu_dereference_notrace | no tracing and no lockdep calls from here |
| srcu_read_lock | register a new reader for an SRCU-protected structure. |
| srcu_read_unlock | unregister a old reader from an SRCU-protected structure. |
| smp_mb__after_srcu_read_unlock | ensure full ordering after srcu_read_unlock |
| init_srcu_struct | initialize a sleep-RCU structure |
| srcu_readers_active | returns true if there are readers. and false otherwise |
| cleanup_srcu_struct | deconstruct a sleep-RCU structure |
| call_srcu | Queue a callback for invocation after an SRCU grace period |
| synchronize_srcu_expedited | Brute-force SRCU grace period |
| synchronize_srcu | wait for prior SRCU read-side critical-section completion |
| get_state_synchronize_srcu | Provide an end-of-grace-period cookie |
| start_poll_synchronize_srcu | Provide cookie and start grace period |
| poll_state_synchronize_srcu | Has cookie’s grace period ended? |
| srcu_barrier | Wait until all in-flightcall_srcu()callbacks complete. |
| srcu_batches_completed | return batches completed. |
| hlist_bl_del_rcu | deletes entry from hash list without re-initialization |
| hlist_bl_add_head_rcu |  |
| hlist_bl_for_each_entry_rcu | iterate over rcu list of given type |
| list_tail_rcu | returns the prev pointer of the head of the list |
| list_add_rcu | add a new entry to rcu-protected list |
| list_add_tail_rcu | add a new entry to rcu-protected list |
| list_del_rcu | deletes entry from list without re-initialization |
| hlist_del_init_rcu | deletes entry from hash list with re-initialization |
| list_replace_rcu | replace old entry by new one |
| __list_splice_init_rcu | join an RCU-protected list into an existing list. |
| list_splice_init_rcu | splice an RCU-protected list into an existing list, designed for stacks. |
| list_splice_tail_init_rcu | splice an RCU-protected list into an existing list, designed for queues. |
| list_entry_rcu | get the struct for this entry |
| list_first_or_null_rcu | get the first element from a list |
| list_next_or_null_rcu | get the first element from a list |
| list_for_each_entry_rcu | iterate over rcu list of given type |
| list_for_each_entry_srcu | iterate over rcu list of given type |
| list_entry_lockless | get the struct for this entry |
| list_for_each_entry_lockless | iterate over rcu list of given type |
| list_for_each_entry_continue_rcu | continue iteration over list of given type |
| list_for_each_entry_from_rcu | iterate over a list from current point |
| hlist_del_rcu | deletes entry from hash list without re-initialization |
| hlist_replace_rcu | replace old entry by new one |
| hlists_swap_heads_rcu | swap the lists the hlist heads point to |
| hlist_add_head_rcu |  |
| hlist_add_tail_rcu |  |
| hlist_add_before_rcu |  |
| hlist_add_behind_rcu |  |
| hlist_for_each_entry_rcu | iterate over rcu list of given type |
| hlist_for_each_entry_srcu | iterate over rcu list of given type |
| hlist_for_each_entry_rcu_notrace | iterate over rcu list of given type (for tracing) |
| hlist_for_each_entry_rcu_bh | iterate over rcu list of given type |
| hlist_for_each_entry_continue_rcu | iterate over a hlist continuing after current point |
| hlist_for_each_entry_continue_rcu_bh | iterate over a hlist continuing after current point |
| hlist_for_each_entry_from_rcu | iterate over a hlist continuing from current point |
| hlist_nulls_del_init_rcu | deletes entry from hash list with re-initialization |
| hlist_nulls_first_rcu | returns the first element of the hash list. |
| hlist_nulls_next_rcu | returns the element of the list afternode. |
| hlist_nulls_del_rcu | deletes entry from hash list without re-initialization |
| hlist_nulls_add_head_rcu |  |
| hlist_nulls_add_tail_rcu |  |
| hlist_nulls_for_each_entry_rcu | iterate over rcu list of given type |
| hlist_nulls_for_each_entry_safe | iterate over list of given type safe against removal of list entry |
| rcu_sync_is_idle | Are readers permitted to use their fastpaths? |
| rcu_sync_init | Initialize an rcu_sync structure |
| rcu_sync_enter_start | Force readers onto slow path for multiple updates |
| rcu_sync_func | Callback function managing reader access to fastpath |
| rcu_sync_enter | Force readers onto slowpath |
| rcu_sync_exit | Allow readers back onto fast path after grace period |
| rcu_sync_dtor | Clean up an rcu_sync structure |

## 总数统计

总共提取了 683 个函数+结构。
