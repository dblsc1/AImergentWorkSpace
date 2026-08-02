#!/usr/bin/env bash
# 写区路签 —— **它是碰撞探测器，不是串行器。**
#
# ── 为什么有这个文件（本项目实证事故，同一个错一天三次）─────────────
# 派活方申领了写区，派完活直接走人，签一直挂着。被卡的一方**沉默地等**——
# 它只会一直重试 `mission_start.sh`，不会喊。ring / table / gantt 三个模块的
# 子代理先后被卡住，最长一次半小时。每次事后 worklog 都写「以后记得还签」，
# 然后又犯。**靠记性不管用。**
#
# 病根不是「忘了」，是三个独立缺陷：
#   ① **无过期**：签发出去永久有效。持有者走了、崩了、忘了，签都还在
#   ② **释放不在关键路径上**：`--release` 是纯自愿的事后动作
#   ③ **被卡的一方沉默**：拒发只说「写区重叠」，被卡方无从知道该找谁
#
# 还有一个那次特有的：**粒度过粗**。arbiter 持 `codeagent/` 挡住了
# `codeagent/programmer/docs/`，而它真正要写的只是那张任务单。
#
# ── 社区对照：没有一个成熟系统用「无限期持有 + 自愿释放」───────────
#   Chubby / ZooKeeper   ephemeral node —— 锁绑会话，进程一死锁自动没
#   etcd / K8s Lease     TTL + 续租，不续租就过期（leader election 全靠这个）
#   数据库 MGL           父节点意向锁 IX、子节点真锁 X —— 正好治「持父挡子」
#   Perforce / Piper     悲观 checkout —— **和我们一样的病**，所以业界往乐观走
#   Bazel 类             声明式输入输出 —— **不相交是设计出来的，不是锁出来的**
#
# 我们属于最后一类：写区本来就该不相交（arbiter 在 module_docs/、
# programmer 在 code/<子文件夹>/、reviewer 在 review/）。
# **一旦签开始串行化正常并发，说明写区划分错了或粒度太粗**——那次两样都占。
#
# ── 落点 ──────────────────────────────────────────────────
#   .git/aimergent-leases/<角色>.lease   写区前缀，一行一个
#   .git/aimergent-leases/<角色>.meta    发签时间 / 任务单 / TTL
#   .git/aimergent-leases/<角色>.docs    预期文档变更
# 住 .git/ 下：不入仓、不随 clone。它是**本机互斥探测**，不是证据。

LEASE_TTL_SECONDS=${AIMERGENT_LEASE_TTL:-7200}   # 默认 2 小时

lease_dir_path() {
  printf '%s/aimergent-leases' \
    "$(git rev-parse --path-format=absolute --git-common-dir 2>/dev/null || echo .git)"
}

# 发签时同时写 meta。**没有 meta 的签按「上一版留下的」处理**——
# 不当成永久有效，也不当成不存在，而是让 lease_age 返回未知（-1），
# 由调用方决定。**未知不等于过期**，这条是有意的：静默回收一个我们
# 判不出年龄的签，等于把互斥悄悄关掉。
lease_write_meta() {   # lease_write_meta <角色> <任务单>
  local role=$1 task=${2:-} d; d=$(lease_dir_path); mkdir -p "$d" || return 1
  {
    printf 'granted_at=%s\n' "$(date -u +%s)"
    printf 'granted_iso=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    printf 'task=%s\n' "$task"
    printf 'ttl=%s\n' "$LEASE_TTL_SECONDS"
  } > "$d/$role.meta"
}

lease_meta_get() {   # lease_meta_get <角色> <键> → 值（无则空）
  local d; d=$(lease_dir_path)
  [ -f "$d/$1.meta" ] || return 1
  sed -n "s/^$2=//p" "$d/$1.meta" | head -1
}

# 签持有了多久（秒）。无 meta 返回 -1（未知），调用方不许把未知当成 0。
lease_age() {   # lease_age <角色>
  local t; t=$(lease_meta_get "$1" granted_at 2>/dev/null) || { printf '%s' -1; return; }
  [ -n "$t" ] || { printf '%s' -1; return; }
  printf '%s' "$(( $(date -u +%s) - t ))"
}

lease_expired() {   # lease_expired <角色> → 0=已过期
  local age ttl
  age=$(lease_age "$1"); [ "$age" -ge 0 ] 2>/dev/null || return 1   # 未知不算过期
  ttl=$(lease_meta_get "$1" ttl 2>/dev/null); [ -n "$ttl" ] || ttl=$LEASE_TTL_SECONDS
  [ "$age" -gt "$ttl" ]
}

