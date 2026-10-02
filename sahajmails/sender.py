"""Bulk send orchestration.

The important property here is that **a run can always be resumed and never
double-sends**. Every attempt is written to the ledger the moment it completes,
so a crash, a Ctrl-C, or a closed laptop lid loses at most the one message in
flight. Restarting with the same run id skips everything already delivered.

1.x had no ledger. A dropped connection at contact 600 of 1000 left the operator
with no record of who had been mailed and no safe way to retry.
"""

from __future__ import annotations

import queue
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from .config import Settings
from .contacts import ContactList
from .errors import AuthenticationError, SahajMailsError, SendError, TransportError
from .message import PreparedAttachment, build_message, prepare_attachments
from .models import Attachment, Contact, SendResult, SendStatus
from .storage.repo import Repository
from .template import EmailTemplate, MissingPolicy
from .transport import Transport, create_transport

__all__ = ["BulkSender", "Progress", "SendOptions", "SendReport"]

ProgressCallback = Callable[["Progress"], None]
ResultCallback = Callable[[SendResult], None]


@dataclass(slots=True)
class SendOptions:
    """Knobs for one run."""

    dry_run: bool = False
    """Write ``.eml`` files instead of sending. Nothing leaves the machine."""

    outdir: str = "outbox"
    limit: int | None = None
    concurrency: int = 1
    rate_per_minute: int = 30
    max_retries: int = 2
    """Retries for *transient* failures only. A 5xx is never retried."""

    retry_delay: float = 5.0
    stop_after_failures: int = 0
    """Abort the run after this many consecutive failures. 0 disables."""


@dataclass(slots=True)
class Progress:
    """A snapshot of an in-flight run, handed to the progress callback."""

    total: int
    sent: int = 0
    failed: int = 0
    skipped: int = 0
    current: str = ""
    started_at: float = field(default_factory=time.monotonic)
    paused: bool = False

    @property
    def done(self) -> int:
        return self.sent + self.failed + self.skipped

    @property
    def remaining(self) -> int:
        return max(0, self.total - self.done)

    @property
    def fraction(self) -> float:
        # 1.x divided by the DataFrame index label rather than a counter, so a
        # filtered CSV could produce progress > 1.0 and crash the UI.
        return min(1.0, self.done / self.total) if self.total else 1.0

    @property
    def elapsed(self) -> float:
        return time.monotonic() - self.started_at

    @property
    def rate_per_minute(self) -> float:
        minutes = self.elapsed / 60
        return self.done / minutes if minutes > 0.01 else 0.0

    @property
    def eta_seconds(self) -> float | None:
        if not self.done or not self.remaining:
            return None
        return self.remaining * (self.elapsed / self.done)

    def to_dict(self) -> dict[str, Any]:
        return {
            "total": self.total,
            "sent": self.sent,
            "failed": self.failed,
            "skipped": self.skipped,
            "done": self.done,
            "remaining": self.remaining,
            "fraction": round(self.fraction, 4),
            "elapsed": round(self.elapsed, 1),
            "rate_per_minute": round(self.rate_per_minute, 1),
            "eta_seconds": round(self.eta_seconds) if self.eta_seconds else None,
            "current": self.current,
            "paused": self.paused,
        }


@dataclass(slots=True)
class SendReport:
    """What happened, once a run is over."""

    run_id: str
    results: list[SendResult] = field(default_factory=list)
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    finished_at: datetime | None = None
    cancelled: bool = False
    aborted_reason: str = ""

    @property
    def sent(self) -> list[SendResult]:
        return [r for r in self.results if r.status is SendStatus.SENT]

    @property
    def failed(self) -> list[SendResult]:
        return [r for r in self.results if r.status is SendStatus.FAILED]

    @property
    def skipped(self) -> list[SendResult]:
        return [r for r in self.results if r.status is SendStatus.SKIPPED]

    @property
    def counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for result in self.results:
            out[str(result.status)] = out.get(str(result.status), 0) + 1
        return out

    @property
    def ok(self) -> bool:
        return not self.failed and not self.cancelled and not self.aborted_reason

    def summary(self) -> str:
        parts = [f"{len(self.sent)} sent"]
        if self.failed:
            parts.append(f"{len(self.failed)} failed")
        if self.skipped:
            parts.append(f"{len(self.skipped)} skipped")
        return ", ".join(parts)


