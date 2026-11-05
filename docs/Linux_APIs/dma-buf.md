# Kernel Module dma-buf.md API Documentation

## Buffer Sharing and Synchronization¶

## Shared DMA Buffers¶

| 名称 | 描述 |
|---|---|
| struct dma_buf_sync | Synchronize with CPU access. |
| dma_buf_export | Creates a new dma_buf, and associates an anon file with this buffer, so it can be exported. Also connect the allocator specific data and ops to the buffer. Additionally, provide a name string for exporter; useful in debugging. |
| dma_buf_fd | returns a file descriptor for the givenstructdma_buf |
| dma_buf_get | returns thestructdma_bufrelated to an fd |
| dma_buf_put | decreases refcount of the buffer |
| dma_buf_dynamic_attach | Add the device to dma_buf’s attachments list |
| dma_buf_attach | Wrapper for dma_buf_dynamic_attach |
| dma_buf_detach | Remove the given attachment from dmabuf’s attachments list |
| dma_buf_pin | Lock down the DMA-buf |
| dma_buf_unpin | Unpin a DMA-buf |
| dma_buf_map_attachment | Returns the scatterlist table of the attachment; mapped into _device_ address space. Is a wrapper for map_dma_buf() of the dma_buf_ops. |
| dma_buf_unmap_attachment | unmaps and decreases usecount of the buffer;might deallocate the scatterlist associated. Is a wrapper for unmap_dma_buf() of dma_buf_ops. |
| dma_buf_move_notify | notify attachments that DMA-buf is moving |
| dma_buf_begin_cpu_access | Must be called before accessing a dma_buf from the cpu in the kernel context. Calls begin_cpu_access to allow exporter-specific preparations. Coherency is only guaranteed in the specified range for the specified access direction. |
| dma_buf_end_cpu_access | Must be called after accessing a dma_buf from the cpu in the kernel context. Calls end_cpu_access to allow exporter-specific actions. Coherency is only guaranteed in the specified range for the specified access direction. |
| dma_buf_mmap | Setup up a userspace mmap with the given vma |
| dma_buf_vmap | Create virtual mapping for the buffer object into kernel address space. Same restrictions as for vmap and friends apply. |
| dma_buf_vunmap | Unmap a vmap obtained by dma_buf_vmap. |
| struct dma_buf_ops | operations possible onstructdma_buf |
| struct dma_buf | shared buffer object |
| struct dma_buf_attach_ops | importer operations for an attachment |
| struct dma_buf_attachment | holds device-buffer attachment data |
| struct dma_buf_export_info | holds information needed to export a dma_buf |
| DEFINE_DMA_BUF_EXPORT_INFO | helper macro for exporters |
| get_dma_buf | convenience wrapper for get_file. |
| dma_buf_is_dynamic | check if a DMA-buf uses dynamic mappings. |
| dma_buf_attachment_is_dynamic | check if a DMA-buf attachment uses dynamic mappings |
| struct dma_buf_map | Pointer to vmap’ed dma-buf memory. |
| DMA_BUF_MAP_INIT_VADDR | Initializesstructdma_buf_mapto an address in system memory |
| dma_buf_map_set_vaddr | Sets a dma-buf mapping structure to an address in system memory |
| dma_buf_map_set_vaddr_iomem | Sets a dma-buf mapping structure to an address in I/O memory |
| dma_buf_map_is_equal | Compares two dma-buf mapping structures for equality |
| dma_buf_map_is_null | Tests for a dma-buf mapping to be NULL |
| dma_buf_map_is_set | Tests is the dma-buf mapping has been set |
| dma_buf_map_clear | Clears a dma-buf mapping structure |
| dma_buf_map_memcpy_to | Memcpy into dma-buf mapping |
| dma_buf_map_incr | Increments the address stored in a dma-buf mapping |

## Reservation Objects¶

