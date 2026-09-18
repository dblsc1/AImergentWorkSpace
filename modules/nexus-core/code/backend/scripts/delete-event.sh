#!/usr/bin/env bash
# nexus-core · 删除**单条**事实 —— **默认 dry-run，--apply 才真删**
#
#   bash code/backend/scripts/delete-event.sh <库名> <事件id>            # 只看，不动数据
#   bash code/backend/scripts/delete-event.sh <库名> <事件id> --apply    # 真删（四道闸）
#
# **库名必填，没有默认值。** 照 restore.sh / prune-orphan-events.sh 的规矩来：
# 删除是不可逆操作，默认值只会在你搞错时帮你更快地删掉不该删的东西。
#
# （任务单里写的签名是 `delete-event.sh <事件id>`。这里**多要一个库名**，
#  因为「--apply 要重打库名」这道闸没有库名就不成立，而任务单同时要求照抄这套闸门。
#  取闸门、不取字面签名——理由记在 code/backend/worklog/ 那篇里。）
#
# ════════ 它与 prune-orphan-events.sh 的分工 ════════
#
# prune 删的是**一类**（判据写死：指向已删实体的孤儿），人不能选删哪些。
# 本工具删的是**一条**，由人给出精确 id，**不接受任何查询条件**——
# 批量删事实的能力已经由 prune 提供且判据写死，再给一个「按条件删」
# 等于把那道判据绕过去。
#
# 实证背景：一个 agent 测顶栏时留了个计时没停，跑了 6h54m，当时契约只有
# start/stop，用户要停表只能往自己档案里写一条 6h54m 的假事实
# （evt_2c5e32cc04d6，24850 秒）。它指向的任务**真实存在**，不是孤儿，
# prune 抓不到它。（同一轮已补上 POST /timer/cancel，让以后不再需要造假事实。）
#
# ════════ 四道闸（都在下面，别拆）════════
#   P1 默认 dry-run    —— 不加 --apply 只打印计划，一条都不动
#   P7 库名白名单      —— 不在名单里直接拒，防手滑打到别的库
#   P2 重打库名确认    —— 照抄 restore.sh / prune 的形状
#   P3 删前先备份      —— 备份失败即中止。「删除前唯一的保险」必须真的存在，
#                        本项目已实证过一次假保险（归档窗口内回写，bundle 少一个 commit）
#   （删完重建投影在核心 py 里，与 prune 同一处理）
#
# `set -e`：本脚本每一步都是下一步的前提（备份成功才允许删）。
# **前一步没成功，后一步不该当它成功了。**
set -euo pipefail

HERE=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
BACKEND=$(cd -- "$HERE/.." && pwd -P)

die() { printf '❌ %s\n' "$*" >&2; exit 1; }
info() { printf 'ℹ️  %s\n' "$*"; }
ok() { printf '✅ %s\n' "$*"; }

# ── 参数 ───────────────────────────────────────────────────────────
DB=""
EVENT_ID=""
DEDUPE_KEY=""
APPLY=0
while [ $# -gt 0 ]; do
  case "$1" in
    --apply) APPLY=1 ;;
    --dedupe-key)
      shift
      [ $# -gt 0 ] || die "--dedupe-key 后面要跟值"
      DEDUPE_KEY="$1"
      ;;
    --help|-h) sed -n '2,12p' "$0"; exit 0 ;;
    -*) die "未知参数：$1（只认 --apply / --dedupe-key）" ;;
    *)
      if [ -z "$DB" ]; then
        DB="$1"
      elif [ -z "$EVENT_ID" ]; then
        EVENT_ID="$1"
      else
        die "多余的位置参数：'$1'（用法：<库名> <事件id> [--apply]）"
      fi
      ;;
  esac
  shift
done

[ -n "$DB" ] || die "用法: delete-event.sh <库名> <事件id> [--apply]
   **库名必填，不提供默认值** —— 删除是不可逆操作，默认值只会在你搞错时
   帮你更快地删掉不该删的东西（同 restore.sh / prune-orphan-events.sh）。"

[ -n "$EVENT_ID" ] || die "缺事件 id。用法: delete-event.sh <库名> <事件id> [--apply]
   本工具**只接受精确的事件 id**，不接受任何查询条件：
   批量删事实的能力由 prune-orphan-events.sh 提供且判据写死，
   再给一个「按条件删」等于把那道判据绕过去。"

# ── P7 库名白名单：不是预期的库直接拒 ──────────────────────────────
# 名单写死：本工具只为 nexus_core 这一个真库而写；其余一律只接受测试库
#（以 _test 结尾，与 conftest.py 的硬护栏同一套判据）。
case "$DB" in
  nexus_core) ;;
  *_test)     ;;
  *)
    die "库名 '$DB' 不在允许名单内。
   本工具只允许作用于：
     · nexus_core   （本项目唯一的生产库）
     · *_test       （测试库，与 conftest.py 的护栏同一判据）
   打到别的库上的删除是不可逆的，所以这里宁可拒绝也不猜你的意图。"
    ;;
esac

