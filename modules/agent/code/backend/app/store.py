"""会话存储：一个租户一个目录，一个会话一个 JSON 文件（agent.chat.v1 第三节）。

目录 = <数据卷>/tenants/<sha256(租户 id) 前 32 位十六进制>/。opencode 的数据也落在同一个目录下
（runtime.py），所以一个租户的一切都在它自己的目录里，靠目录隔离，不靠过滤。

适配器自己存「用户说的话 + 助手的最终文字」：列会话、读历史不用拉起运行时进程；
opencode 那边的会话只当模型上下文用。单进程单事件循环，文件读写中间没有 await，不需要锁。
"""
from __future__ import annotations

import hashlib
import json
import os
import secrets
from datetime import datetime, timezone
from pathlib import Path

ID_RE = r"^[A-Za-z0-9_-]{1,128}$"


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def new_id(prefix: str) -> str:
    return prefix + secrets.token_urlsafe(12)


def tenant_dir(data_dir: str, tenant: str) -> Path:
    return Path(data_dir) / "tenants" / hashlib.sha256(tenant.encode()).hexdigest()[:32]


def _write(path: Path, obj) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False))
    os.replace(tmp, path)


class Store:
    def __init__(self, data_dir: str):
        self.data_dir = data_dir

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
        f = self._dir(tenant) / f"{sid}.json"
        try:
            return json.loads(f.read_text())
        except (OSError, ValueError):
            return None

    def create(self, tenant: str, title: str | None) -> dict:
        t = now()
        s = {"id": new_id("ses_"), "title": title, "createdAt": t, "updatedAt": t,
             "ocId": None, "messages": []}
        self.save(tenant, s)
        return s

    def save(self, tenant: str, s: dict) -> None:
        _write(self._dir(tenant) / f"{s['id']}.json", s)

    def delete(self, tenant: str, sid: str) -> dict | None:
        s = self.get(tenant, sid)
        if s:
            (self._dir(tenant) / f"{sid}.json").unlink(missing_ok=True)
        return s

    def add_message(self, tenant: str, sid: str, mid: str, role: str, text: str) -> None:
        s = self.get(tenant, sid)
        if s is None:          # 生成期间被删了：不复活
            return
        t = now()
        s["messages"].append({"id": mid, "role": role, "text": text, "createdAt": t})
        s["updatedAt"] = t
        self.save(tenant, s)
