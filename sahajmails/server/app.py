"""The FastAPI application.

Every route is a thin wrapper over the library — there is no email logic in this
layer. That is deliberate: the UI, the CLI and ``import sahajmails`` all drive
exactly the same code, so behaviour cannot drift between them.
"""

from __future__ import annotations

import asyncio
import json
import shutil
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Any

from fastapi import Depends, FastAPI, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .. import __version__
from ..ai import PROVIDERS as AI_PROVIDERS
from ..ai import SlotEngine, create_backend, slots_to_csv
from ..config import Settings, load_settings
from ..contacts import ContactList, load_contacts
from ..errors import SahajMailsError
from ..message import load_attachment
from ..models import Contact
from ..plugins import load_plugins, local_plugin_dir
from ..preflight import run_preflight
from ..presets import PRESETS
from ..sender import BulkSender, SendOptions
from ..storage.db import Database, default_data_dir
from ..storage.repo import Repository, new_id
from ..template import BodyFormat, EmailTemplate, MissingPolicy
from .jobs import JobManager
from .security import SecurityConfig, SecurityMiddleware

__all__ = ["create_app"]

STATIC_DIR = Path(__file__).parent / "static"
MAX_UPLOAD_BYTES = 50 * 1024 * 1024

SAMPLE_CONTACTS = """\
email,first_name,company,role
ada@example.com,Ada,Analytical Engines,Engineer
grace@example.com,Grace,Naval Systems,Rear Admiral
alan@example.com,Alan,Bletchley Labs,Cryptanalyst
katherine@example.com,Katherine,Flight Research,Mathematician
"""


# ------------------------------------------------------------------- schemas


class SettingsIn(BaseModel):
    """A settings patch.

    Every field defaults to empty and updates use ``exclude_unset``, so a
    request changes only what it actually names. Defaulting ``provider`` to
    "gmail" here would mean any partial update silently reset a user's custom
    SMTP server back to Gmail.
    """

    sender_email: str = ""
    sender_name: str = ""
    reply_to: str = ""
    provider: str = ""
    smtp_host: str = ""
    smtp_port: int = 0
    smtp_username: str = ""
    smtp_password: str = ""
    security: str = ""
    rate_per_minute: int = 0
    concurrency: int = 0
    unsubscribe_mailto: str = ""
    unsubscribe_url: str = ""
    footer: str = ""
    ai_provider: str = ""
    ai_model: str = ""
    ai_api_key: str = ""
    ai_base_url: str = ""
    remember_password: bool = False


class CampaignIn(BaseModel):
    id: str | None = None
    name: str = "Untitled campaign"
    subject: str = ""
    body: str = ""
    preheader: str = ""
    footer: str = ""
    contacts: str = ""
    config: dict[str, Any] = Field(default_factory=dict)


class PreviewIn(BaseModel):
    subject: str = ""
    body: str = ""
    preheader: str = ""
    footer: str = ""
    contacts: str = ""
    index: int = 0
    format: str = "markdown"
    missing_policy: str = "blank"


class SendIn(BaseModel):
    campaign_id: str | None = None
    subject: str
    body: str
    preheader: str = ""
    footer: str = ""
    contacts: str
    format: str = "markdown"
    attachments: list[str] = Field(default_factory=list)
    dry_run: bool = False
    limit: int | None = None
    resume_run: str | None = None
    test_to: str | None = None
    missing_policy: str = "error"


class SuppressIn(BaseModel):
    emails: list[str]
    reason: str = "manual"


class AITestIn(BaseModel):
    provider: str = ""
    model: str = ""
    api_key: str = ""
    base_url: str = ""


class AIGenerateIn(BaseModel):
    campaign_id: str
    subject: str = ""
    body: str
    preheader: str = ""
    format: str = "markdown"
    contacts: str
    limit: int | None = None


class PluginToggleIn(BaseModel):
    name: str
    enabled: bool = True


# --------------------------------------------------------------------- state


