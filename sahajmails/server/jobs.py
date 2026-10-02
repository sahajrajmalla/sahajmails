"""Background send jobs and the event stream that reports on them.

SMTP is blocking and FastAPI is async, so a run happens on a worker thread and
publishes events to any number of subscribed browser tabs over Server-Sent
Events. SSE rather than WebSockets: the traffic is one-directional, ``EventSource``
is built into every browser, and it reconnects on its own.

Progress events are coalesced. A 5,000-contact run would otherwise push 5,000
DOM updates through the browser for no benefit.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import threading
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

from ..errors import SahajMailsError
from ..models import SendResult
from ..sender import BulkSender, Progress

__all__ = ["Job", "JobManager"]

#: Minimum seconds between progress frames pushed to the browser.
_COALESCE_SECONDS = 0.2


@dataclass
class Job:
    """One running or finished send."""

    id: str
    sender: BulkSender
    total: int
    campaign_id: str | None = None
    status: str = "queued"
    started_at: float = field(default_factory=time.monotonic)
    error: str = ""
    progress: dict[str, Any] = field(default_factory=dict)
    recent: list[dict[str, Any]] = field(default_factory=list)

    @property
    def finished(self) -> bool:
        return self.status in {"completed", "failed", "cancelled"}

    def snapshot(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "status": self.status,
            "total": self.total,
            "campaign_id": self.campaign_id,
            "error": self.error,
            "progress": self.progress,
            "recent": self.recent[-40:],
        }


class JobManager:
    """Owns the running jobs and fans their events out to subscribers."""

    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}
        self._subscribers: dict[str, set[asyncio.Queue[str]]] = {}
        self._lock = threading.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """Remember the event loop so worker threads can publish into it."""
        self._loop = loop

    # -- lifecycle ---------------------------------------------------------

    def start(
        self,
        *,
        job_id: str,
        sender: BulkSender,
        total: int,
        campaign_id: str | None = None,
        run_id: str | None = None,
    ) -> Job:
        job = Job(id=job_id, sender=sender, total=total, campaign_id=campaign_id)
        with self._lock:
            self._jobs[job_id] = job
            self._subscribers.setdefault(job_id, set())

        thread = threading.Thread(
            target=self._run, args=(job, run_id), name=f"sahajmails-job-{job_id}", daemon=True
        )
        thread.start()
        return job

    def _run(self, job: Job, run_id: str | None) -> None:
        job.status = "running"
        self._publish(job.id, "status", job.snapshot())
        last_frame = 0.0

        def on_progress(progress: Progress) -> None:
            nonlocal last_frame
            job.progress = progress.to_dict()
            now = time.monotonic()
            # Always emit the final frame; throttle everything in between.
            if now - last_frame >= _COALESCE_SECONDS or progress.remaining == 0:
                last_frame = now
                self._publish(job.id, "progress", job.progress)

        def on_result(result: SendResult) -> None:
            entry = {
                "email": result.email,
                "status": str(result.status),
                "error": result.error,
                "row": result.row,
            }
            job.recent.append(entry)
            if result.status != "sent":
                # Failures are worth an immediate frame; successes can wait for
                # the next coalesced progress tick.
                self._publish(job.id, "result", entry)

        try:
            report = job.sender.send(
                run_id=run_id,
                campaign_id=job.campaign_id,
                on_progress=on_progress,
                on_result=on_result,
            )
        except SahajMailsError as exc:
            job.status = "failed"
            job.error = str(exc)
            hint = exc.hint or ""
            self._publish(job.id, "status", {**job.snapshot(), "hint": hint})
            self._publish(job.id, "done", {**job.snapshot(), "hint": hint})
            return
        except Exception as exc:  # pragma: no cover - unexpected, but must not hang the UI
            job.status = "failed"
            job.error = f"Unexpected error: {exc}"
            self._publish(job.id, "status", job.snapshot())
            self._publish(job.id, "done", job.snapshot())
            return

        job.status = "cancelled" if report.cancelled else "completed"
        job.error = report.aborted_reason
        payload = {
            **job.snapshot(),
            "run_id": report.run_id,
            "summary": report.summary(),
            "counts": report.counts,
            "failed": [{"email": r.email, "error": r.error} for r in report.failed[:200]],
        }
        self._publish(job.id, "done", payload)

    # -- control -----------------------------------------------------------

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def list(self) -> list[dict[str, Any]]:
        with self._lock:
            return [job.snapshot() for job in self._jobs.values()]

    def cancel(self, job_id: str) -> bool:
        job = self.get(job_id)
        if job is None or job.finished:
            return False
        job.sender.cancel()
        return True

    def pause(self, job_id: str) -> bool:
        job = self.get(job_id)
        if job is None or job.finished:
            return False
        job.sender.pause()
        self._publish(job_id, "status", {**job.snapshot(), "paused": True})
        return True

    def resume(self, job_id: str) -> bool:
        job = self.get(job_id)
        if job is None or job.finished:
            return False
        job.sender.resume()
        self._publish(job_id, "status", {**job.snapshot(), "paused": False})
        return True

    # -- events ------------------------------------------------------------

    def _publish(self, job_id: str, event: str, data: dict[str, Any]) -> None:
        """Push an event from a worker thread into the event loop."""
        loop = self._loop
        if loop is None or loop.is_closed():
            return
        frame = f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n"
        with self._lock:
            queues = list(self._subscribers.get(job_id, ()))
        for queue in queues:
            # The loop lives on another thread; this is the supported handoff.
            loop.call_soon_threadsafe(self._offer, queue, frame)

    @staticmethod
    def _offer(queue: asyncio.Queue[str], frame: str) -> None:
        # A tab that has stopped reading is not worth blocking the sender for.
        with contextlib.suppress(asyncio.QueueFull):
            queue.put_nowait(frame)

    async def stream(self, job_id: str) -> AsyncIterator[str]:
        """Yield SSE frames for one job until it finishes."""
        queue: asyncio.Queue[str] = asyncio.Queue(maxsize=512)
        with self._lock:
            self._subscribers.setdefault(job_id, set()).add(queue)

        job = self.get(job_id)
        if job is not None:
            yield f"event: status\ndata: {json.dumps(job.snapshot(), default=str)}\n\n"
            if job.finished:
                yield f"event: done\ndata: {json.dumps(job.snapshot(), default=str)}\n\n"

        try:
            while True:
                try:
                    frame = await asyncio.wait_for(queue.get(), timeout=20.0)
                except TimeoutError:
                    # Comment frame: keeps proxies and the browser from timing
                    # the connection out during a slow, quiet send.
                    yield ": keepalive\n\n"
                    current = self.get(job_id)
                    if current is not None and current.finished:
                        return
                    continue

                yield frame
                if frame.startswith("event: done"):
                    return
        finally:
            with self._lock:
                self._subscribers.get(job_id, set()).discard(queue)

    def prune(self, keep: int = 20) -> None:
        """Forget old finished jobs so a long-lived server does not grow forever."""
        with self._lock:
            finished = [j for j in self._jobs.values() if j.finished]
            for job in sorted(finished, key=lambda j: j.started_at)[: -keep or None]:
                self._jobs.pop(job.id, None)
                self._subscribers.pop(job.id, None)
