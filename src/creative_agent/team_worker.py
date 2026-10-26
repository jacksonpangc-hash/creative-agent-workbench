"""Persistent, leased worker. Billing requests require a current lease and grants."""

from __future__ import annotations

import logging
import threading
import uuid
from contextvars import ContextVar

from .team_auth import Actor, current_actor

active_lease: ContextVar[tuple | None] = ContextVar("team_active_lease", default=None)
log = logging.getLogger(__name__)


def external_guard():
    lease = active_lease.get()
    if lease is None:
        raise PermissionError("团队外部调用必须由持有有效租约的 worker 发起")
    store, claim = lease
    store.assert_lease(claim)


class TeamWorker:
    def __init__(self, store, execute, *, worker_id=None, lease_seconds=30):
        self.store, self.execute = store, execute
        self.worker_id = worker_id or str(uuid.uuid4())
        if not 5 <= lease_seconds <= 300:
            raise ValueError("租约时长必须为 5–300 秒")
        self.lease_seconds = lease_seconds

    def run_once(self):
        claim = self.store.claim(self.worker_id, self.lease_seconds)
        if claim is None:
            return False
        actor_token = current_actor.set(Actor(claim["actor_id"], claim["organization_id"], worker=True))
        lease_token = active_lease.set((self.store, claim))
        stopped = threading.Event()

        def heartbeat():
            while not stopped.wait(self.lease_seconds / 3):
                try:
                    if not self.store.heartbeat(claim, self.lease_seconds):
                        return
                except Exception:  # noqa: BLE001 - loss of heartbeat fails the next request closed
                    log.warning("Worker heartbeat failed; further provider calls will require a fresh check")
                    return

        thread = threading.Thread(target=heartbeat, daemon=True, name="team-lease-heartbeat")
        thread.start()
        try:
            self.store.assert_lease(claim)
            self.execute(claim["project_id"], claim["job_id"])
            job = self.store.job(claim["project_id"], claim["job_id"])
            terminal = job["state"] in {"succeeded", "failed", "cancelled", "needs_attention"}
            self.store.release(claim, done=terminal, delay_seconds=10)
        except Exception:
            # Never infer a safe retry after an unknown provider outcome. An expired
            # running/submitting lease is quarantined by the database claim function.
            log.exception("Team worker interrupted; lease recovery will inspect persisted job state")
        finally:
            stopped.set()
            thread.join(timeout=2)
            active_lease.reset(lease_token)
            current_actor.reset(actor_token)
        return True

    def run(self, stop=None):
        stop = stop or threading.Event()
        while not stop.is_set():
            if not self.run_once():
                stop.wait(1)
