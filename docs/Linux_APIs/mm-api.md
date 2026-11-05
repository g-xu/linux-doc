# Kernel Module mm-api.md API Documentation

## Memory Management APIs¶

## User Space Memory Access¶

| 名称 | 描述 |
|---|---|
| access_ok | Checks if a user space pointer is valid |
| get_user | Get a simple variable from user space. |
| __get_user | Get a simple variable from user space, with less checking. |
| put_user | Write a simple value into user space. |
| __put_user | Write a simple value into user space, with less checking. |
| clear_user | Zero a block of memory in user space. |
| __clear_user | Zero a block of memory in user space, with less checking. |
| get_user_pages_fast | pin user pages in memory |

## Memory Allocation Controls¶

| 名称 | 描述 |
|---|---|
| struct gfp_t | Memory allocation flags. |
| gfpflags_normal_context | is gfp_flags a normal sleepable context? |

## The Slab Cache¶

| 名称                         | 描述                                                                                                                    |
| -------------------------- | --------------------------------------------------------------------------------------------------------------------- |
| kmalloc                    | allocate memory                                                                                                       |
| kmalloc_array              | allocate memory for an array.                                                                                         |
| krealloc_array             | reallocate memory for an array.                                                                                       |
| kcalloc                    | allocate memory for an array. The memory is set to zero.                                                              |
| kzalloc                    | allocate memory. The memory is set to zero.                                                                           |
| kzalloc_node               | allocate zeroed memory from a particular memory node.                                                                 |
| kmem_cache_alloc           | Allocate an object                                                                                                    |
| kmem_cache_alloc_node      | Allocate an object on the specified node                                                                              |
| kmem_cache_free            | Deallocate an object                                                                                                  |
| kfree                      | free previously allocated memory                                                                                      |
| __ksize                    | Uninstrumented ksize.                                                                                                 |
| kmem_cache_create_usercopy | Create a cache with a region suitable for copying to userspace                                                        |
| kmem_cache_create          | Create a cache.                                                                                                       |
| kmem_cache_shrink          | Shrink a cache.                                                                                                       |
| kmem_valid_obj             | does the pointer reference a valid slab object?                                                                       |
| kmem_dump_obj              | Print available slab provenance information                                                                           |
| krealloc                   | reallocate memory. The contents will remain unchanged.                                                                |
| kfree_sensitive            | Clear sensitive information in memory before freeing                                                                  |
| ksize                      | get the actual amount of memory allocated for a given object                                                          |
| kfree_const                | conditionally free memory                                                                                             |
| kvmalloc_node              | attempt to allocate physically contiguous memory, but upon failure, fall back to non-contiguous (vmalloc) allocation. |
| kvfree                     | Free memory.                                                                                                          |

## Virtually Contiguous Mappings¶

| 名称 | 描述 |
|---|---|
| vm_unmap_aliases | unmap outstanding lazy aliases in the vmap layer |
| vm_unmap_ram | unmap linear kernel address space set up by vm_map_ram |
| vm_map_ram | map pages linearly into kernel virtual address (vmalloc space) |
| vfree | Release memory allocated byvmalloc() |
| vunmap | release virtual mapping obtained byvmap() |
| vmap | map an array of pages into virtually contiguous space |
| vmap_pfn | map an array of PFNs into virtually contiguous space |
| __vmalloc_node | allocate virtually contiguous memory |
| vmalloc | allocate virtually contiguous memory |
| vmalloc_no_huge | allocate virtually contiguous memory using small pages |
| vzalloc | allocate virtually contiguous memory with zero fill |
| vmalloc_user | allocate zeroed virtually contiguous memory for userspace |
| vmalloc_node | allocate memory on a specific node |
| vzalloc_node | allocate memory on a specific node with zero fill |
| vmalloc_32 | allocate virtually contiguous memory (32bit addressable) |
| vmalloc_32_user | allocate zeroed virtually contiguous 32bit memory |
| remap_vmalloc_range | map vmalloc pages to userspace |

## File Mapping and Page Cache¶

