#!/usr/bin/env bash
# nexus-core · 从备份还原 —— **覆盖性操作，先读完这段注释**
#
#   bash code/backend/scripts/restore.sh <archive文件> <目标库名>
#
# **两个参数都必填，没有默认值。** 这是有意的。
#
# 2026-08-01 本项目出过一次事故：测试套件的库名用 `setdefault` 给默认值，
# 「默认值只防你什么都没传，不防你传错了」——有人传了生产库名，
# 62 个测试挨个清空全部集合，用户的计时历史全没，append-only 无备份不可恢复。
#
# 还原比那个更危险：它是**主动覆盖**。所以这里连默认值都不给——
# 你必须把目标库名亲手打出来。
set -uo pipefail
die() { printf '❌ %s\n' "$*" >&2; exit 1; }
info() { printf 'ℹ️  %s\n' "$*"; }
ok() { printf '✅ %s\n' "$*"; }

ARCHIVE=${1:-}
TARGET=${2:-}
# 默认容器名：与 backup.sh 同一个坑，同一次修（2026-08-10 P0）——
# `deploy/docker-compose.yml` 的 compose 迁移把 Mongo 容器改名成
# `honeycomb-mongo`，本脚本的默认值必须跟着改，否则「目标库现有文档数」
# 那步会静默读到 `{}`（连不上時的兜底），让人误以为目标库是空的。
CONTAINER=${COCKPIT_MONGO_CONTAINER:-honeycomb-mongo}

[ -n "$ARCHIVE" ] && [ -n "$TARGET" ] || die "用法: restore.sh <archive文件> <目标库名>
   两个参数都必填。**目标库名不提供默认值**——还原是覆盖性操作，
   默认值只会在你搞错时帮你更快地覆盖掉不该覆盖的东西。"

[ -f "$ARCHIVE" ] || die "archive 不存在：$ARCHIVE"

docker exec "$CONTAINER" true 2>/dev/null || die "Mongo 容器 '$CONTAINER' 连不上（不存在或未运行）。
   docker ps 看真实容器名，用 COCKPIT_MONGO_CONTAINER=<真实容器名> 覆盖默认值
   （当前默认 'honeycomb-mongo'）。**拒绝继续**——还原是覆盖性操作，连不上
   容器时绝不能往下走到「假装目标库是空的」那一步。"

# 校验指纹（如果有 meta）
META="${ARCHIVE%.archive}.meta.json"
if [ -f "$META" ]; then
  want=$(python3 -c "import json,sys;print(json.load(open(sys.argv[1]))['sha256'])" "$META" 2>/dev/null || echo "")
  got=$(sha256sum "$ARCHIVE" | cut -d' ' -f1)
  if [ -n "$want" ] && [ "$want" != "$got" ]; then
    die "指纹对不上，archive 可能损坏或被改过：
   期望 $want
   实际 $got
   不还原一份来路不明的备份。"
  fi
  ok "指纹校验通过"
  info "这份备份当时的条数：$(python3 -c "import json,sys;d=json.load(open(sys.argv[1]));print(', '.join(f'{k}={v}' for k,v in sorted(d['collectionCounts'].items()) if v))" "$META" 2>/dev/null)"
else
  printf '⚠️  没有 .meta.json，无法校验指纹也无法对数——继续吗？\n' >&2
fi

# 目标库现状：**先告诉你要覆盖掉什么**，不要默默盖掉
EXISTING=$(docker exec "$CONTAINER" mongosh --quiet "$TARGET" --eval \
  'JSON.stringify(db.getCollectionNames().reduce((a,c)=>(a[c]=db[c].countDocuments({}),a),{}))' 2>/dev/null || echo '{}')
NONEMPTY=$(python3 -c "import json,sys;d=json.loads(sys.argv[1]);print(sum(d.values()))" "$EXISTING" 2>/dev/null || echo 0)

printf '\n──────────────────────────────────────────────\n'
printf '  还原目标库：%s\n' "$TARGET"
printf '  目标库现有文档数：%s\n' "$NONEMPTY"
if [ "${NONEMPTY:-0}" -gt 0 ]; then
  printf '  ⚠️  目标库**非空**。--drop 会先删掉同名集合再灌入。\n'
  printf '     现有：%s\n' "$(python3 -c "import json,sys;d=json.loads(sys.argv[1]);print(', '.join(f'{k}={v}' for k,v in sorted(d.items()) if v))" "$EXISTING" 2>/dev/null)"
fi
printf '──────────────────────────────────────────────\n\n'

if [ "${COCKPIT_RESTORE_YES:-}" != "1" ]; then
  printf '确认还原？输入目标库名以确认（照抄一遍，防手滑）： ' >&2
  read -r confirm
  [ "$confirm" = "$TARGET" ] || die "输入的是 '$confirm'，与目标库名 '$TARGET' 不符。已中止，什么都没动。"
fi

# 源库名从 meta 取；没有 meta 就从文件名推（`<db>-<时间戳>.archive`）。
# **不能写死** —— 备份哪个库、还原到哪个库是两回事。
SRC_DB=""
[ -f "$META" ] && SRC_DB=$(python3 -c "import json,sys;print(json.load(open(sys.argv[1])).get('db',''))" "$META" 2>/dev/null)
[ -n "$SRC_DB" ] || SRC_DB=$(basename "$ARCHIVE" | sed -E 's/-[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]+Z\.archive$//')
[ -n "$SRC_DB" ] || die "推断不出源库名，拒绝盲猜。archive 文件名应形如 <db>-<时间戳>.archive"

info "还原中… （$SRC_DB → $TARGET）"
# ⚠️ nsFrom 与 nsTo 的**星号数必须相等**，否则 mongorestore 报
#    "Different number of asterisks"。写 `*.*` → `<db>.*` 会挂（2 个 vs 1 个）。
#    2026-08-01 恢复演练当场撞到——**这就是"备份没演练过不算备份"的实例**。
docker exec -i "$CONTAINER" mongorestore --archive --gzip --drop \
  --nsFrom="${SRC_DB}.*" --nsTo="${TARGET}.*" < "$ARCHIVE" 2>&1 | tail -3 \
  || die "mongorestore 失败"

AFTER=$(docker exec "$CONTAINER" mongosh --quiet "$TARGET" --eval \
  'JSON.stringify(db.getCollectionNames().reduce((a,c)=>(a[c]=db[c].countDocuments({}),a),{}))' 2>/dev/null || echo '{}')
ok "还原完成。现在：$(python3 -c "import json,sys;d=json.loads(sys.argv[1]);print(', '.join(f'{k}={v}' for k,v in sorted(d.items()) if v))" "$AFTER" 2>/dev/null)"

# 对数：meta 里记了备份时的条数，不一致就响亮说出来
if [ -f "$META" ]; then
  python3 - "$META" "$AFTER" <<'PY'
import json, sys
meta = json.load(open(sys.argv[1]))
after = json.loads(sys.argv[2])
want = {k: v for k, v in meta.get("collectionCounts", {}).items() if v}
bad = [f"{k}: 备份时 {v}，还原后 {after.get(k, 0)}" for k, v in want.items() if after.get(k, 0) != v]
if bad:
    print("❌ 对数不一致：\n   " + "\n   ".join(bad))
    print("   还原可能不完整。别当成功。")
    sys.exit(1)
print("✅ 对数一致：每个集合的条数与备份时相同")
PY
fi
