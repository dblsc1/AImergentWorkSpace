#!/usr/bin/env bash
# nexus-core · 数据备份
#
#   bash code/backend/scripts/backup.sh [库名]
#
# dump 到独立的备份仓（不进业务仓 —— 备份进业务仓会让每次备份后工作树永远是脏的），
# 按 YYYY/M/D 分目录，自动 commit + push。
# 备份仓位置由 env COCKPIT_ARCHIVE_DIR 指定；缺省 `../honeycomb-archive`（仓根同级的
# 相对路径，只是本地占位方便直接试跑）。生产使用请显式把这个变量设置成你自己的
# 真实备份仓路径——缺省值不是给你偷懒用的。
#
# 为什么要 push：**只在本机存一份的备份不算备份**。机器挂了备份跟着一起没，
# 那时候才发现「原来一直没有备份」是最坏的时刻。
set -uo pipefail
HERE=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
BACKEND=$(cd -- "$HERE/.." && pwd -P)

die() { printf '❌ %s\n' "$*" >&2; exit 1; }
info() { printf 'ℹ️  %s\n' "$*"; }
ok() { printf '✅ %s\n' "$*"; }

DB=${1:-${NEXUS_DB_NAME:-nexus_core}}
URI=${NEXUS_MONGO_URI:-mongodb://127.0.0.1:27017/}
# 备份仓位置由 COCKPIT_ARCHIVE_DIR 指定；缺省 `../honeycomb-archive` 只是本地占位，
# 生产使用必须自己显式设置成真实的备份仓路径。
# 别写死成绝对路径——换台机器就废，而且门禁的绝对路径判据会当场抓住，
# 本行第一版就是这么被拦下的。
ARCHIVE_DIR=${COCKPIT_ARCHIVE_DIR:-../honeycomb-archive}

[ -d "$ARCHIVE_DIR/.git" ] || die "备份仓不存在或不是 git 仓：$ARCHIVE_DIR
   建它：mkdir -p $ARCHIVE_DIR && git -C $ARCHIVE_DIR init
   （它**不能**放在业务仓工作树内 —— 运行数据与备份混进业务仓会让工作树永远是脏的）"

command -v mongodump >/dev/null 2>&1 || MONGODUMP_IN_DOCKER=1
# 默认容器名：2026-08-10 撞过一次真事故——`deploy/docker-compose.yml` 的
# 这个默认值**会过期**：部署侧一改容器名，脚本还对着旧名字，
# 本脚本的默认值没跟着改，结果备份/删除前置备份**连不上任何容器**。
# 元数据统计步骤（下面的 COUNTS）**无条件**用 docker exec，不管
# `MONGODUMP_IN_DOCKER` 是否为真，所以这道检查必须在任何路径之前挡住。
CONTAINER=${COCKPIT_MONGO_CONTAINER:-honeycomb-mongo}
docker exec "$CONTAINER" true 2>/dev/null || die "Mongo 容器 '$CONTAINER' 连不上（不存在或未运行）。
   docker ps 看真实容器名，用 COCKPIT_MONGO_CONTAINER=<真实容器名> 覆盖默认值
   （当前默认 'honeycomb-mongo'）。**拒绝继续**——静默对着一个连不上的容器
   \"备份\"等于什么都没备份，但看起来像是跑完了（2026-08-10 P0 事故的教训）。"

TS=$(date -u +%Y-%m-%dT%H%M%SZ)
DAY_DIR="$ARCHIVE_DIR/$(date -u +%Y)/$(date -u +%-m)/$(date -u +%-d)"
mkdir -p "$DAY_DIR"
OUT="$DAY_DIR/${DB}-${TS}.archive"
META="$DAY_DIR/${DB}-${TS}.meta.json"

info "备份 $DB → $OUT"

if [ -n "${MONGODUMP_IN_DOCKER:-}" ]; then
  # 本机没装 mongodump 就借容器里的（镜像自带）。
  docker exec "$CONTAINER" mongodump --db "$DB" --archive --gzip > "$OUT" 2>/dev/null \
    || die "mongodump 失败（容器 $CONTAINER）。容器起着吗？docker ps"
else
  mongodump --uri "$URI" --db "$DB" --archive --gzip > "$OUT" \
    || die "mongodump 失败"
fi

[ -s "$OUT" ] || die "dump 出来是空文件 —— 拒绝把空备份当成功。已删。$(rm -f "$OUT")"

# 元数据：光有 archive 文件，事后没法确认「这份到底存了多少东西」。
# 还原时拿它对数，条数对不上就知道出问题了。
COUNTS=$(docker exec "$CONTAINER" mongosh --quiet "$DB" --eval \
  'JSON.stringify(db.getCollectionNames().reduce((a,c)=>(a[c]=db[c].countDocuments({}),a),{}))' 2>/dev/null || echo '{}')
python3 - "$META" "$DB" "$TS" "$OUT" "$COUNTS" <<'PY'
import sys, json, os, hashlib, pathlib
meta, db, ts, out, counts = sys.argv[1:6]
h = hashlib.sha256(pathlib.Path(out).read_bytes()).hexdigest()
try: c = json.loads(counts)
except Exception: c = {}
json.dump({"db": db, "takenAt": ts, "archive": os.path.basename(out),
           "bytes": os.path.getsize(out), "sha256": h, "collectionCounts": c},
          open(meta, "w"), ensure_ascii=False, indent=2)
PY

ok "$(du -h "$OUT" | cut -f1)  $(basename "$OUT")"
python3 -c "import json,sys;d=json.load(open(sys.argv[1]));print('   条数:', ', '.join(f'{k}={v}' for k,v in sorted(d['collectionCounts'].items()) if v))" "$META"

cd "$ARCHIVE_DIR"
git add -A
if git diff --cached --quiet; then
  info "无变化，不提交"
else
  git -c user.name=cockpit-backup -c user.email=backup@localhost \
    commit -q -m "backup($DB): $TS" || die "备份仓 commit 失败"
  ok "已 commit：$(git log --oneline -1)"
fi

if git remote get-url origin >/dev/null 2>&1; then
  if git push -q origin HEAD 2>/dev/null; then
    ok "已推远端"
  else
    printf '⚠️  推送失败——备份只在本机，机器挂了就一起没。手动跑：git -C %s push origin HEAD\n' "$ARCHIVE_DIR" >&2
  fi
else
  printf '⚠️  备份仓没配远端 —— 只在本机存一份的备份不算备份。\n' >&2
fi