class AppState:
    """Process-wide singletons, created once at startup."""

    def __init__(self, data_dir: Path | None = None) -> None:
        self.data_dir = data_dir or default_data_dir()
        self.uploads = self.data_dir / "uploads"
        self.uploads.mkdir(parents=True, exist_ok=True)
        self.db = Database(self.data_dir / "sahajmails.db")
        self.repo = Repository(self.db)
        self.jobs = JobManager()
        #: Passwords live here unless the user asked to remember them.
        self.session_secrets: dict[str, str] = {}
        self.plugins = load_plugins(self.data_dir, enabled=self._enabled_plugins())

    def _enabled_plugins(self) -> list[str]:
        raw = Repository(self.db).get_setting("enabled_plugins", "")
        return [n for n in raw.split(",") if n]

    def ai_backend(self, overrides: Mapping[str, str] | None = None) -> Any:
        """Build the configured AI backend, or raise with a useful hint."""
        settings = self.settings()
        o = dict(overrides or {})
        provider = o.get("provider") or settings.ai_provider
        if not provider:
            raise SahajMailsError(
                "No AI provider configured.",
                hint="Choose one on the AI page, or use the offline demo provider.",
            )
        return create_backend(
            provider,
            api_key=o.get("api_key") or settings.ai_api_key,
            model=o.get("model") or settings.ai_model or "",
            base_url=o.get("base_url") or settings.ai_base_url,
        )

    def settings(self, overrides: dict[str, Any] | None = None) -> Settings:
        merged = dict(overrides or {})
        if "smtp_password" not in merged and self.session_secrets.get("smtp_password"):
            merged["smtp_password"] = self.session_secrets["smtp_password"]
        if "ai_api_key" not in merged and self.session_secrets.get("ai_api_key"):
            merged["ai_api_key"] = self.session_secrets["ai_api_key"]
        return load_settings(merged, stored=self.repo.all_settings())

    def contact_path(self, token: str) -> Path:
        """Resolve an upload id to a path, refusing anything outside uploads/."""
        candidate = (self.uploads / token).resolve()
        if not str(candidate).startswith(str(self.uploads.resolve())):
            raise HTTPException(400, "Invalid contacts reference.")
        if not candidate.is_file():
            raise HTTPException(404, "That contact list is no longer on disk.")
        return candidate

    def load_contacts(self, token: str) -> ContactList:
        return load_contacts(self.contact_path(token))

    def close(self) -> None:
        self.db.close()


def get_state(request: Request) -> AppState:
    state: AppState = request.app.state.app_state
    return state


State = Annotated[AppState, Depends(get_state)]


# ----------------------------------------------------------------- factory


def create_app(*, security: SecurityConfig | None = None, data_dir: Path | None = None) -> FastAPI:
    """Build the application."""
    config = security or SecurityConfig()
    state = AppState(data_dir)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        # Worker threads publish SSE frames into this loop, so it has to be
        # captured once the loop actually exists.
        state.jobs.bind_loop(asyncio.get_running_loop())
        try:
            yield
        finally:
            state.close()

    app = FastAPI(
        title="SahajMails",
        version=__version__,
        description="Local bulk email. Everything runs on your machine.",
        docs_url="/api/docs",
        redoc_url=None,
        openapi_url="/api/openapi.json",
        lifespan=lifespan,
    )
    app.state.app_state = state
    app.state.security = config

    @app.exception_handler(SahajMailsError)
    async def _domain_error(request: Request, exc: SahajMailsError) -> JSONResponse:
        # The hint is the whole point of the exception hierarchy: surface it.
        return JSONResponse({"error": str(exc), "hint": exc.hint or ""}, status_code=400)

    _register_routes(app)

    if STATIC_DIR.is_dir():
        app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    app.add_middleware(SecurityMiddleware, config=config)
    return app