| 名称 | 描述 |
|---|---|
| read_cache_pages | populate an address space with some pages & start reads against them |
| page_cache_ra_unbounded | Start unchecked readahead. |
| readahead_expand | Expand a readahead request |
| delete_from_page_cache | delete page from page cache |
| filemap_fdatawrite_wbc | start writeback on mapping dirty pages in range |
| filemap_flush | mostly a non-blocking flush |
| filemap_range_has_page | check if a page exists in range. |
| filemap_fdatawait_range | wait for writeback to complete |
| filemap_fdatawait_range_keep_errors | wait for writeback to complete |
| file_fdatawait_range | wait for writeback to complete |
| filemap_fdatawait_keep_errors | wait for writeback without clearing errors |
| filemap_range_needs_writeback | check if range potentially needs writeback |
| filemap_write_and_wait_range | write out & wait on a file range |
| file_check_and_advance_wb_err | report wb error (if any) that was previously and advance wb_err to current one |
| file_write_and_wait_range | write out & wait on a file range |
| replace_page_cache_page | replace a pagecache page with a new one |
| add_to_page_cache_locked | add a locked page to the pagecache |
| add_page_wait_queue | Add an arbitrary waiter to a page’s wait queue |
| unlock_page | unlock a locked page |
| end_page_private_2 | Clear PG_private_2 and release any waiters |
| wait_on_page_private_2 | Wait for PG_private_2 to be cleared on a page |
| wait_on_page_private_2_killable | Wait for PG_private_2 to be cleared on a page |
| end_page_writeback | end writeback against a page |
| __lock_page | get a lock on the page, assuming we need to sleep to get it |
| page_cache_next_miss | Find the next gap in the page cache. |
| page_cache_prev_miss | Find the previous gap in the page cache. |
| pagecache_get_page | Find and get a reference to a page. |
| find_get_pages_contig | gang contiguous pagecache lookup |
| find_get_pages_range_tag | Find and return head pages matchingtag. |
| filemap_read | Read data from the page cache. |
| generic_file_read_iter | generic filesystem read routine |
| filemap_fault | read in file data for page fault handling |
| read_cache_page | read into page cache, fill it if needed |
| read_cache_page_gfp | read into page cache, using specified page allocation flags. |
| __generic_file_write_iter | write data to a file |
| generic_file_write_iter | write data to a file |
| try_to_release_page | release old fs-specific metadata on a page |
| balance_dirty_pages_ratelimited | balance dirty memory state |
| tag_pages_for_writeback | tag pages to be written by write_cache_pages |
| write_cache_pages | walk the list of dirty pages of the given address space and write all of them. |
| generic_writepages | walk the list of dirty pages of the given address space and writepage() all of them. |
| write_one_page | write out a single page and wait on I/O |
| wait_for_stable_page | wait for writeback to finish, if necessary. |
| truncate_inode_pages_range | truncate range of pages specified by start & end byte offsets |
| truncate_inode_pages | truncateallthe pages from an offset |
| truncate_inode_pages_final | truncateallpages before inode dies |
| invalidate_mapping_pages | Invalidate all clean, unlocked cache of one inode |
| invalidate_inode_pages2_range | remove range of pages from an address_space |
| invalidate_inode_pages2 | remove all pages from an address_space |
| truncate_pagecache | unmap and remove pagecache that has been truncated |
| truncate_setsize | update inode and pagecache for a new file size |
| pagecache_isize_extended | update pagecache after extension of i_size |
| truncate_pagecache_range | unmap and remove pagecache that is hole-punched |
| mapping_set_error | record a writeback error in the address_space |
| attach_page_private | Attach private data to a page. |
| detach_page_private | Detach private data from a page. |
| find_get_page | find and get a page reference |
| find_lock_page | locate, pin and lock a pagecache page |
| find_lock_head | Locate, pin and lock a pagecache page. |
| find_or_create_page | locate or add a pagecache page |
| grab_cache_page_nowait | returns locked page at given index in given cache |
| set_page_private_2 | Set PG_private_2 on a page and take a ref |
| struct readahead_control | Describes a readahead request. |
| page_cache_sync_readahead | generic file readahead |
| page_cache_async_readahead | file readahead for marked pages |
| readahead_page | Get the next page to read. |
| readahead_page_batch | Get a batch of pages to read. |
| readahead_pos | The byte offset into the file of this readahead request. |
| readahead_length | The number of bytes in this readahead request. |
| readahead_index | The index of the first page in this readahead request. |
| readahead_count | The number of pages in this readahead request. |
| readahead_batch_length | The number of bytes in the current batch. |
| page_mkwrite_check_truncate | check if page was truncated |
| i_blocks_per_page | How many blocks fit in this page. |

## Memory pools¶

| 名称 | 描述 |
|---|---|
| mempool_exit | exit a mempool initialized withmempool_init() |
| mempool_destroy | deallocate a memory pool |
| mempool_init | initialize a memory pool |
| mempool_create | create a memory pool |
| mempool_resize | resize an existing memory pool |
| mempool_alloc | allocate an element from a specific memory pool |
| mempool_free | return an element to the pool. |

## DMA pools¶

| 名称 | 描述 |
|---|---|
| dma_pool_create | Creates a pool of consistent memory blocks, for dma. |
| dma_pool_destroy | destroys a pool of dma memory blocks. |
| dma_pool_alloc | get a block of consistent memory |
| dma_pool_free | put block back into dma pool |
| dmam_pool_create | Manageddma_pool_create() |
| dmam_pool_destroy | Manageddma_pool_destroy() |

