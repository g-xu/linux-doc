# Kernel Module idr.md API Documentation

## ID Allocation¶

## Overview¶

## IDR usage¶

## IDA usage¶

## Functions and structures¶

| 名称 | 描述 |
|---|---|
| IDR_INIT | Initialise an IDR. |
| DEFINE_IDR | Define a statically-allocated IDR. |
| idr_get_cursor | Return the current position of the cyclic allocator |
| idr_set_cursor | Set the current position of the cyclic allocator |
| idr_init_base | Initialise an IDR. |
| idr_init | Initialise an IDR. |
| idr_is_empty | Are there any IDs allocated? |
| idr_preload_end | end preload section started with idr_preload() |
| idr_for_each_entry | Iterate over an IDR’s elements of a given type. |
| idr_for_each_entry_ul | Iterate over an IDR’s elements of a given type. |
| idr_for_each_entry_continue | Continue iteration over an IDR’s elements of a given type |
| idr_for_each_entry_continue_ul | Continue iteration over an IDR’s elements of a given type |
| ida_alloc | Allocate an unused ID. |
| ida_alloc_min | Allocate an unused ID. |
| ida_alloc_max | Allocate an unused ID. |
| idr_alloc_u32 | Allocate an ID. |
| idr_alloc | Allocate an ID. |
| idr_alloc_cyclic | Allocate an ID cyclically. |
| idr_remove | Remove an ID from the IDR. |
| idr_find | Return pointer for given ID. |
| idr_for_each | Iterate through all stored pointers. |
| idr_get_next_ul | Find next populated entry. |
| idr_get_next | Find next populated entry. |
| idr_replace | replace pointer for given ID. |
| ida_alloc_range | Allocate an unused ID. |
| ida_free | Release an allocated ID. |
| ida_destroy | Free all IDs. |

## 总数统计

总共提取了 27 个函数+结构。
