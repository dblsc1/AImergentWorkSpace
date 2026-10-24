"""对账的后台循环（契约 v2.38「四」：nexus-core 进程内，每 60 秒一轮）。守护线程，由 ``main.lifespan`` 起停；
``NEXUS_RECONCILE_LOOP=0`` 关掉（测试里手动调 ``reconcile.run_all`` / ``run_round``）。一轮出错只记日志，下一轮照跑。
"""

from __future__ import annotations

import logging
import threading

from ... import config
from . import reconcile

INTERVAL = 60.0
log = logging.getLogger("uvicorn.error")


def start() -> tuple[threading.Thread, threading.Event] | None:
    if not config.settings.reconcile_loop:
        return None
    stop = threading.Event()

    def loop() -> None:
        while not stop.wait(INTERVAL):
            try:
                reconcile.run_all()
            except Exception:  # noqa: BLE001
                log.exception("对账后台循环这一轮失败，60 秒后重来")

    thread = threading.Thread(target=loop, name="reconcile-loop", daemon=True)
    thread.start()
    return thread, stop


def stop(handle: tuple[threading.Thread, threading.Event] | None) -> None:
    if handle:
        handle[1].set()
        handle[0].join(timeout=5)
