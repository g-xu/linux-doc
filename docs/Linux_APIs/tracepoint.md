# Kernel Module tracepoint.md API Documentation

## The Linux Kernel Tracepoint API¶

## Introduction¶

## IRQ¶

| 名称 | 描述 |
|---|---|
| trace_irq_handler_entry | called immediately before the irq action handler |
| trace_irq_handler_exit | called immediately after the irq action handler returns |
| trace_softirq_entry | called immediately before the softirq handler |
| trace_softirq_exit | called immediately after the softirq handler returns |
| trace_softirq_raise | called immediately when a softirq is raised |

## SIGNAL¶

| 名称 | 描述 |
|---|---|
| trace_signal_generate | called when a signal is generated |
| trace_signal_deliver | called when a signal is delivered |

## Block IO¶

| 名称 | 描述 |
|---|---|
| trace_block_touch_buffer | mark a buffer accessed |
| trace_block_dirty_buffer | mark a buffer dirty |
| trace_block_rq_requeue | place block IO request back on a queue |
| trace_block_rq_complete | block IO operation completed by device driver |
| trace_block_rq_insert | insert block operation request into queue |
| trace_block_rq_issue | issue pending block IO request operation to device driver |
| trace_block_rq_merge | merge request with another one in the elevator |
| trace_block_bio_complete | completed all work on the block operation |
| trace_block_bio_bounce | used bounce buffer when processing block operation |
| trace_block_bio_backmerge | merging block operation to the end of an existing operation |
| trace_block_bio_frontmerge | merging block operation to the beginning of an existing operation |
| trace_block_bio_queue | putting new block IO operation in queue |
| trace_block_getrq | get a free request entry in queue for block IO operations |
| trace_block_plug | keep operations requests in request queue |
| trace_block_unplug | release of operations requests in request queue |
| trace_block_split | split a single bio struct into two bio structs |
| trace_block_bio_remap | map request for a logical device to the raw device |
| trace_block_rq_remap | map request for a block operation request |

## Workqueue¶

| 名称 | 描述 |
|---|---|
| trace_workqueue_queue_work | called when a work gets queued |
| trace_workqueue_activate_work | called when a work gets activated |
| trace_workqueue_execute_start | called immediately before the workqueue callback |
| trace_workqueue_execute_end | called immediately after the workqueue callback |

## 总数统计

总共提取了 29 个函数+结构。