def _register_routes(app: FastAPI) -> None:
    api = app.router

    # -- shell ------------------------------------------------------------

    @api.get("/", include_in_schema=False)
    async def index() -> FileResponse:
        page = STATIC_DIR / "index.html"
        if not page.is_file():  # pragma: no cover - only if the wheel is broken
            raise HTTPException(500, "The web UI is missing from this installation.")
        return FileResponse(page, headers={"Cache-Control": "no-store"})

    @api.get("/health", include_in_schema=False)
    async def health() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    # -- settings ---------------------------------------------------------

    @api.get("/api/state")
    async def read_state(state: State) -> dict[str, Any]:
        settings = state.settings()
        stored = state.repo.all_settings()
        return {
            "version": __version__,
            "settings": settings.redacted(),
            "has_password": bool(settings.smtp_password),
            "configured": bool(stored.get("sender_email")),
            "campaigns": state.repo.list_campaigns(),
            "suppressed_count": len(state.repo.suppressed()),
            "ai_providers": AI_PROVIDERS,
            "plugins_dir": str(local_plugin_dir(state.data_dir)),
            "providers": [
                {
                    "key": p.key,
                    "label": p.label,
                    "host": p.host,
                    "port": p.port,
                    "security": str(p.security),
                    "daily_limit": p.daily_limit,
                    "rate_per_minute": p.rate_per_minute,
                    "setup_hint": p.setup_hint,
                    "docs_url": p.docs_url,
                }
                for p in PRESETS.values()
            ],
        }

    @api.post("/api/settings")
    async def write_settings(payload: SettingsIn, state: State) -> dict[str, Any]:
        values = payload.model_dump(exclude_unset=True)
        remember = bool(values.pop("remember_password", False))
        password = str(values.pop("smtp_password", "") or "")

        for key, value in values.items():
            if value in (None, "", 0):
                # Explicitly cleared: fall back to the preset default.
                state.repo.delete_setting(key)
            else:
                state.repo.set_setting(key, str(value))

        if password:
            if remember:
                # Explicit, opt-in, and the UI says plainly what this means.
                state.repo.set_setting("smtp_password", password)
            else:
                state.session_secrets["smtp_password"] = password
                state.repo.delete_setting("smtp_password")

        return {"ok": True, "settings": state.settings().redacted()}

    @api.post("/api/settings/test")
    async def test_connection(payload: SettingsIn, state: State) -> dict[str, Any]:
        overrides = {
            k: v
            for k, v in payload.model_dump(exclude_unset=True).items()
            if v not in (None, "", 0)
        }
        overrides.pop("remember_password", None)
        settings = state.settings(overrides)
        try:
            settings.validate()
        except SahajMailsError as exc:
            return {"ok": False, "error": str(exc), "hint": exc.hint or ""}

        from ..transport import create_transport

        transport = create_transport("smtp", **settings.transport_kwargs())
        try:
            await asyncio.to_thread(transport.verify)
        except SahajMailsError as exc:
            return {"ok": False, "error": str(exc), "hint": exc.hint or ""}
        finally:
            transport.close()
        return {"ok": True, "message": f"Signed in to {settings.smtp_host} successfully."}

    # -- contacts ---------------------------------------------------------

    @api.post("/api/contacts/upload")
    async def upload_contacts(state: State, file: UploadFile) -> dict[str, Any]:
        name = Path(file.filename or "contacts.csv").name
        suffix = Path(name).suffix.casefold() or ".csv"
        if suffix not in {".csv", ".tsv", ".txt", ".xlsx", ".xlsm", ".xls"}:
            raise HTTPException(400, f"Unsupported file type: {suffix}")

        token = f"{new_id('list-')}{suffix}"
        target = state.uploads / token
        size = 0
        with target.open("wb") as handle:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > MAX_UPLOAD_BYTES:
                    handle.close()
                    target.unlink(missing_ok=True)
                    raise HTTPException(413, "That file is larger than 50 MB.")
                handle.write(chunk)

        try:
            contacts = load_contacts(target, filename=name)
        except SahajMailsError:
            target.unlink(missing_ok=True)
            raise

        return {"token": token, "filename": name, **_contacts_payload(contacts)}

    @api.get("/api/contacts/{token}")
    async def read_contacts(token: str, state: State) -> dict[str, Any]:
        return {"token": token, **_contacts_payload(state.load_contacts(token))}

    @api.post("/api/contacts/sample")
    async def sample_contacts(state: State) -> dict[str, Any]:
        """Write the built-in demo list so a new user can try the flow at once."""
        token = f"{new_id('list-')}.csv"
        (state.uploads / token).write_text(SAMPLE_CONTACTS, encoding="utf-8")
        contacts = state.load_contacts(token)
        return {"token": token, "filename": "sample-contacts.csv", **_contacts_payload(contacts)}

    # -- campaigns --------------------------------------------------------

    @api.get("/api/campaigns")
    async def list_campaigns(state: State) -> list[dict[str, Any]]:
        return state.repo.list_campaigns()

    @api.post("/api/campaigns")
    async def save_campaign(payload: CampaignIn, state: State) -> dict[str, Any]:
        campaign_id = state.repo.save_campaign(payload.model_dump())
        saved = state.repo.get_campaign(campaign_id)
        return saved or {"id": campaign_id}

    @api.get("/api/campaigns/{campaign_id}")
    async def read_campaign(campaign_id: str, state: State) -> dict[str, Any]:
        campaign = state.repo.get_campaign(campaign_id)
        if campaign is None:
            raise HTTPException(404, "No such campaign.")
        return campaign

    @api.delete("/api/campaigns/{campaign_id}")
    async def delete_campaign(campaign_id: str, state: State) -> dict[str, bool]:
        state.repo.delete_campaign(campaign_id)
        return {"ok": True}

    # -- preview and preflight --------------------------------------------

    @api.post("/api/preview")
    async def preview(payload: PreviewIn, state: State) -> dict[str, Any]:
        contacts = state.load_contacts(payload.contacts) if payload.contacts else None
        contact = (
            contacts.contacts[min(payload.index, len(contacts) - 1)]
            if contacts
            else Contact(email="you@example.com", fields={})
        )
        template = _build_template(payload.model_dump(), state, payload.missing_policy)
        rendered = template.render(contact)
        return {
            "email": contact.email,
            "subject": rendered.subject,
            "html": rendered.html,
            "text": rendered.text,
            "preheader": rendered.preheader,
            "index": payload.index,
            "total": len(contacts) if contacts else 1,
        }

    @api.post("/api/preflight")
    async def preflight(payload: SendIn, state: State) -> dict[str, Any]:
        contacts = state.load_contacts(payload.contacts)
        settings = state.settings()
        template = _build_template(payload.model_dump(), state, payload.missing_policy)
        report = await asyncio.to_thread(
            run_preflight,
            contacts=contacts,
            template=template,
            settings=settings,
            suppressed=state.repo.suppressed(),
            recently_mailed=state.repo.recently_mailed([c.email for c in contacts]),
            check_dns=True,
        )
        return report.to_dict()

    # -- sending ----------------------------------------------------------

    @api.post("/api/send")
    async def start_send(payload: SendIn, state: State) -> dict[str, Any]:
        settings = state.settings()
        if not payload.dry_run:
            settings.validate()

        template = _build_template(payload.model_dump(), state, payload.missing_policy)
        contacts = state.load_contacts(payload.contacts)

        if payload.test_to:
            # A test send goes to one address but keeps the first contact's data,
            # so what you receive is a real personalized message.
            sample = contacts.contacts[0] if contacts else Contact(email=payload.test_to, fields={})
            contacts = ContactList(
                contacts=[Contact(email=payload.test_to, fields=sample.fields, row=sample.row)],
                columns=contacts.columns,
                labels=contacts.labels,
            )
        else:
            suppressed = state.repo.suppressed()
            if suppressed:
                contacts = contacts.filter_out(suppressed)

        if not contacts:
            raise HTTPException(400, "No contacts left to send to.")

        attachments = [load_attachment(state.contact_path(a)) for a in payload.attachments]

        sender = BulkSender(
            template=template,
            contacts=contacts,
            settings=settings,
            repo=state.repo,
            attachments=attachments,
            ai_slots=state.repo.get_slots(payload.campaign_id) if payload.campaign_id else None,
            options=SendOptions(
                dry_run=payload.dry_run,
                outdir=str(state.data_dir / "outbox"),
                limit=payload.limit,
                rate_per_minute=settings.rate_per_minute,
                concurrency=settings.concurrency,
            ),
        )
        job_id = new_id("job-")
        state.jobs.start(
            job_id=job_id,
            sender=sender,
            total=payload.limit or len(contacts),
            campaign_id=payload.campaign_id,
            run_id=payload.resume_run,
        )
        state.jobs.prune()
        return {"job_id": job_id, "total": len(contacts)}

    @api.get("/api/jobs/{job_id}/events")
    async def job_events(job_id: str, state: State) -> StreamingResponse:
        if state.jobs.get(job_id) is None:
            raise HTTPException(404, "No such job.")
        return StreamingResponse(
            state.jobs.stream(job_id),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-store",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    @api.get("/api/jobs/{job_id}")
    async def job_status(job_id: str, state: State) -> dict[str, Any]:
        job = state.jobs.get(job_id)
        if job is None:
            raise HTTPException(404, "No such job.")
        return job.snapshot()

    @api.post("/api/jobs/{job_id}/{action}")
    async def job_control(job_id: str, action: str, state: State) -> dict[str, bool]:
        handler = {
            "cancel": state.jobs.cancel,
            "pause": state.jobs.pause,
            "resume": state.jobs.resume,
        }.get(action)
        if handler is None:
            raise HTTPException(400, f"Unknown action {action!r}.")
        return {"ok": handler(job_id)}

    # -- history and suppression ------------------------------------------

    @api.get("/api/runs")
    async def list_runs(state: State, limit: int = 50) -> list[dict[str, Any]]:
        return state.repo.list_runs(limit)

    @api.get("/api/runs/{run_id}")
    async def read_run(run_id: str, state: State) -> dict[str, Any]:
        run = state.repo.get_run(run_id)
        if run is None:
            raise HTTPException(404, "No such run.")
        return {"run": run, "results": state.repo.run_results(run_id)}

    @api.get("/api/suppression")
    async def list_suppression(state: State) -> list[dict[str, Any]]:
        return state.repo.list_suppressed()

    @api.post("/api/suppression")
    async def add_suppression(payload: SuppressIn, state: State) -> dict[str, int]:
        count = state.repo.suppress_many([(e, payload.reason, "") for e in payload.emails])
        return {"added": count}

    @api.get("/api/suppression/export")
    async def export_suppression(state: State) -> PlainTextResponse:
        """Download the do-not-contact list as CSV."""
        import csv
        import io

        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow(["email", "reason", "detail", "added_at"])
        for row in state.repo.list_suppressed(limit=1_000_000):
            writer.writerow([row["email"], row["reason"], row["detail"], row["added_at"]])
        return PlainTextResponse(
            buffer.getvalue(),
            media_type="text/csv",
            headers={"Content-Disposition": 'attachment; filename="suppressed.csv"'},
        )

    @api.post("/api/suppression/import")
    async def import_suppression(state: State, file: UploadFile) -> dict[str, int]:
        """Accept a CSV or a plain list of addresses, one per line."""
        raw = await file.read(MAX_UPLOAD_BYTES + 1)
        if len(raw) > MAX_UPLOAD_BYTES:
            raise HTTPException(413, "That file is too large.")
        text = raw.decode("utf-8-sig", errors="replace")

        found: list[tuple[str, str, str]] = []
        for line in text.splitlines():
            # Tolerate CSV with headers, quoted fields, or a bare address list.
            first = line.split(",")[0].strip().strip('"').casefold()
            if not first or first == "email" or "@" not in first:
                continue
            found.append((first, "imported", file.filename or ""))
        return {"added": state.repo.suppress_many(found)}

    # -- ai ---------------------------------------------------------------

    @api.post("/api/ai/test")
    async def ai_test(payload: AITestIn, state: State) -> dict[str, Any]:
        backend = state.ai_backend(payload.model_dump(exclude_unset=True))
        try:
            completion = await asyncio.to_thread(
                backend.complete,
                system="",
                prompt="Reply with exactly: ready",
                max_tokens=16,
                temperature=0.0,
            )
        except SahajMailsError as exc:
            return {"ok": False, "error": str(exc), "hint": exc.hint or ""}
        finally:
            backend.close()
        return {
            "ok": True,
            "message": f"{backend.name} responded using {backend.model}.",
            "sample": completion.text[:120],
        }

    @api.post("/api/ai/estimate")
    async def ai_estimate(payload: AIGenerateIn, state: State) -> dict[str, Any]:
        template = _build_template(payload.model_dump(), state, "blank")
        contacts = state.load_contacts(payload.contacts)
        backend = state.ai_backend()
        try:
            engine = SlotEngine(backend)
            return engine.estimate(template, contacts)
        finally:
            backend.close()

    @api.post("/api/ai/generate")
    async def ai_generate(payload: AIGenerateIn, state: State) -> dict[str, Any]:
        """Fill every slot for every contact, then store it for review."""
        template = _build_template(payload.model_dump(), state, "blank")
        if not template.slots():
            raise HTTPException(400, "This template has no {% ai %} blocks.")

        contacts = state.load_contacts(payload.contacts)
        backend = state.ai_backend()
        try:
            engine = SlotEngine(
                backend,
                cache_get=state.repo.cache_get,
                cache_put=lambda key, value: state.repo.cache_put(key, value, model=backend.model),
                concurrency=4,
            )
            fill = await asyncio.to_thread(
                engine.fill, template=template, contacts=contacts, limit=payload.limit
            )
        finally:
            backend.close()

        state.repo.clear_slots(payload.campaign_id)
        state.repo.save_slots(payload.campaign_id, fill.rows())
        return {
            "generated": fill.generated,
            "cached": fill.cache_hits,
            "fallbacks": fill.fallbacks,
            "tokens_in": fill.tokens_in,
            "tokens_out": fill.tokens_out,
            "rows": state.repo.slot_rows(payload.campaign_id),
        }

    @api.get("/api/ai/slots/{campaign_id}")
    async def ai_slots(campaign_id: str, state: State) -> list[dict[str, Any]]:
        return state.repo.slot_rows(campaign_id)

    @api.post("/api/ai/slots/{campaign_id}")
    async def ai_save_slots(
        campaign_id: str, rows: list[dict[str, Any]], state: State
    ) -> dict[str, int]:
        """Persist the operator's edits. This text is what actually gets sent."""
        entries = [
            (
                str(r.get("email", "")),
                str(r.get("slot", "")),
                str(r.get("text", "")),
                True,
                str(r.get("generated", "")),
            )
            for r in rows
            if r.get("email") and r.get("slot")
        ]
        state.repo.save_slots(campaign_id, entries)
        return {"saved": len(entries)}

    @api.get("/api/ai/slots/{campaign_id}/export")
    async def ai_export_slots(campaign_id: str, state: State) -> PlainTextResponse:
        from ..ai.engine import SlotResult

        rows = [
            SlotResult(
                email=str(r["email"]),
                slot=str(r["slot"]),
                text=str(r["text"]),
                source="approved" if r["approved"] else "generated",
            )
            for r in state.repo.slot_rows(campaign_id)
        ]
        return PlainTextResponse(
            slots_to_csv(rows),
            media_type="text/csv",
            headers={"Content-Disposition": 'attachment; filename="review.csv"'},
        )

    # -- plugins ----------------------------------------------------------

    @api.get("/api/plugins")
    async def list_plugins(state: State) -> list[dict[str, Any]]:
        return state.plugins.describe()

    @api.post("/api/plugins/toggle")
    async def toggle_plugin(payload: PluginToggleIn, state: State) -> dict[str, bool]:
        enabled = {n for n in state.repo.get_setting("enabled_plugins", "").split(",") if n}
        if payload.enabled:
            enabled.add(payload.name)
        else:
            enabled.discard(payload.name)
        state.repo.set_setting("enabled_plugins", ",".join(sorted(enabled)))
        # Loading happens at startup: importing arbitrary code into a running
        # process cannot be undone, so a restart is the honest answer.
        return {"ok": True, "restart_required": True}

    @api.delete("/api/suppression/{email}")
    async def remove_suppression(email: str, state: State) -> dict[str, bool]:
        state.repo.unsuppress(email)
        return {"ok": True}


# ------------------------------------------------------------------ helpers


def _contacts_payload(contacts: ContactList) -> dict[str, Any]:
    report = contacts.report
    return {
        "count": len(contacts),
        "columns": [{"key": c, "label": contacts.label_for(c)} for c in contacts.columns],
        "email_column": contacts.email_column,
        "preview": [
            {"email": c.email, "row": c.row, "fields": c.fields} for c in contacts.contacts[:10]
        ],
        "issues": {
            "invalid": [
                {"row": row, "value": value, "reason": reason}
                for row, value, reason in report.invalid[:50]
            ],
            "duplicates": len(report.duplicates),
            "blank_rows": len(report.blank_rows),
            "total_rows": report.total_rows,
        },
    }


def _build_template(payload: dict[str, Any], state: AppState, policy: str) -> EmailTemplate:
    settings = state.settings()
    try:
        body_format = BodyFormat(str(payload.get("format") or "markdown"))
    except ValueError:
        body_format = BodyFormat.MARKDOWN
    return EmailTemplate(
        subject=str(payload.get("subject") or ""),
        body=str(payload.get("body") or ""),
        preheader=str(payload.get("preheader") or ""),
        footer=str(payload.get("footer") or settings.footer or ""),
        body_format=body_format,
        missing_policy=MissingPolicy(policy),
    )


def export_sample(target: Path) -> None:
    """Write the demo contact list. Used by the first-run flow and by tests."""
    target.write_text(SAMPLE_CONTACTS, encoding="utf-8")


def copy_static(destination: Path) -> None:  # pragma: no cover - packaging helper
    shutil.copytree(STATIC_DIR, destination, dirs_exist_ok=True)


def dumps(value: Any) -> str:
    return json.dumps(value, default=str)