| 名称 | 描述 |
|---|---|
| dma_resv_init | initialize a reservation object |
| dma_resv_fini | destroys a reservation object |
| dma_resv_reserve_shared | Reserve space to add shared fences to a dma_resv. |
| dma_resv_reset_shared_max | reset shared fences for debugging |
| dma_resv_add_shared_fence | Add a fence to a shared slot |
| dma_resv_add_excl_fence | Add an exclusive fence. |
| dma_resv_copy_fences | Copy all fences from src to dst. |
| dma_resv_get_fences | Get an object’s shared and exclusive fences without update side lock held |
| dma_resv_wait_timeout | Wait on reservation’s objects shared and/or exclusive fences. |
| dma_resv_test_signaled | Test if a reservation object’s fences have been signaled. |
| struct dma_resv_list | a list of shared fences |
| struct dma_resv | a reservation object manages fences for a buffer |
| dma_resv_lock | lock the reservation object |
| dma_resv_lock_interruptible | lock the reservation object |
| dma_resv_lock_slow | slowpath lock the reservation object |
| dma_resv_lock_slow_interruptible | slowpath lock the reservation object, interruptible |
| dma_resv_trylock | trylock the reservation object |
| dma_resv_is_locked | is the reservation object locked |
| dma_resv_locking_ctx | returns the context used to lock the object |
| dma_resv_unlock | unlock the reservation object |
| dma_resv_excl_fence | return the object’s exclusive fence |
| dma_resv_get_excl_unlocked | get the reservation object’s exclusive fence, without lock held. |
| dma_resv_shared_list | get the reservation object’s shared fence list |

## DMA Fences¶

| 名称 | 描述 |
|---|---|
| dma_fence_get_stub | return a signaled fence |
| dma_fence_allocate_private_stub | return a private, signaled fence |
| dma_fence_context_alloc | allocate an array of fence contexts |
| dma_fence_begin_signalling | begin a critical DMA fence signalling section |
| dma_fence_end_signalling | end a critical DMA fence signalling section |
| dma_fence_signal_timestamp_locked | signal completion of a fence |
| dma_fence_signal_timestamp | signal completion of a fence |
| dma_fence_signal_locked | signal completion of a fence |
| dma_fence_signal | signal completion of a fence |
| dma_fence_wait_timeout | sleep until the fence gets signaled or until timeout elapses |
| dma_fence_release | default relese function for fences |
| dma_fence_free | default release function fordma_fence. |
| dma_fence_enable_sw_signaling | enable signaling on fence |
| dma_fence_add_callback | add a callback to be called when the fence is signaled |
| dma_fence_get_status | returns the status upon completion |
| dma_fence_remove_callback | remove a callback from the signaling list |
| dma_fence_default_wait | default sleep until the fence gets signaled or until timeout elapses |
| dma_fence_wait_any_timeout | sleep until any fence gets signaled or until timeout elapses |
| dma_fence_init | Initialize a custom fence. |
| struct dma_fence | software synchronization primitive |
| struct dma_fence_cb | callback fordma_fence_add_callback() |
| struct dma_fence_ops | operations implemented for fence |
| dma_fence_put | decreases refcount of the fence |
| dma_fence_get | increases refcount of the fence |
| dma_fence_get_rcu | get a fence from a dma_resv_list with rcu read lock |
| dma_fence_get_rcu_safe | acquire a reference to an RCU tracked fence |
| dma_fence_is_signaled_locked | Return an indication if the fence is signaled yet. |
| dma_fence_is_signaled | Return an indication if the fence is signaled yet. |
| __dma_fence_is_later | return if f1 is chronologically later than f2 |
| dma_fence_is_later | return if f1 is chronologically later than f2 |
| dma_fence_later | return the chronologically later fence |
| dma_fence_get_status_locked | returns the status upon completion |
| dma_fence_set_error | flag an error condition on the fence |
| dma_fence_wait | sleep until the fence gets signaled |
| to_seqno_fence | cast a fence to a seqno_fence |
| seqno_fence_init | initialize a seqno fence |
| dma_fence_array_create | Create a custom fence array |
| dma_fence_match_context | Check if all fences are from the given context |
| struct dma_fence_array_cb | callback helper for fence array |
| struct dma_fence_array | fence to represent an array of fences |
| dma_fence_is_array | check if a fence is from the array subsclass |
| to_dma_fence_array | cast a fence to a dma_fence_array |
| dma_fence_chain_walk | chain walking function |
| dma_fence_chain_find_seqno | find fence chain node by seqno |
| dma_fence_chain_init | initialize a fence chain |
| struct dma_fence_chain | fence to represent an node of a fence chain |
| to_dma_fence_chain | cast a fence to a dma_fence_chain |
| dma_fence_chain_alloc |  |
| dma_fence_chain_free |  |
| dma_fence_chain_for_each | iterate over all fences in chain |
| sync_file_create | creates a sync file |
| sync_file_get_fence | get the fence related to the sync_file fd |
| struct sync_file | sync file to export to the userspace |

## 总数统计

总共提取了 113 个函数+结构。
