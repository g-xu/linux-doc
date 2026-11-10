#!/usr/bin/env python3
"""file:line 越界检查器。

扫描文档集里所有 `路径:行号` / `路径:起-止` 引用，解析成内核树里的真实路径，
与 `wc -l` 比对，报告三档：
  - AMBIGUOUS：简写名在树里有多个同名文件，已按文档自己最常用的全路径校验
  - UNRESOLVED：解析不到。两种原因，都要处理：
      (a) 文件名记错了 → 改文档
      (b) 文档从头到尾没把这个文件的全路径写过一次 → 补一次全路径
          （方法论第 13 条：路径要写全，不能通篇只有裸文件名）
  - OUT-OF-RANGE：行号超出文件实际行数

抓不到的：**行号在范围内但指向了无关代码**。那一类只能逐个 sed 打开看，
本脚本不替代逐行核对，只负责先把机械错误清干净。

简写名的解析办法见 build_tables()：不按目录顺序猜，只采信文档自己写过的全路径。

用法：
    check_line_refs.py <文档目录> <内核树根> [--ignore 忽略文件]

忽略文件格式：每行一条 `路径:行号`，`#` 开头为注释。用于登记已知假阳性
（纠错型文档里"错值"和"旧值"是内容的一部分，见 methodology.md §B）。
退出码：0 = 干净，1 = 有未登记的问题。
"""
import argparse
import os
import re
import sys
from collections import defaultdict

# 只认内核树里真实存在的扩展名，避免把 "i2c-1:0x50" 之类误判成文件引用
EXT = r"(?:c|h|S|rst|txt|yaml|dts|dtsi|py|sh|pl|awk|md|json)"
REF = re.compile(
    r"(?<![\w/.-])"                      # 左边界
    r"((?:[\w.+-]+/)*[\w.+-]+\." + EXT + r")"   # 路径
    r":(\d+)(?:\s*[-–]\s*(\d+))?"        # :N 或 :N-M（允许 en dash）
)
# Kconfig / Makefile 没有扩展名，单独一条
REF_NOEXT = re.compile(
    r"(?<![\w/.-])((?:[\w.+-]+/)*(?:Kconfig|Makefile)[\w.+-]*):(\d+)(?:\s*[-–]\s*(\d+))?"
)


def collect_refs(doc_dir):
    """返回 {(文件名, 行号, 止行号): [出现位置...]}"""
    refs = defaultdict(list)
    for name in sorted(os.listdir(doc_dir)):
        if not name.endswith(".md"):
            continue
        path = os.path.join(doc_dir, name)
        with open(path, encoding="utf-8") as fh:
            for lineno, line in enumerate(fh, 1):
                for pat in (REF, REF_NOEXT):
                    for m in pat.finditer(line):
                        f, start, end = m.group(1), int(m.group(2)), m.group(3)
                        refs[(f, start, int(end) if end else None)].append(
                            f"{name}:{lineno}"
                        )
    return refs


def build_tables(refs, ktree):
    """两张表，都从文档自己的显式全路径写法推出来，不按目录顺序猜。

    内核树里同名头文件很多（i2c.h 有 12 个，17~1092 行），随便取一个必然错。
    可靠判据是**文档在别处怎么写它的全路径**：

      bymap  基名 -> [(次数, 全路径)…]  给简写名消歧（i2c.h → include/linux/i2c.h）
      prefix [目录…] 按引用次数降序      给相对路径补前缀

    prefix 连祖先目录一起放，这样 `muxes/i2c-mux-reg.c` 能靠 `drivers/i2c` 命中。
    两张表迭代到不动点：`muxes/i2c-mux-reg.c` 一旦靠前缀解析成功，
    `drivers/i2c/muxes` 就进了 prefix、`i2c-mux-reg.c` 就进了 bymap，
    于是下一轮裸写的 `i2c-mux-reg.c:201-207` 也能解析。
    """
    bysub = defaultdict(lambda: defaultdict(int))
    pref = defaultdict(int)

    def learn(rel, n):
        bysub[os.path.basename(rel)][rel] += n
        d = os.path.dirname(rel)
        while d:
            pref[d] += n
            d = os.path.dirname(d)

    def tables():
        bymap = {b: sorted(((n, p) for p, n in d.items()), reverse=True)
                 for b, d in bysub.items()}
        prefix = [d for _, d in sorted(((n, d) for d, n in pref.items()),
                                       reverse=True)]
        return bymap, prefix

    for (f, _, _), where in refs.items():
        if "/" in f and os.path.isfile(os.path.join(ktree, f)):
            learn(f, len(where))

    for _ in range(4):                       # 实测 2 轮就收敛，留点余量
        bymap, prefix = tables()
        grew = False
        for (f, _, _), where in refs.items():
            if os.path.isfile(os.path.join(ktree, f)):
                continue                     # 已是全路径
            p, _o = resolve(f, ktree, bymap, prefix)
            if p:
                rel = os.path.relpath(p, ktree)
                if rel not in bysub[os.path.basename(rel)]:
                    grew = True
                learn(rel, len(where))
        if not grew:
            break
    return tables()