# 回收过期签。**必须打印回收了谁**——静默回收等于把互斥悄悄关掉，
# 那比忘了还签更危险：忘了还签会卡住人（吵闹），静默回收会让双写发生（安静）。
lease_reap() {   # lease_reap [排除的角色] → 打印被回收的角色名
  local skip=${1:-} d f holder reaped=""
  d=$(lease_dir_path); [ -d "$d" ] || return 0
  shopt -s nullglob
  for f in "$d"/*.lease; do
    holder=$(basename "$f" .lease)
    [ "$holder" = "$skip" ] && continue
    lease_expired "$holder" || continue
    printf '♻️  回收过期路签：%s（持有 %s 秒，超过 TTL %s 秒；任务 %s）\n' \
      "$holder" "$(lease_age "$holder")" \
      "$(lease_meta_get "$holder" ttl 2>/dev/null || echo "$LEASE_TTL_SECONDS")" \
      "$(lease_meta_get "$holder" task 2>/dev/null || echo '（未记）')" >&2
    rm -f "$f" "$d/$holder.meta" "$d/$holder.docs"
    reaped="${reaped:+$reaped }$holder"
  done
  shopt -u nullglob
  [ -n "$reaped" ] && printf '%s' "$reaped"
  return 0
}

lease_release() {   # lease_release <角色>
  local d; d=$(lease_dir_path)
  [ -f "$d/$1.lease" ] || return 1
  rm -f "$d/$1.lease" "$d/$1.meta" "$d/$1.docs"
  return 0
}

# ── 层②：「完工即自动还签」的落点 —— 拒发这一刻 ──────────────────
# （2026-08-02 CFO 裁决；驳回的方案是挂在 dispatch.sh 上）
#
# 为什么落在**拒发**而不是派单或完工：这是唯一**有人真的正被挡住**的时刻。
# 派单时没有任何人在等，把释放挂在那里依然是自愿动作，只是换了个触发器；
# 完工（mission_complete）是 pre-commit 的主体，每次 commit 都跑，在那里还签
# 等于每提交一次就把互斥关掉一次。
#
# **只给判据 + 可粘贴的命令，绝不自动强收。** 本文件顶上那条红线在这里同样成立：
# 忘了还签会卡住人（吵闹），静默回收会让双写发生（安静）。判断「持有者是不是
# 真的走了」需要的信息在人那儿，不在这个脚本里 —— 所以脚本只负责把判据摆出来。
#
# 三条判据（全过才建议强收）：
#   A 持有者写区在工作树里干净 —— 有未提交改动 = 人还在里面干活，收了就是抢
#   B 起飞时 --docs 声明的文档变更已全部落地（入仓 + 发签之后真被改过）
#   C 签龄已知 —— 年龄未知的签判不出「走没走」，只能人看
# 退 0 = 三条全过可强收；退 1 = 有条件不满足，别收。
lease_takeover_advice() {   # lease_takeover_advice <持有者> [被卡方的重试命令]
  local h=$1 retry=${2:-} d dirty="" nland="" age granted _a _p _t held
  d=$(lease_dir_path)
  if [ ! -f "$d/$h.lease" ]; then
    printf '强收判据：%s 的签已经不在了，直接重试即可。\n' "$h"
    return 0
  fi

  # A 写区干净？（--untracked-files=no：未跟踪文件不算「正在改」）
  while IFS= read -r held; do
    [ -n "$held" ] || continue
    [ -n "$(git status --porcelain --untracked-files=no -- "$held" 2>/dev/null)" ] &&
      dirty="${dirty:+$dirty }$held"
  done < "$d/$h.lease"

  # B 声明的文档变更落地了？
  granted=$(lease_meta_get "$h" granted_at 2>/dev/null || true)
  if [ -f "$d/$h.docs" ]; then
    while IFS=$'\t' read -r _a _p; do
      [ -n "$_p" ] || continue
      if ! git ls-files --error-unmatch -- "$_p" >/dev/null 2>&1; then
        nland="${nland:+$nland; }$_p（还没入仓）"; continue
      fi
      _t=$(git log -1 --format=%ct -- "$_p" 2>/dev/null)
      if [ -n "$granted" ] && { [ -z "$_t" ] || [ "$_t" -lt "$granted" ] 2>/dev/null; }; then
        nland="${nland:+$nland; }$_p（发签之后一次都没动过）"
      fi
    done < "$d/$h.docs"
  fi

  age=$(lease_age "$h")
  printf '强收判据（三条全过才建议收；本脚本永远不自动收）：\n'
  if [ -z "$dirty" ]; then printf '  ✓ A 持有者写区在工作树里干净\n'
  else printf '  ✗ A 持有者写区有未提交改动：%s ← 人还在里面干活\n' "$dirty"; fi
  if [ -z "$nland" ]; then printf '  ✓ B 起飞时声明的文档变更都已落地（或没声明）\n'
  else printf '  ✗ B 声明了却没落地：%s\n' "$nland"; fi
  if [ "$age" -ge 0 ] 2>/dev/null; then printf '  ✓ C 签龄已知：%s 分钟\n' "$(( age / 60 ))"
  else printf '  ✗ C 签龄未知（无 meta，上一版留下的签）—— 判不出走没走，只能人看\n'; fi

  if [ -z "$dirty" ] && [ -z "$nland" ] && [ "$age" -ge 0 ] 2>/dev/null; then
    printf '结论：可强收。直接粘贴——\n'
    printf '  scripts/mission_start.sh --release %s\n' "$h"
    [ -n "$retry" ] && printf '  %s\n' "$retry"
    return 0
  fi
  printf '结论：不建议强收（见上面打 ✗ 的那条）。先找 %s，或把自己的写区切细到不相交。\n' "$h"
  return 1
}

# 被卡时的诊断。**拒发不许只说「重叠」**——要说清谁、多久、什么任务、怎么办。
# 那次事故里被卡的一方看到的只有「写区重叠」，它无从知道该找谁、该等多久。
lease_holder_detail() {   # lease_holder_detail <持有者>
  local h=$1 age
  age=$(lease_age "$h")
  if [ "$age" -lt 0 ] 2>/dev/null; then
    printf '%s（发签时间未记录——上一版留下的签，可用 scripts/mission_start.sh --release %s 强收）' "$h" "$h"
  else
    printf '%s（已持有 %s 分钟，任务：%s）' "$h" "$(( age / 60 ))" \
      "$(lease_meta_get "$h" task 2>/dev/null || echo '（未记）')"
  fi
}

# ── F2：写区粒度棘轮 ──────────────────────────────────────────────
# 五天的 diary 是这样的：cfo 的签从 agents/ 一路涨到八片，且**每一次膨胀前
# 90 秒内都紧跟一条 mission_blocked failed:write-lease** —— 即 05 拦下越区提交后，
# 持有者的标准修法是放宽自己的签。这条棘轮没有反向齿，终点是一个角色持有整仓，
# 那时路签不再是碰撞探测器，而是派活方持有的全局互斥锁。
# 本文件开头写下的判据「一旦签开始串行化正常并发，说明写区划分错了或粒度太粗」
# 已经触发；唯一一次收窄是 2026-08-02 07:49 人手做的。
#
# **只警告不硬拦**（判例库「判断题不做硬闸门」：硬拦会逼人用逃生口，
# 逃生口用滥整套闸门就废）。「放宽到什么程度算粒度错了」没有客观阈值，是判断题。
lease_widening() {   # lease_widening <角色> <新写区...> → 打印放宽项；退 0=检出放宽
  local role=$1; shift
  local d o n hit=1 covered old=()
  d=$(lease_dir_path)
  [ -f "$d/$role.lease" ] || return 1          # 没有旧签就没有棘轮可言
  mapfile -t old < "$d/$role.lease"
  [ "${#old[@]}" -gt 0 ] || return 1
  for n in "$@"; do
    covered=0
    for o in "${old[@]}"; do
      [ -n "$o" ] || continue
      case "$n" in "$o"*) covered=1 ;; esac                     # 新的落在旧的里 = 没放宽
      case "$o" in "$n"*)                                       # 旧的落在新的里 = 放宽了
        [ "$o" = "$n" ] || { printf '  放宽：%s → %s\n' "$o" "$n"; hit=0; covered=1; } ;;
      esac
    done
    [ "$covered" -eq 1 ] || { printf '  新增：%s\n' "$n"; hit=0; }
  done
  return $hit
}

# 该角色历次发签的实况。**光说「你的签有点宽」没有信息量**，
# 「五天里从 4 片涨到 8 片、从未收窄」才是让人停一下的那句话。
# 取自 logs/diary.jsonl 的 lease_grant —— diary 不入仓（每写一行就动 git status，
# 入仓会把「活落盘了」类检查骗成恒真），所以这是**本机视角**的签史：
# 新克隆上是空的，那时不打印，**不假装有历史**。
lease_grant_history() {   # lease_grant_history <角色> [最多显示几条]
  local role=$1 max=${2:-5} f cnt i start=0 ts note lines=()
  f=$(git rev-parse --show-toplevel 2>/dev/null)/logs/diary.jsonl
  [ -f "$f" ] || return 1
  mapfile -t lines < <(grep -F '"event":"lease_grant"' "$f" 2>/dev/null |
                       grep -F "\"note\":\"$role: ")
  cnt=${#lines[@]}
  [ "$cnt" -gt 0 ] || return 1
  printf '本机记到的 %s 签史（%d 次发签；diary 不入仓，新克隆上没有）：\n' "$role" "$cnt"
  [ "$cnt" -gt "$max" ] && start=$(( cnt - max ))
  for i in $(seq 0 $(( cnt - 1 ))); do
    # **永远带上第一条**：棘轮比的是起点和现在，只看最近几条看不出单调性
    [ "$i" -eq 0 ] || [ "$i" -ge "$start" ] || continue
    [ "$i" -eq "$start" ] && [ "$start" -gt 1 ] && printf '   …（中间 %d 次略）\n' "$(( start - 1 ))"
    ts=$(sed -n 's/.*"ts":"\([^"]*\)".*/\1/p' <<<"${lines[$i]}")
    note=$(sed -n 's/.*"note":"[^:]*: \([^"]*\)".*/\1/p' <<<"${lines[$i]}")
    printf '   %s  %s 片  %s\n' "${ts:0:16}" "$(wc -w <<<"$note")" "$note"
  done
  return 0
}
