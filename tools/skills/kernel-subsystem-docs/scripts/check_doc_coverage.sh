#!/usr/bin/env bash
# 官方文档反向覆盖度检查。
#
# 前两个脚本查的是"我们写的东西对不对"，这个脚本查的是"官方写过的东西我们漏没漏"。
# 做法：列出官方文档里每一篇，统计它在文档集里被引用了几次。
#
# 分三档报，因为要求完全不一样：
#
#   【必查】概念/框架文档：Documentation/<subsys>/ 顶层 .rst（含 muxes/ 之类子框架目录）
#           + Documentation/driver-api/<subsys>*。
#           这一档 0 引用是真缺口，必须逐条处理：
#             (a) 内容确实没覆盖  → 补正文，并把该文件加进 99 §2.3 三等证据表
#             (b) 覆盖了但没标出处 → 在对应章节补一句"官方原文见 …"，让读者能跳回上游
#
#   【参考】DT 绑定：devicetree/bindings/<subsys>/。只报覆盖率 + 命中名单。
#           通用绑定（i2c.yaml / i2c-mux.yaml 这类）本来就会被引用很多次、自然出现在名单里；
#           单控制器绑定 100+ 篇，没人逐篇文档化。要求在 06 篇里落实的是
#           "每条属性都有定位到行的消费者"，不是"每篇绑定都被引用"。
#
#   【参考】单控制器说明：Documentation/<subsys>/busses/。
#           arm64-only 范围下这一档大部分本来就不该读（多为 x86 芯片组）。
#
# 用法：check_doc_coverage.sh <子系统名> <文档目录> [内核树根]
# 例：  check_doc_coverage.sh i2c output/i2c .

set -uo pipefail

SUBSYS="${1:?用法: check_doc_coverage.sh <子系统名> <文档目录> [内核树根]}"
DOCDIR="${2:?缺少文档目录}"
KTREE="${3:-.}"

found_any=0
must_total=0; must_zero=0
declare -A tier_total tier_hit tier_list

count_ref() {   # $1 = 检索键
    grep -ro -- "$1" "$DOCDIR" 2>/dev/null | wc -l | tr -d ' '
}

note() {        # $1 = 档名, $2 = 键, $3 = 次数
    tier_total["$1"]=$(( ${tier_total["$1"]:-0} + 1 ))
    if [ "$3" -gt 0 ]; then
        tier_hit["$1"]=$(( ${tier_hit["$1"]:-0} + 1 ))
        tier_list["$1"]="${tier_list["$1"]:-}  $2（$3 次）"$'\n'
    fi
}

echo "=== 【必查】概念/框架文档 ==="
for base in "$KTREE/Documentation/$SUBSYS" \
            "$KTREE/Documentation/driver-api/$SUBSYS"; do
    [ -d "$base" ] || continue
    found_any=1
    while IFS= read -r f; do
        stem="${f#"$base"/}"; stem="${stem%.rst}"; stem="${stem%.yaml}"
        case "$stem" in
        index|*/index) continue ;;
        busses/*) n=$(count_ref "$stem"); note "单控制器说明" "$stem" "$n"; continue ;;
        esac
        n=$(count_ref "$stem")
        must_total=$((must_total + 1))
        if [ "$n" -eq 0 ]; then
            must_zero=$((must_zero + 1))
            printf '  %-40s 引用 0 次   ← 查：内容真没写，还是写了没标出处？\n' "$stem"
        else
            printf '  %-40s 引用 %s 次\n' "$stem" "$n"
        fi
    done < <(find "$base" -maxdepth 2 \( -name '*.rst' -o -name '*.yaml' \) | sort)
done
# driver-api 下可能是单文件 <subsys>.rst 而不是目录
for f in "$KTREE/Documentation/driver-api/$SUBSYS".rst; do
    [ -f "$f" ] || continue
    found_any=1
    stem="driver-api/$SUBSYS"
    n=$(count_ref "$stem")
    must_total=$((must_total + 1))
    if [ "$n" -eq 0 ]; then
        must_zero=$((must_zero + 1))
        printf '  %-40s 引用 0 次   ← 查：内容真没写，还是写了没标出处？\n' "$stem"
    else
        printf '  %-40s 引用 %s 次\n' "$stem" "$n"
    fi
done
echo

bind="$KTREE/Documentation/devicetree/bindings/$SUBSYS"
if [ -d "$bind" ]; then
    found_any=1
    while IFS= read -r f; do
        stem="${f#"$bind"/}"; stem="${stem%.rst}"; stem="${stem%.yaml}"
        case "$stem" in index) continue ;; esac
        note "DT 绑定" "$stem" "$(count_ref "$stem")"
    done < <(find "$bind" -maxdepth 2 \( -name '*.rst' -o -name '*.yaml' \) | sort)
fi

for tier in "DT 绑定" "单控制器说明"; do
    t="${tier_total[$tier]:-0}"
    [ "$t" -eq 0 ] && continue
    h="${tier_hit[$tier]:-0}"
    echo "=== 【参考】$tier：共 $t 篇，被引用 $h 篇 ==="
    printf '%s' "${tier_list[$tier]:-}"
    case "$tier" in
    "DT 绑定")     echo "  提醒：06 篇的硬要求是"每条属性都有定位到行的消费者"，不是每篇绑定都被引用。" ;;
    "单控制器说明") echo "  提醒：arm64-only 范围下这一档大部分本来就不该读；99 §2.2 的代表驱动应在名单里。" ;;
    esac
    echo
done

if [ "$found_any" -eq 0 ]; then
    echo "没找到 Documentation/$SUBSYS/、driver-api/$SUBSYS* 或 bindings/$SUBSYS/。"
    echo "说明该子系统没有独立的官方文档目录——在 99 §1 里明确登记这一点，"
    echo "不要留白让读者以为漏读了。"
    exit 0
fi

echo "【必查】共 $must_total 篇，其中 $must_zero 篇 0 引用。"
if [ "$must_zero" -eq 0 ]; then
    echo "必查档全部有引用。"
    exit 0
fi
cat <<'EOF'
0 引用的逐条处理，二选一，都要落到 99 §2.3：
  (a) 内容没覆盖  → 补正文，并把该文件加进三等证据表
  (b) 覆盖了没标  → 在对应章节补"官方原文见 Documentation/…"
EOF
exit 1
