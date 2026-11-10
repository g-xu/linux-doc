#!/usr/bin/env python3
"""跨篇引用检查器。

从每篇文档的标题里抽出全部章节编号建成集合，再抽出全部 `0X §Y` 形式的
跨篇引用，求差集，报告指向不存在章节的引用。

为什么必须用脚本：重排轮会动章节号，而手工回访会漏。i2c 那次 90 条引用里
手工自认为查过的 3 条恰好就是失效的那 3 条（见 methodology.md §A.21）。
**每轮重排结束必须跑这个脚本。**

识别的引用写法（都是文档里实际用过的）：
    06 §3.1
    `06-devicetree.md` §3.1
    [`03-internals.md`](03-internals.md) §B1.4
    见 04 §6
支持 A/B/C 前缀的章节号（03 用 Part A/B/C 分区，编号形如 §A5 / §B1.4）。

用法：
    check_xrefs.py <文档目录> [--ignore 忽略文件]

忽略文件格式：每行一条 `源篇号->目标篇号 §章节号`，如 `99->03 §7`。
`#` 开头为注释。用于登记已知假阳性（变更日志里引用的旧章节号）。
退出码：0 = 全部命中，1 = 有未登记的失效引用。
"""
import argparse
import os
import re
import sys
from collections import defaultdict

SECNUM = r"[A-C]?\d+(?:\.\d+)*"
# 标题：## 1. xxx / ### 2.11 xxx / ### §A5 xxx / #### B1.4 xxx
HEADING = re.compile(r"^#{2,6}\s*§?\s*(" + SECNUM + r")[.、：:\s]")
# 引用：先匹配显式带 .md 的，再匹配裸篇号
# 关键：篇号与 § 之间的间隔里不许再出现另一个 .md，否则
# "`00-overview.md` 开头和 `03-internals.md` §B1.4" 会把 §B1.4 误挂到 00 头上
XREF_MD = re.compile(
    r"(\d{2})-[\w-]+\.md(?:(?!\.md)[^§\n]){0,60}?§\s*(" + SECNUM + r")")
XREF_BARE = re.compile(r"(?<![\w.-])(\d{2})\s*§\s*(" + SECNUM + r")")


def doc_index(doc_dir):
    """{篇号: (文件名, {章节号})}"""
    idx = {}
    for name in sorted(os.listdir(doc_dir)):
        m = re.match(r"^(\d{2})-", name)
        if not name.endswith(".md") or not m:
            continue
        secs = set()
        with open(os.path.join(doc_dir, name), encoding="utf-8") as fh:
            for line in fh:
                h = HEADING.match(line)
                if h:
                    secs.add(h.group(1))
        idx[m.group(1)] = (name, secs)
    return idx


def implied(sec):
    """§2.11 命中时也接受父节 §2 存在的情况反之不成立；这里只生成自身。"""
    return sec


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("doc_dir")
    ap.add_argument("--ignore", default=None)
    args = ap.parse_args()

    ignore = set()
    if args.ignore and os.path.isfile(args.ignore):
        with open(args.ignore, encoding="utf-8") as fh:
            for line in fh:
                line = line.split("#", 1)[0].strip()
                if line:
                    ignore.add(line)

    idx = doc_index(args.doc_dir)
    if not idx:
        print(f"错误：{args.doc_dir} 下没有 NN-*.md 文档", file=sys.stderr)
        return 2

    hits, misses = 0, []
    for name in sorted(os.listdir(args.doc_dir)):
        m = re.match(r"^(\d{2})-", name)
        if not name.endswith(".md") or not m:
            continue
        src = m.group(1)
        with open(os.path.join(args.doc_dir, name), encoding="utf-8") as fh:
            for lineno, line in enumerate(fh, 1):
                seen = set()
                for pat in (XREF_MD, XREF_BARE):
                    for mm in pat.finditer(line):
                        tgt, sec = mm.group(1), mm.group(2)
                        if (tgt, sec) in seen:
                            continue
                        seen.add((tgt, sec))
                        if tgt not in idx:
                            misses.append(
                                (f"{name}:{lineno}", f"{src}->{tgt} §{sec}",
                                 f"没有 {tgt}-*.md 这一篇"))
                            continue
                        tname, secs = idx[tgt]
                        if sec in secs:
                            hits += 1
                            continue
                        key = f"{src}->{tgt} §{sec}"
                        if key in ignore:
                            continue
                        near = sorted(s for s in secs
                                      if s.split(".")[0] == sec.split(".")[0])
                        misses.append(
                            (f"{name}:{lineno}", key,
                             f"{tname} 里没有 §{sec}"
                             + (f"（同组现有：{', '.join(near[:6])}）" if near else "")))

    print(f"文档 {len(idx)} 篇；跨篇引用命中 {hits} 条，"
          f"失效 {len(misses)} 条（忽略项 {len(ignore)} 条）")
    for where, key, why in misses:
        print(f"  {where}  {key}\n      {why}")
    if not misses:
        print("干净：所有跨篇引用都指向真实存在的章节。")
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
