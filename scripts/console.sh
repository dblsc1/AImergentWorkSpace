#!/usr/bin/env bash
# 任务控制台数据源 → logs/console.json
# 只读 Git 里已有的留痕，不引入新数据源（避免成为第二个事实源）。
set -uo pipefail
root=$(git rev-parse --show-toplevel) || exit 2
cd "$root"
command -v jq >/dev/null || { echo '{"error":"jq not found"}'; exit 0; }

reports=$(git ls-files -z | tr '\0' '\n' | grep '/report\.json$' || true)
reports_json='[]'
if [ -n "$reports" ]; then
  reports_json=$(while IFS= read -r f; do
    [ -f "$f" ] || continue
    jq -c --arg path "$f" \
       --arg ts "$(git log -1 --format=%aI -- "$f" 2>/dev/null)" \
       --arg commit "$(git log -1 --format=%h -- "$f" 2>/dev/null)" '{
      path:$path, ts:$ts, commit:$commit,
      role:(.role//"?"), module:(.module//"?"), task:(.task//""),
      tier:(.tier//""), status:(.status//"?"), summary:(.summary//""),
      escalated:((.escalation//null)!=null),
      contract_touched:(.contract.touched//false),
      sub_reports:((.sub_reports//[])|length),
      objections:([(.sub_reports//[])[]|.objections//empty]|flatten|length),
      resumed:([(.sub_reports//[])[]|select(.resumed==true)]|length)
    }' "$f" 2>/dev/null
  done <<<"$reports" | jq -s 'sort_by(.ts)|reverse')
fi

events_json='[]'
[ -f logs/diary.jsonl ] && events_json=$(jq -s 'sort_by(.ts)|reverse|.[0:100]' logs/diary.jsonl 2>/dev/null || echo '[]')

jq -n --argjson reports "$reports_json" --argjson events "$events_json" \
      --arg generated "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
      --arg branch "$(git rev-parse --abbrev-ref HEAD)" '{
  generated:$generated, branch:$branch,
  reports:$reports, events:$events,
  metrics:{
    reports_total:($reports|length),
    rejected:([$reports[]|select(.status=="rejected")]|length),
    escalated:([$reports[]|select(.escalated)]|length),
    objections:([$reports[]|.objections]|add//0),
    resumed:([$reports[]|.resumed]|add//0),
    exam_fail:([$events[]|select(.event=="exam" and .pass==false)]|length),
    blocked:([$events[]|select(.event=="mission_blocked")]|length),
    override:([$events[]|select(.event=="mission_override")]|length)
  }
}'