# ── NEXUS_TZ 必须显式给，且会被打印出来 ────────────────────────────
# 不是走过场：删完要重建投影，而 proj_daily_stats 是**按 NEXUS_TZ 归日**的。
# 用与服务端不同的时区跑重建，会把用户的历史静默重新分桶到错误的日子——
# 数据还在，但每一天的统计都错了，而且没有任何报错。所以这里不给默认值。
[ -n "${NEXUS_TZ:-}" ] || die "NEXUS_TZ 未设置。
   删完会重建投影，而 proj_daily_stats 按 NEXUS_TZ 归日。
   猜错时区 = 把历史静默记到错误的日子，之后每次统计都继承这个错。
   本机服务端用的是：NEXUS_TZ=Asia/Shanghai
   例：NEXUS_TZ=Asia/Shanghai bash $0 $DB $EVENT_ID"

export NEXUS_DB_NAME="$DB"
export NEXUS_MONGO_URI="${NEXUS_MONGO_URI:-mongodb://127.0.0.1:27017/}"

# ── 关键路径缺文件必须 die，不许静默跳过 ────────────────
# 反面教材是 `if [ -x <脚本> ]; then …; fi`：脚本改名或丢失就静默跳过、
# 然后照常报成功。崩溃会停下，撒谎让人以为干过了。
PY="$BACKEND/.venv/bin/python"
[ -x "$PY" ] || die "解释器不在：$PY
   本模块的依赖装在这个 venv 里，换成系统 python 会因缺 pymongo 而失败。"
CORE="$HERE/delete_event.py"
[ -f "$CORE" ] || die "核心逻辑不在：$CORE（本脚本只是它的闸门，缺了它什么也做不了）"
BACKUP="$HERE/backup.sh"
if [ "$APPLY" = 1 ]; then
  [ -f "$BACKUP" ] || die "备份脚本不在：$BACKUP
   --apply 必须先备份（P3）。没有备份就没有后悔药，拒绝执行。"
fi

CORE_ARGS=(--expect-db "$DB" --event-id "$EVENT_ID")
[ -n "$DEDUPE_KEY" ] && CORE_ARGS+=(--dedupe-key "$DEDUPE_KEY")

# ── 先定位并打印计划（dry-run 与 --apply 看到的是同一份计算）──────
# 定位失败（查无此 id / id 有歧义）在这里就非 0 退出，--apply 也走不到备份那步。
"$PY" "$CORE" "${CORE_ARGS[@]}" || die "定位失败，什么都没动"

if [ "$APPLY" != 1 ]; then
  printf '\n'
  info "以上是 DRY-RUN。要真删：在同一条命令后加 --apply"
  exit 0
fi

# ── P2 重打库名确认（照抄 restore.sh 的形状）───────────────────────
printf '\n──────────────────────────────────────────────\n'
printf '  ⚠️  即将从库 **%s** 中真实删除上面那一条事实\n' "$DB"
printf '     事件 id：%s\n' "$EVENT_ID"
printf '     重建投影将使用 NEXUS_TZ=%s\n' "$NEXUS_TZ"
printf '     events 是 append-only 且无软删除、无回收站——删了就是删了\n'
printf '──────────────────────────────────────────────\n\n'

# 非交互确认走 COCKPIT_DELETE_YES，但它**必须等于库名**，不是等于 1。
# 「重打一遍库名」的全部意义就是让你把库名亲手打出来；
# 一个 =1 的旁路等于把这道闸拆了。
if [ -n "${COCKPIT_DELETE_YES:-}" ]; then
  [ "$COCKPIT_DELETE_YES" = "$DB" ] || die "COCKPIT_DELETE_YES='$COCKPIT_DELETE_YES' 与库名 '$DB' 不符。
   非交互确认也必须把库名亲手打出来（这道闸的意义就在这里）。已中止，什么都没动。"
  info "已通过 COCKPIT_DELETE_YES 确认库名（非交互）"
else
  printf '确认删除？输入库名以确认（照抄一遍，防手滑）： ' >&2
  # read 读到 EOF 会返回非 0（非交互环境跑到这里就是这种情况）。
  # 必须显式说清「因为读不到确认所以停了」，不能靠 set -e 悄悄退出——
  # 一个没有理由的非 0 退出码，看的人只会以为是脚本坏了，然后想办法绕过去。
  read -r confirm || die "读不到确认输入（非交互环境？）。已中止，什么都没动。
   非交互执行请设 COCKPIT_DELETE_YES=<库名>（值必须就是库名本身）。"
  [ "$confirm" = "$DB" ] || die "输入的是 '$confirm'，与库名 '$DB' 不符。已中止，什么都没动。"
fi

# ── P3 删前自动备份，失败即中止 ────────────────────────────────────
info "先备份 $DB（删除前唯一的保险，失败就不往下走）"
bash "$BACKUP" "$DB" || die "备份失败 —— **已中止，一条都没删**。
   先把备份跑通再来。没有备份的删除在本项目里出过事故：
   events 无软删除、无回收站，丢了就是丢了。"
ok "备份完成"

# ── 真删 + 重建投影 ────────────────────────────────────────────────
"$PY" "$CORE" "${CORE_ARGS[@]}" --apply || die "删除过程失败。
   若已开始删除，请立刻用刚才那份备份还原：
   bash $HERE/restore.sh <上一步生成的 archive> $DB"

printf '\n'
ok "删除完成。建议立刻抽查档案页，确认这一条没了、其余的都还在。"