## More Memory Management Functions¶

| 名称 | 描述 |
|---|---|
| zap_vma_ptes | remove ptes mapping the vma |
| vm_insert_pages | insert multiple pages into user vma, batching the pmd lock. |
| vm_insert_page | insert single page into user vma |
| vm_map_pages | maps range of kernel pages starts with non zero offset |
| vm_map_pages_zero | map range of kernel pages starts with zero offset |
| vmf_insert_pfn_prot | insert single pfn into user vma with specified pgprot |
| vmf_insert_pfn | insert single pfn into user vma |
| vmf_insert_mixed_prot | insert single pfn into user vma with specified pgprot |
| remap_pfn_range | remap kernel memory to userspace |
| vm_iomap_memory | remap memory to userspace |
| unmap_mapping_pages | Unmap pages from processes. |
| unmap_mapping_range | unmap the portion of all mmaps in the specified address_space corresponding to the specified byte range in the underlying file. |
| follow_pte | look up PTE at a user virtual address |
| follow_pfn | look up PFN at a user virtual address |
| generic_access_phys | generic implementation for iomem mmap access |
| get_pfnblock_flags_mask | Return the requested group of flags for the pageblock_nr_pages block of pages |
| set_pfnblock_flags_mask | Set the requested group of flags for a pageblock_nr_pages block of pages |
| __putback_isolated_page | Return a now-isolated page back where we got it |
| __free_pages | Free pages allocated withalloc_pages(). |
| alloc_pages_exact | allocate an exact number physically-contiguous pages. |
| alloc_pages_exact_nid | allocate an exact number of physically-contiguous pages on a node. |
| free_pages_exact | release memory allocated viaalloc_pages_exact() |
| nr_free_zone_pages | count number of pages beyond high watermark |
| nr_free_buffer_pages | count number of pages beyond high watermark |
| find_next_best_node | find the next node that should appear in a given node’s fallback list |
| get_pfn_range_for_nid | Return the start and end page frames for a node |
| absent_pages_in_range | Return number of page frames in holes within a range |
| node_map_pfn_alignment | determine the maximum internode alignment |
| find_min_pfn_with_active_regions | Find the minimum PFN registered |
| free_area_init | Initialise all pg_data_t and zone data |
| set_dma_reserve | set the specified number of pages reserved in the first zone |
| setup_per_zone_wmarks | called when min_free_kbytes changes or when memory is hot-{added|removed} |
| alloc_contig_range | tries to allocate given range of pages |
| alloc_contig_pages | tries to find and allocate contiguous range of pages |
| numa_map_to_online_node | Find closest online node |
| alloc_pages_vma | Allocate a page for a VMA. |
| alloc_pages | Allocate pages. |
| mpol_misplaced | check whether current page node is valid in policy |
| mpol_shared_policy_init | initialize shared policy for inode |
| mpol_parse_str | parse string to mempolicy, for tmpfs mpol mount option. |
| mpol_to_str | format a mempolicy structure for printing |
| struct vm_fault_t | Return type for page fault handlers. |
| struct vm_fault_reason | Page fault handlers return a bitmask of these values to tell the core VM what happened when handling the fault. Used to decide whether a process gets delivered SIGBUS or just gets major/minor fault counters bumped up. |
| struct fault_flag | Fault flag definitions. |
| fault_flag_allow_retry_first | check ALLOW_RETRY the first time |
| page_maybe_dma_pinned | Report if a page is pinned for DMA. |
| find_vma_intersection | Look up the first VMA which intersects the interval |
| vma_lookup | Find a VMA at a specific address |
| vma_is_special_huge | Are transhuge page-table entries considered special? |
| seal_check_future_write | Check for F_SEAL_FUTURE_WRITE flag and handle it |
| is_highmem | helper function to quickly check if a struct zone is a highmem zone or not. This is an attempt to keep references to ZONE_{DMA/NORMAL/HIGHMEM/etc} in general code to a minimum. |
| for_each_online_pgdat | helper macro to iterate over all online nodes |
| for_each_zone | helper macro to iterate over all memory zones |
| next_zones_zonelist | Returns the next zone at or below highest_zoneidx within the allowed nodemask using a cursor within a zonelist as a starting point |
| first_zones_zonelist | Returns the first zone at or below highest_zoneidx within the allowed nodemask in a zonelist |
| for_each_zone_zonelist_nodemask | helper macro to iterate over valid zones in a zonelist at or below a given zone index and within a nodemask |
| for_each_zone_zonelist | helper macro to iterate over valid zones in a zonelist at or below a given zone index |
| pfn_valid | check if there is a valid memory map entry for a PFN |

## 总数统计

总共提取了 194 个函数+结构。
