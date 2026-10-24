"""会话存储：一个租户一个目录（agent.chat.v1 第三节）。

目录 = <数据卷>/tenants/<sha256(租户 id) 前 32 位十六进制>/。opencode 的数据也落在同一个目录下
（runtime.py），所以一个租户的一切都在它自己的目录里，靠目录隔离，不靠过滤。

每个会话两个文件：`sessions/<id>.json` 只放元数据（列会话只读它们），`sessions/<id>.jsonl` 一行一条消息、
只追加。每个会话最多留 MAX_MESSAGES 条，超过 COMPACT_SLACK 条再整体压回去（旧的丢掉，`dropped` 记数，
读历史时 `truncated` 为真）。适配器自己存「用户说的话 + 助手的最终文字」：列会话、读历史不用拉起运行时；
opencode 那边的会话只当模型上下文。单进程单事件循环，文件读写中间没有 await，不需要锁。
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import os
import secrets
import shutil
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

ID_RE = r"^[A-Za-z0-9_-]{1,128}$"
MAX_MESSAGES = 500      # = GET ?limit 的上限：能读到的都留着
COMPACT_SLACK = 50      # 攒够这么多再压一次，不是每条都重写
META_KEYS = ("id", "title", "createdAt", "updatedAt", "ocId", "count", "dropped")


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def new_id(prefix: str) -> str:
    return prefix + secrets.token_urlsafe(12)


def tenant_dir(data_dir: str, tenant: str) -> Path:
    return Path(data_dir) / "tenants" / hashlib.sha256(tenant.encode()).hexdigest()[:32]


def _write(path: Path, text: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text)
    os.replace(tmp, path)


class Store:
    def __init__(self, data_dir: str):
        self.data_dir = data_dir
        self._seen: set[str] = set()

    def note_tenant(self, tenant: str) -> None:
        """记下见过的租户 id（目录名是哈希，反推不出）：后台认窗口的工人重启后也知道该替谁去问。"""
        if tenant in self._seen:
            return
        self._seen.add(tenant)
        f = tenant_dir(self.data_dir, tenant) / "tenant"
        if not f.exists():
            f.parent.mkdir(parents=True, exist_ok=True)
            _write(f, tenant)

    def tenants(self) -> list[str]:
        out = []
        for f in sorted(Path(self.data_dir).glob("tenants/*/tenant")):
            with contextlib.suppress(OSError):
                t = f.read_text().strip()
                if tenant_dir(self.data_dir, t) == f.parent:     # 文件里写的确实是这个目录的租户
                    out.append(t)
        return out

    def _dir(self, tenant: str) -> Path:
        d = tenant_dir(self.data_dir, tenant) / "sessions"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def list(self, tenant: str) -> list[dict]:
        out = []
        for f in self._dir(tenant).glob("*.json"):
            try:
                out.append(json.loads(f.read_text()))
            except (OSError, ValueError):
                continue
        return sorted(out, key=lambda s: s.get("updatedAt", ""), reverse=True)

    def get(self, tenant: str, sid: str) -> dict | None:
        # sid 已按 ID_RE 校验过（main.py），拼路径安全
        try:
            return json.loads((self._dir(tenant) / f"{sid}.json").read_text())
        except (OSError, ValueError):
            return None

    def messages(self, tenant: str, sid: str, limit: int) -> list[dict]:
        try:
            with open(self._dir(tenant) / f"{sid}.jsonl") as f:
                return [json.loads(line) for line in deque(f, maxlen=limit)]
        except OSError:
            return []

    def create(self, tenant: str, title: str | None) -> dict:
        t = now()
        s = {"id": new_id("ses_"), "title": title, "createdAt": t, "updatedAt": t,
             "ocId": None, "count": 0, "dropped": 0}
        self.save(tenant, s)
        return s

    def save(self, tenant: str, s: dict) -> None:
        _write(self._dir(tenant) / f"{s['id']}.json", json.dumps({k: s.get(k) for k in META_KEYS}, ensure_ascii=False))

    def delete(self, tenant: str, sid: str) -> dict | None:
        s = self.get(tenant, sid)
        if s:
            (self._dir(tenant) / f"{sid}.json").unlink(missing_ok=True)
            (self._dir(tenant) / f"{sid}.jsonl").unlink(missing_ok=True)
            shutil.rmtree(tenant_dir(self.data_dir, tenant) / "debug" / sid, ignore_errors=True)   # 调试记录（debug.py）
        return s

    def add_message(self, tenant: str, sid: str, mid: str, role: str, text: str) -> None:
        s = self.get(tenant, sid)
        if s is None:          # 生成期间被删了：不复活
            return
        t = now()
        log = self._dir(tenant) / f"{sid}.jsonl"
        with open(log, "a") as f:
            f.write(json.dumps({"id": mid, "role": role, "text": text, "createdAt": t}, ensure_ascii=False) + "\n")
        s["count"] = s.get("count", 0) + 1
        if s["count"] > MAX_MESSAGES + COMPACT_SLACK:
            keep = self.messages(tenant, sid, MAX_MESSAGES)
            _write(log, "".join(json.dumps(m, ensure_ascii=False) + "\n" for m in keep))
            s["dropped"] = s.get("dropped", 0) + s["count"] - len(keep)
            s["count"] = len(keep)
        s["updatedAt"] = t
        self.save(tenant, s)