class BulkSender:
    """Renders and delivers a template to a contact list."""

    def __init__(
        self,
        *,
        template: EmailTemplate,
        contacts: ContactList,
        settings: Settings,
        repo: Repository | None = None,
        attachments: Sequence[Attachment] = (),
        ai_slots: Mapping[str, Mapping[str, str]] | None = None,
        options: SendOptions | None = None,
    ) -> None:
        self.template = template
        self.contacts = contacts
        self.settings = settings
        self.repo = repo
        self.options = options or SendOptions(
            rate_per_minute=settings.rate_per_minute,
            concurrency=settings.concurrency,
        )
        #: Approved AI text, keyed by recipient then slot name.
        self.ai_slots = {k.casefold(): dict(v) for k, v in (ai_slots or {}).items()}

        # Encoded once here, reused by every message in the run.
        self._attachments: list[PreparedAttachment] = prepare_attachments(attachments)

        self._cancel = threading.Event()
        self._pause = threading.Event()
        self._pause.set()  # set == running
        self._lock = threading.Lock()
        self._consecutive_failures = 0
        self._abort_reason = ""
        self._startup_error: SahajMailsError | None = None

    # -- control ----------------------------------------------------------

    def cancel(self) -> None:
        """Stop after the in-flight message. The ledger stays consistent."""
        self._cancel.set()
        self._pause.set()

    def pause(self) -> None:
        self._pause.clear()

    def resume(self) -> None:
        self._pause.set()

    @property
    def cancelled(self) -> bool:
        return self._cancel.is_set()

    @property
    def paused(self) -> bool:
        return not self._pause.is_set()

    # -- main entry point --------------------------------------------------

    def send(
        self,
        *,
        run_id: str | None = None,
        campaign_id: str | None = None,
        on_progress: ProgressCallback | None = None,
        on_result: ResultCallback | None = None,
    ) -> SendReport:
        """Run the batch.

        Args:
            run_id: Resume this run instead of starting a new one. Recipients
                already marked ``sent`` in the ledger are skipped.
            on_progress: Called after every attempt.
            on_result: Called with each individual outcome.

        Returns:
            A :class:`SendReport`. Individual failures do not raise; a failure
            that makes the whole run pointless (bad credentials) does.
        """
        targets = list(self.contacts.contacts)
        if self.options.limit is not None:
            targets = targets[: self.options.limit]

        already: set[str] = set()
        resuming = run_id is not None
        if resuming and self.repo is not None:
            already = self.repo.already_sent(run_id or "")
            if already:
                targets = [c for c in targets if c.email not in already]

        if run_id is None and self.repo is not None:
            run_id = self.repo.create_run(
                total=len(targets),
                campaign_id=campaign_id,
                config={
                    "dry_run": self.options.dry_run,
                    "provider": self.settings.provider,
                    "rate_per_minute": self.options.rate_per_minute,
                },
            )
        run_id = run_id or f"local-{int(time.time())}"

        report = SendReport(run_id=run_id)
        progress = Progress(total=len(targets))

        if not targets:
            report.finished_at = datetime.now(UTC)
            if self.repo is not None and resuming:
                self.repo.finish_run(run_id)
            return report

        try:
            if self.options.concurrency > 1:
                self._run_pool(targets, run_id, report, progress, on_progress, on_result)
            else:
                self._run_serial(targets, run_id, report, progress, on_progress, on_result)

            # A connection that never opened is a configuration problem, not a
            # delivery outcome. Raise it rather than handing back an empty
            # report the caller might mistake for success. A failure *during*
            # the run is different: there are real results worth keeping, so
            # that path records `aborted_reason` instead.
            if self._startup_error is not None and not report.results:
                raise self._startup_error
        finally:
            report.finished_at = datetime.now(UTC)
            report.cancelled = self._cancel.is_set()
            report.aborted_reason = self._abort_reason
            if self.repo is not None:
                status = "cancelled" if report.cancelled else "completed"
                if self._abort_reason:
                    status = "failed"
                self.repo.finish_run(run_id, status)

        return report

    # -- execution strategies ---------------------------------------------

    def _run_serial(
        self,
        targets: Sequence[Contact],
        run_id: str,
        report: SendReport,
        progress: Progress,
        on_progress: ProgressCallback | None,
        on_result: ResultCallback | None,
    ) -> None:
        transport = self._make_transport()
        try:
            try:
                transport.open()
            except SahajMailsError as exc:
                self._startup_error = exc
                return
            for contact in targets:
                if not self._wait_if_paused():
                    break
                result = self._attempt(transport, contact)
                self._commit(run_id, result, report, progress, on_progress, on_result)
                if self._abort_reason:
                    break
        finally:
            transport.close()

    def _run_pool(
        self,
        targets: Sequence[Contact],
        run_id: str,
        report: SendReport,
        progress: Progress,
        on_progress: ProgressCallback | None,
        on_result: ResultCallback | None,
    ) -> None:
        """Several connections in parallel, one transport per worker.

        Transports are not shared between threads: a single SMTP connection is
        a stateful, strictly sequential protocol.
        """
        work: queue.Queue[Contact | None] = queue.Queue()
        for contact in targets:
            work.put(contact)

        workers = min(self.options.concurrency, len(targets))
        for _ in range(workers):
            work.put(None)

        def worker() -> None:
            transport = self._make_transport()
            try:
                transport.open()
            except SahajMailsError as exc:
                with self._lock:
                    if self._startup_error is None:
                        self._startup_error = exc
                    self._abort_reason = self._abort_reason or str(exc)
                self._cancel.set()
                return
            try:
                while True:
                    contact = work.get()
                    try:
                        if contact is None:
                            return
                        if self._cancel.is_set() or not self._wait_if_paused():
                            return
                        result = self._attempt(transport, contact)
                        self._commit(run_id, result, report, progress, on_progress, on_result)
                    finally:
                        work.task_done()
            finally:
                transport.close()

        threads = [
            threading.Thread(target=worker, name=f"sahajmails-send-{i}", daemon=True)
            for i in range(workers)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

    def _wait_if_paused(self) -> bool:
        """Block while paused. Returns ``False`` if the run was cancelled."""
        while not self._pause.wait(timeout=0.25):
            if self._cancel.is_set():
                return False
        return not self._cancel.is_set()

    # -- one message -------------------------------------------------------

    def _make_transport(self) -> Transport:
        if self.options.dry_run:
            return create_transport("file", outdir=self.options.outdir)
        kwargs = self.settings.transport_kwargs()
        kwargs["rate_per_minute"] = self.options.rate_per_minute
        return create_transport("smtp", **kwargs)

    def _attempt(self, transport: Transport, contact: Contact) -> SendResult:
        """Deliver to one contact, retrying transient failures."""
        try:
            message_bytes = self._build(contact)
        except SahajMailsError as exc:
            # A render failure is the operator's bug, not the recipient's;
            # never retry it.
            return SendResult(
                email=contact.email,
                status=SendStatus.FAILED,
                error=str(exc),
                row=contact.row,
            )

        last_error = ""
        for attempt in range(self.options.max_retries + 1):
            try:
                message_id = transport.send(
                    message_bytes,
                    sender=self.settings.from_address,
                    recipient=contact.email,
                )
                return SendResult(
                    email=contact.email,
                    status=SendStatus.SENT,
                    message_id=message_id,
                    row=contact.row,
                )
            except AuthenticationError as exc:
                # Every remaining message will fail the same way. Stop.
                with self._lock:
                    self._abort_reason = str(exc)
                self._cancel.set()
                return SendResult(
                    email=contact.email,
                    status=SendStatus.FAILED,
                    error=str(exc),
                    row=contact.row,
                )
            except SendError as exc:
                last_error = str(exc)
                if exc.permanent or attempt >= self.options.max_retries:
                    break
            except TransportError as exc:
                last_error = str(exc)
                if attempt >= self.options.max_retries:
                    break
            if self._cancel.is_set():
                break
            time.sleep(self.options.retry_delay * (attempt + 1))

        return SendResult(
            email=contact.email,
            status=SendStatus.FAILED,
            error=last_error or "unknown error",
            row=contact.row,
        )

    def _build(self, contact: Contact) -> Any:
        rendered = self.template.render(contact, ai_slots=self.ai_slots.get(contact.email, {}))
        return build_message(
            sender=self.settings.from_address,
            sender_name=self.settings.sender_name,
            recipient=contact.email,
            recipient_name=contact.get("name") or contact.get("first_name"),
            rendered=rendered,
            reply_to=self.settings.reply_to or None,
            attachments=self._attachments,
            unsubscribe_mailto=self.settings.unsubscribe_mailto or None,
            unsubscribe_url=self.settings.unsubscribe_url or None,
        )

    def _commit(
        self,
        run_id: str,
        result: SendResult,
        report: SendReport,
        progress: Progress,
        on_progress: ProgressCallback | None,
        on_result: ResultCallback | None,
    ) -> None:
        """Persist one outcome, then notify.

        The ledger write happens before the callbacks so that a listener
        crashing cannot lose the record of a message that really was sent.
        """
        if self.repo is not None:
            self.repo.record(run_id, result)

        with self._lock:
            report.results.append(result)
            if result.status is SendStatus.SENT:
                progress.sent += 1
                self._consecutive_failures = 0
            elif result.status is SendStatus.FAILED:
                progress.failed += 1
                self._consecutive_failures += 1
            else:
                progress.skipped += 1
            progress.current = result.email
            progress.paused = self.paused

            if (
                self.options.stop_after_failures
                and self._consecutive_failures >= self.options.stop_after_failures
            ):
                self._abort_reason = (
                    f"Stopped after {self._consecutive_failures} failures in a row."
                )
                self._cancel.set()

        if on_result is not None:
            on_result(result)
        if on_progress is not None:
            on_progress(progress)


def send(
    *,
    contacts: str,
    template: str,
    subject: str,
    settings: Settings | None = None,
    dry_run: bool = False,
    **kwargs: Any,
) -> SendReport:
    """Send a campaign in one call.

    The convenience wrapper the README shows::

        from sahajmails import send
        send(contacts="contacts.csv", template="email.md", subject="Hi {{ first_name }}")
    """
    from pathlib import Path

    from .config import load_settings
    from .contacts import load_contacts

    resolved = settings or load_settings()
    body = Path(template).read_text(encoding="utf-8") if Path(template).is_file() else template

    email_template = EmailTemplate(
        subject=subject,
        body=body,
        footer=resolved.footer,
        missing_policy=MissingPolicy(resolved.missing_policy),
    )
    sender = BulkSender(
        template=email_template,
        contacts=load_contacts(contacts),
        settings=resolved,
        options=SendOptions(
            dry_run=dry_run,
            rate_per_minute=resolved.rate_per_minute,
            concurrency=resolved.concurrency,
            **kwargs,
        ),
    )
    return sender.send()
