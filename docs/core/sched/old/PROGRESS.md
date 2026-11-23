# sched 文档集生成台账

- **基线**:Linux 6.1.158,**RT 树 `-rt58`**(三判据全中)。RT 改动文件:core.c / fair.c / features.h / sched.h → 逐篇标 RT 差异,05 §7 强制。
- **架构**:arm64 only。
- **本轮范围**:顶层 11 篇 + `sub/fair/` 4 篇 ≈ 15 篇。
- **产出顺序**:04 → 03 → 05 → 02 → sub/fair → 01 → 06/07/08 → 00 → 99。

## 已确认台账事实(共享对象,kernel/sched/sched.h)

| 符号 | 行号 | 备注 |
|---|---|---|
| `struct rq` | 954 | per-CPU 主结构 |
| `rq.__lock` | 956 | **raw_spinlock_t**(RT 下语义不变) |
| `rq.nr_running` | 962 | |
| `rq.nr_switches` | 981 | |
| `rq.cfs/rt/dl` | 990-992 | 内嵌各类 runqueue |
| `rq->curr` | 1008 | **`struct task_struct __rcu *`**(RCU 字段) |
| `rq->idle/stop` | 1009-1010 | |
| `rq.clock/clock_task` | 1015 / — | |
| `struct cfs_rq` | 550 | |
| `cfs_rq->curr` | 574 | **裸 `struct sched_entity *`**(与 rq->curr 同名不同物) |
| `cfs_rq->nr_running` | 552 | 与 rq->nr_running 同名不同物 |
| `struct rt_rq` | 663 | |
| `struct dl_rq` | 704 | |
| `struct sched_class` | 2148 | ops 回调表 |
| `DEFINE_SCHED_CLASS` 实例 | stop_task.c:117 / deadline.c:2738 / rt.c:2692 / idle.c:499 / fair.c:12443 | 优先级链 stop>dl>rt>fair>idle |
| load-balance 段 | fair.c:8009-~11760 | 注释锚点 "Fair scheduling class load-balancing methods" |

## 进度

| 篇 | 状态 | 备注 |
|---|---|---|
| 04-interactions | 完成 | 42 非空格子,展开 8+欠债 34;证据 .evidence/R3 |
| 03-lifecycle | 完成 | 10 跳状态机;证据 .evidence/R2 |
| 05-concurrency | 完成 | RT §7 强制;9 锁+7 mutex;证据 .evidence/R4 |
| 02-objects | 完成 | 主对象 struct rq 六组字段;§3 三组同名陷阱+RCU+双时钟;证据 .evidence/R1 |
| sub/fair/00 | 完成 | 定位+动机+顶层跳映射+API 三列表;证据 .evidence/R5 |
| sub/fair/02 | 完成 | 入/出队/选任/tick 逐段+专属观测点;证据 .evidence/R5 |
| sub/fair/03 | 完成 | 3 不变式+复杂度+5 退化+参数敏感性+CFS 代际;证据 .evidence/R5 |
| sub/fair/99 | 完成 | 9 节取证+索引盲区声明 |
| 01-api | 完成 | 两层:syscall ABI + 内核内 API;RT §3;证据 .evidence/R6 |
| 06-tuning | 完成 | §0 CFS 参数挪 debugfs;sysctl/cgroup/boot/features;RT throttling §5;R7 证据 |
| 07-debug | 完成 | /proc/sched_debug/tracepoint/BPF(无 sched_ext)/schedstat;RT §6;R8 证据 |
| 08-feynman | 完成 | 四概念四段式:vruntime公平/tick抢占/RT lazy/DL吃RT带宽 |
| 00-overview | 完成 | 11 子模块划分 + 文档导航 + 阅读路径 |
| 99-evidence | 完成 | 顶层 00-08 取证 + 索引盲区总声明 + 分篇证据索引 |

**全部 15 篇完成,三个自检脚本全部通过(脚本已迁至 `~/.claude/skills/kernel-subsystem-docs/scripts/`):**
- `check_xrefs.py`:62 条跨篇引用全命中,0 失效。
- `check_line_refs.py`:353 条 file:line 引用,0 解析不到 / 0 越界。
- `check_doc_coverage.sh`:14 篇官方概念文档全部被引用(0 篇 0 引用)。

收尾修复记录:
- xref 两处误挂(02:25 `§2.6`、08:46 `§7`)——章节号紧跟 `.md` 链接被误绑到顶层篇号(检查器只索引顶层 `NN-*.md`),去掉 `§` 字形改为"本篇 2.6 节"/"RT 差异一节"。
- line_ref:99:11 版本出处 `Makefile:2-4` 改 `./Makefile:2-4`(消歧到仓库根 Makefile,非 kernel/Makefile);99:78 `fair.c/rt.c/deadline.c` 三连引用补空格,避免 `3062/deadline.c` 被当成一个文件名。
- 覆盖:99 新增 §11「官方文档对照」表,把 14 篇 `Documentation/scheduler/*.rst` 映射到各篇覆盖点。

## 自检脚本(每篇写完跑)

```bash
S=.qoder/skills/kernel-core-subsystem-docs/scripts
D=output/sched
python3 $S/check_xrefs.py     $D --ignore $D/.xref-ignore
python3 $S/check_line_refs.py $D . --ignore $D/.lineref-ignore
bash    $S/check_doc_coverage.sh scheduler $D .
```