def resolve(f, ktree, bymap, prefix, doc_dir=None):
    """返回 (路径 or None, 其它候选)。三级：全路径 → 消歧表 → 前缀拼接。"""
    if "/" in f:
        p = os.path.join(ktree, f)
        if os.path.isfile(p):
            return p, []
    else:
        # 文档引用自己（`05-usage.md:639`）：在文档目录里找
        if doc_dir and os.path.isfile(os.path.join(doc_dir, f)):
            return os.path.join(doc_dir, f), []
        # 文档别处写过它的全路径，采信最常用的那个
        cands = bymap.get(f, [])
        if cands:
            best = os.path.join(ktree, cands[0][1])
            if os.path.isfile(best):
                return best, [p for _, p in cands[1:]]
    hits = [os.path.join(d, f) for d in prefix
            if os.path.isfile(os.path.join(ktree, d, f))]
    if hits:
        return os.path.join(ktree, hits[0]), hits[1:]
    return None, []


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("doc_dir")
    ap.add_argument("ktree")
    ap.add_argument("--ignore", default=None)
    args = ap.parse_args()

    ignore = set()
    if args.ignore and os.path.isfile(args.ignore):
        with open(args.ignore, encoding="utf-8") as fh:
            for line in fh:
                line = line.split("#", 1)[0].strip()
                if line:
                    ignore.add(line)

    refs = collect_refs(args.doc_dir)
    bymap, prefix = build_tables(refs, args.ktree)
    nlines = {}

    unresolved, oor, ambig = defaultdict(list), [], {}
    for (f, start, end), where in sorted(
            refs.items(), key=lambda kv: (kv[0][0], kv[0][1], kv[0][2] or 0)):
        p, others = resolve(f, args.ktree, bymap, prefix, args.doc_dir)
        if p is None:
            unresolved[f].extend(where)
            continue
        if others:
            ambig[f] = (os.path.relpath(p, args.ktree), others)
        if p not in nlines:
            with open(p, "rb") as fh:
                nlines[p] = sum(1 for _ in fh)
        total = nlines[p]
        hi = end or start
        if hi > total or start < 1:
            key = f"{f}:{start}" + (f"-{end}" if end else "")
            if key in ignore:
                continue
            oor.append((key, total, where))

    print(f"扫描 {len(refs)} 条唯一 file:line 引用"
          f"（消歧表 {len(bymap)} 个基名 / {len(prefix)} 个前缀目录，"
          f"忽略项 {len(ignore)} 条）")

    if ambig:
        print(f"\n== AMBIGUOUS（{len(ambig)} 个简写文件名，树里有多个候选）==")
        print("   已按文档里最常用的全路径校验，但正文最好写全路径：")
        for f, (used, others) in sorted(ambig.items()):
            print(f"  {f} → 按 {used} 校验；另有 {', '.join(others[:3])}")

    if unresolved:
        print(f"\n== UNRESOLVED（{len(unresolved)} 个文件名解析不到）==")
        print("   要么是文件名记错了，要么是文档从没把它的全路径写过一次：")
        for f, where in sorted(unresolved.items()):
            print(f"  {f}\n      出现于 {', '.join(sorted(set(where))[:5])}")

    if oor:
        print(f"\n== OUT-OF-RANGE（{len(oor)} 条）==")
        for key, total, where in oor:
            print(f"  {key}  实际只有 {total} 行"
                  f"\n      出现于 {', '.join(sorted(set(where))[:5])}")

    if not unresolved and not oor:
        print("\n干净：没有解析不到的文件，也没有越界行号。")
        return 0
    print("\n注意：本脚本抓不到"
          "「行号在范围内但指向无关代码」——那一类必须逐个 sed 打开核对。")
    return 1


if __name__ == "__main__":
    sys.exit(main())
