# Kernel Module spi.md API Documentation

## Serial Peripheral Interface (SPI)¶

| 名称 | 描述 |
|---|---|
| struct spi_statistics | statistics for spi transfers |
| struct spi_delay | SPI delay information |
| struct spi_device | Controller side proxy for an SPI slave device |
| struct spi_driver | Host side “protocol” driver |
| spi_unregister_driver | reverse effect of spi_register_driver |
| module_spi_driver | Helper macro for registering a SPI driver |
| struct spi_controller | interface to SPI master or slave controller |
| struct spi_res | spi resource management structure |
| struct spi_transfer | a read/write buffer pair |
| struct spi_message | one multi-segment SPI transaction |
| spi_message_init_with_transfers | Initialize spi_message and append transfers |
| spi_is_bpw_supported | Check if bits per word is supported |
| struct spi_replaced_transfers | structure describing the spi_transfer replacements that have occurred so that they can get reverted |
| spi_sync_transfer | synchronous SPI data transfer |
| spi_write | SPI synchronous write |
| spi_read | SPI synchronous read |
| spi_w8r8 | SPI synchronous 8 bit write followed by 8 bit read |
| spi_w8r16 | SPI synchronous 8 bit write followed by 16 bit read |
| spi_w8r16be | SPI synchronous 8 bit write followed by 16 bit big-endian read |
| struct spi_board_info | board-specific template for a SPI device |
| spi_register_board_info | register SPI devices for a given board |
| __spi_register_driver | register a SPI driver |
| spi_alloc_device | Allocate a new SPI device |
| spi_add_device | Add spi_device allocated with spi_alloc_device |
| spi_new_device | instantiate one new SPI device |
| spi_unregister_device | unregister a single SPI device |
| spi_finalize_current_transfer | report completion of a transfer |
| spi_take_timestamp_pre | helper for drivers to collect the beginning of the TX timestamp for the requested byte from the SPI transfer. The frequency with which this function must be called (once per word, once for the whole transfer, once per batch of words etc) is arbitrary as long as thetxbuffer offset is greater than or equal to the requested byte at the time of the call. The timestamp is only taken once, at the first such call. It is assumed that the driver advances itstxbuffer pointer monotonically. |
| spi_take_timestamp_post | helper for drivers to collect the end of the TX timestamp for the requested byte from the SPI transfer. Can be called with an arbitrary frequency: only the first call wheretxexceeds or is equal to the requested word will be timestamped. |
| spi_get_next_queued_message | called by driver to check for queued messages |
| spi_finalize_current_message | the current message is complete |
| spi_new_ancillary_device | Register ancillary SPI device |
| spi_slave_abort | abort the ongoing transfer request on an SPI slave controller |
| __spi_alloc_controller | allocate an SPI master or slave controller |
| __devm_spi_alloc_controller | resource-managed__spi_alloc_controller() |
| spi_register_controller | register SPI master or slave controller |
| devm_spi_register_controller | register managed SPI master or slave controller |
| spi_unregister_controller | unregister SPI master or slave controller |
| spi_busnum_to_master | look up master associated with bus_num |
| spi_res_alloc | allocate a spi resource that is life-cycle managed during the processing of a spi_message while using spi_transfer_one |
| spi_res_free | free an spi resource |
| spi_res_add | add a spi_res to the spi_message |
| spi_res_release | release all spi resources for this message |
| spi_replace_transfers | replace transfers with several transfers and register change with spi_message.resources |
| spi_split_transfers_maxsize | split spi transfers into multiple transfers when an individual transfer exceeds a certain size |
| spi_setup | setup SPI mode and clock rate |
| spi_async | asynchronous SPI transfer |
| spi_async_locked | version of spi_async with exclusive bus usage |
| spi_sync | blocking/synchronous SPI data transfers |
| spi_sync_locked | version of spi_sync with exclusive bus usage |
| spi_bus_lock | obtain a lock for exclusive SPI bus usage |
| spi_bus_unlock | release the lock for exclusive SPI bus usage |
| spi_write_then_read | SPI synchronous write followed by read |

## 总数统计

总共提取了 53 个函数+结构。
