"""Command line interface.

``sahajmails`` with no arguments starts the web UI, which is what the 1.x
console script did and what most people want. Everything the UI can do is also
a subcommand, so the tool scripts and automates cleanly.

Errors print as a message plus the hint attached to the exception, because
"authentication failed" without "create an app password" is not help.
"""

from __future__ import annotations

import signal
import sys
from collections.abc import Sequence
from pathlib import Path
from types import FrameType
from typing import Any

import click

from . import __version__
from .config import Settings, load_settings, write_config
from .contacts import ContactList, load_contacts
from .errors import SahajMailsError
from .message import load_attachment
from .models import Attachment
from .presets import PRESETS
from .sender import BulkSender, Progress, SendOptions
from .storage.db import Database, default_data_dir
from .storage.repo import Repository
from .template import EmailTemplate, MissingPolicy

__all__ = ["cli", "main"]

CONTEXT_SETTINGS = {"help_option_names": ["-h", "--help"], "max_content_width": 100}


# --------------------------------------------------------------------- output


def ok(message: str) -> None:
    click.echo(click.style("✓ ", fg="green") + message)


def warn(message: str) -> None:
    click.echo(click.style("! ", fg="yellow") + message, err=True)


def info(message: str) -> None:
    click.echo(click.style("· ", fg="cyan") + message)


def die(exc: SahajMailsError) -> None:
    """Print an error the way a person can act on, then exit non-zero."""
    click.echo(click.style("✗ ", fg="red") + click.style(str(exc), bold=True), err=True)
    if exc.hint:
        click.echo(f"  {click.style(exc.hint, fg='yellow')}", err=True)
    raise SystemExit(1)


def _open_repo() -> Repository:
    return Repository(Database())


def _resolve_settings(**overrides: Any) -> Settings:
    repo = _open_repo()
    try:
        return load_settings(
            {k: v for k, v in overrides.items() if v not in (None, ())},
            stored=repo.all_settings(),
        )
    finally:
        repo.db.close()


def _read_template(value: str) -> str:
    """Accept either a path or the template text itself."""
    candidate = Path(value).expanduser()
    if candidate.is_file():
        return candidate.read_text(encoding="utf-8")
    return value


def _load_attachments(paths: Sequence[str]) -> list[Attachment]:
    return [load_attachment(p) for p in paths]


# ------------------------------------------------------------------ commands


@click.group(context_settings=CONTEXT_SETTINGS, invoke_without_command=True)
@click.version_option(__version__, "-V", "--version", prog_name="sahajmails")
@click.pass_context
def cli(ctx: click.Context) -> None:
    """Simple, secure bulk email.

    Run with no arguments to open the web interface.
    """
    if ctx.invoked_subcommand is None:
        ctx.invoke(run)


@cli.command()
@click.option("--host", default="127.0.0.1", help="Interface to bind. Keep this local.")
@click.option("--port", default=8000, type=int, help="Port to listen on.")
@click.option("--no-browser", is_flag=True, help="Do not open a browser window.")
@click.option("--allow-remote", is_flag=True, help="Permit binding a non-local address.")
@click.option("--reload", is_flag=True, hidden=True, help="Auto-reload (development).")
def run(host: str, port: int, no_browser: bool, allow_remote: bool, reload: bool) -> None:
    """Start the web interface."""
    from .server.launcher import serve

    try:
        serve(
            host=host,
            port=port,
            open_browser=not no_browser,
            allow_remote=allow_remote,
            reload=reload,
        )
    except SahajMailsError as exc:
        die(exc)


@cli.command()
@click.option("--force", is_flag=True, help="Overwrite existing files.")
def init(force: bool) -> None:
    """Create a starter config, template and contact list here."""
    config_path = Path("sahajmails.toml")
    template_path = Path("email.md")
    contacts_path = Path("contacts.csv")

    existing = [p for p in (config_path, template_path, contacts_path) if p.exists()]
    if existing and not force:
        warn(f"Already here: {', '.join(str(p) for p in existing)}. Use --force to overwrite.")
        raise SystemExit(1)

    write_config(
        config_path,
        {
            "sender_email": "you@gmail.com",
            "sender_name": "Your Name",
            "provider": "gmail",
            "missing_policy": "error",
        },
    )
    template_path.write_text(_STARTER_TEMPLATE, encoding="utf-8")
    contacts_path.write_text(_STARTER_CONTACTS, encoding="utf-8")

    ok(f"Wrote {config_path}, {template_path} and {contacts_path}")
    click.echo()
    info("Next: put your app password in the environment, then preview")
    click.echo("    export SAHAJMAILS_SMTP_PASSWORD='your app password'")
    click.echo("    sahajmails preview contacts.csv -t email.md -s 'Hello {{ first_name }}'")


@cli.command()
def providers() -> None:
    """List the built-in SMTP provider presets."""
    width = max(len(p.label) for p in PRESETS.values())
    for preset in PRESETS.values():
        limit = f"{preset.daily_limit:,}/day" if preset.daily_limit else "no stated limit"
        host = f"{preset.host}:{preset.port}" if preset.host else "(you supply the host)"
        click.echo(
            f"  {click.style(preset.label.ljust(width), bold=True)}  "
            f"{click.style(preset.key.ljust(18), fg='cyan')}  {host}  ·  {limit}"
        )


@cli.command()
@click.argument("contacts_file", type=click.Path(exists=True, dir_okay=False))
@click.option("-t", "--template", required=True, help="Template file or inline text.")
@click.option("-s", "--subject", required=True, help="Subject line. May use placeholders.")
@click.option("-n", "--number", default=1, type=int, help="How many contacts to render.")
@click.option("--html", "as_html", is_flag=True, help="Show the HTML part instead of text.")
@click.option("--missing", type=click.Choice(["error", "blank", "keep"]), default="error")
def preview(
    contacts_file: str, template: str, subject: str, number: int, as_html: bool, missing: str
) -> None:
    """Render the first few emails without sending anything."""
    try:
        contacts = load_contacts(contacts_file)
        email_template = EmailTemplate(
            subject=subject, body=_read_template(template), missing_policy=MissingPolicy(missing)
        )
        for contact in contacts.contacts[: max(1, number)]:
            rendered = email_template.render(contact)
            click.echo(click.style("─" * 72, dim=True))
            click.echo(click.style(f"To:      {contact.email}", fg="cyan"))
            click.echo(click.style(f"Subject: {rendered.subject}", bold=True))
            click.echo()
            click.echo(rendered.html if as_html else rendered.text)
        click.echo(click.style("─" * 72, dim=True))
    except SahajMailsError as exc:
        die(exc)


@cli.command()
@click.argument("contacts_file", type=click.Path(exists=True, dir_okay=False))
@click.option("-t", "--template", help="Template file or inline text.")
@click.option("-s", "--subject", help="Subject line.")
@click.option("--from", "sender_email", help="Sending address.")
@click.option("--provider", help="Provider preset key. See `sahajmails providers`.")
@click.option("--missing", type=click.Choice(["error", "blank", "keep"]), default="error")
def check(
    contacts_file: str,
    template: str | None,
    subject: str | None,
    sender_email: str | None,
    provider: str | None,
    missing: str,
) -> None:
    """Pre-flight everything before a single email goes out."""
    from .preflight import run_preflight

    try:
        settings = _resolve_settings(sender_email=sender_email, provider=provider)
        contacts = load_contacts(contacts_file)
        email_template = (
            EmailTemplate(
                subject=subject or "",
                body=_read_template(template),
                missing_policy=MissingPolicy(missing),
            )
            if template
            else None
        )
        report = run_preflight(contacts=contacts, template=email_template, settings=settings)
    except SahajMailsError as exc:
        die(exc)
        return

    for finding in report.findings:
        style = {"error": "red", "warning": "yellow", "info": "cyan"}[finding.level]
        marker = {"error": "✗", "warning": "!", "info": "·"}[finding.level]
        click.echo(click.style(f"{marker} ", fg=style) + finding.message)
        if finding.hint:
            click.echo(f"  {click.style(finding.hint, dim=True)}")

    click.echo()
    if report.blocking:
        click.echo(click.style(f"{len(report.blocking)} problem(s) must be fixed.", fg="red"))
        raise SystemExit(1)
    ok(f"Ready to send to {len(contacts):,} contact(s).")


@cli.command()
@click.argument("contacts_file", type=click.Path(exists=True, dir_okay=False))
@click.option("-t", "--template", required=True, help="Template file or inline text.")
@click.option("-s", "--subject", required=True, help="Subject line.")
@click.option("--from", "sender_email", help="Sending address.")
@click.option("--from-name", help="Display name on the From header.")
@click.option("--reply-to", help="Reply-To address.")
@click.option("--provider", help="Provider preset key.")
@click.option("-a", "--attach", multiple=True, type=click.Path(exists=True, dir_okay=False))
@click.option("--dry-run", is_flag=True, help="Write .eml files instead of sending.")
@click.option("--outdir", default="outbox", help="Where --dry-run writes.")
@click.option("--limit", type=int, help="Only send to the first N contacts.")
@click.option("--resume", "resume_run", help="Resume a previous run by id.")
@click.option("--rate", type=int, help="Messages per minute.")
@click.option("--concurrency", type=int, help="Parallel connections.")
@click.option("--missing", type=click.Choice(["error", "blank", "keep"]), default="error")
@click.option("-y", "--yes", is_flag=True, help="Skip the confirmation prompt.")
def send(
    contacts_file: str,
    template: str,
    subject: str,
    sender_email: str | None,
    from_name: str | None,
    reply_to: str | None,
    provider: str | None,
    attach: tuple[str, ...],
    dry_run: bool,
    outdir: str,
    limit: int | None,
    resume_run: str | None,
    rate: int | None,
    concurrency: int | None,
    missing: str,
    yes: bool,
) -> None:
    """Send a campaign."""
    repo = _open_repo()
    try:
        settings = load_settings(
            {
                k: v
                for k, v in {
                    "sender_email": sender_email,
                    "sender_name": from_name,
                    "reply_to": reply_to,
                    "provider": provider,
                    "rate_per_minute": rate,
                    "concurrency": concurrency,
                    "missing_policy": missing,
                }.items()
                if v is not None
            },
            stored=repo.all_settings(),
        )
        if not dry_run:
            settings.validate()

        contacts = load_contacts(contacts_file)
        email_template = EmailTemplate(
            subject=subject,
            body=_read_template(template),
            footer=settings.footer,
            missing_policy=MissingPolicy(missing),
        )
        attachments = _load_attachments(attach)

        suppressed = repo.suppressed()
        if suppressed:
            before = len(contacts)
            contacts = contacts.filter_out(suppressed)
            removed = before - len(contacts)
            if removed:
                info(f"Skipping {removed} suppressed address(es).")

        if not contacts:
            warn("Nothing to send: every contact is suppressed.")
            raise SystemExit(1)

        if not yes and not _confirm(contacts, repo, settings, dry_run, limit):
            click.echo("Cancelled.")
            return

        sender = BulkSender(
            template=email_template,
            contacts=contacts,
            settings=settings,
            repo=repo,
            attachments=attachments,
            options=SendOptions(
                dry_run=dry_run,
                outdir=outdir,
                limit=limit,
                rate_per_minute=settings.rate_per_minute,
                concurrency=settings.concurrency,
            ),
        )
        _install_interrupt_handler(sender)

        with click.progressbar(
            length=limit or len(contacts), label="Sending", show_pos=True
        ) as bar:
            last = 0

            def advance(progress: Progress) -> None:
                nonlocal last
                bar.update(progress.done - last)
                last = progress.done

            report = sender.send(run_id=resume_run, on_progress=advance)

    except SahajMailsError as exc:
        die(exc)
        return
    finally:
        repo.db.close()

    click.echo()
    if report.failed:
        warn(f"{len(report.failed)} failed:")
        for result in report.failed[:10]:
            click.echo(f"    {result.email}: {result.error}")
        if len(report.failed) > 10:
            click.echo(f"    … and {len(report.failed) - 10} more")

    if report.cancelled:
        warn("Stopped early.")
        info(f"Resume with:  sahajmails send {contacts_file} --resume {report.run_id} …")
    if report.aborted_reason:
        warn(report.aborted_reason)

    ok(f"{report.summary()}  (run {report.run_id})")
    if report.failed:
        raise SystemExit(1)


def _confirm(
    contacts: ContactList,
    repo: Repository,
    settings: Settings,
    dry_run: bool,
    limit: int | None,
) -> bool:
    """Show exactly what is about to happen, then ask."""
    total = min(limit, len(contacts)) if limit else len(contacts)
    targets = contacts.contacts[:total]

    click.echo()
    verb = "Write .eml files for" if dry_run else "Send to"
    click.echo(click.style(f"{verb} {total:,} contact(s)", bold=True))
    for contact in targets[:3]:
        click.echo(f"    {contact.email}")
    if total > 3:
        click.echo(f"    … and {total - 3:,} more")

    if not dry_run:
        click.echo(f"  From:     {settings.from_address}")
        click.echo(f"  Provider: {settings.preset.label} ({settings.smtp_host})")
        click.echo(f"  Rate:     {settings.rate_per_minute}/min")

        # The most common bulk-email mistake, and free to catch with a ledger.
        recent = repo.recently_mailed([c.email for c in targets], days=30)
        if recent:
            warn(f"{len(recent)} of these received mail from you in the last 30 days.")

        if settings.daily_limit and total > settings.daily_limit:
            warn(
                f"{settings.preset.label} allows about {settings.daily_limit:,}/day; "
                f"this run is {total:,}."
            )
    click.echo()
    return bool(click.confirm("Proceed?", default=False))


def _install_interrupt_handler(sender: BulkSender) -> None:
    """Ctrl-C finishes the in-flight message, then stops cleanly.

    Killing the process outright would leave the ledger without a record of a
    message the server may already have accepted.
    """

    def handler(signum: int, frame: FrameType | None) -> None:
        click.echo()
        warn("Stopping after the current message… (Ctrl-C again to force)")
        sender.cancel()
        signal.signal(signal.SIGINT, signal.SIG_DFL)

    signal.signal(signal.SIGINT, handler)


# ------------------------------------------------------------- runs and lists


@cli.group()
def runs() -> None:
    """Inspect past sends."""


@runs.command("list")
@click.option("--limit", default=20, type=int)
def runs_list(limit: int) -> None:
    """Show recent runs."""
    repo = _open_repo()
    try:
        rows = repo.list_runs(limit)
    finally:
        repo.db.close()

    if not rows:
        info("No runs yet.")
        return
    for row in rows:
        status = str(row["status"])
        colour = {"completed": "green", "cancelled": "yellow", "failed": "red"}.get(status, "white")
        click.echo(
            f"  {click.style(str(row['id']), fg='cyan')}  "
            f"{click.style(status.ljust(9), fg=colour)}  "
            f"{row['sent']} sent, {row['failed']} failed  ·  {str(row['started_at'])[:19]}"
        )


@runs.command("show")
@click.argument("run_id")
@click.option("--failed-only", is_flag=True)
def runs_show(run_id: str, failed_only: bool) -> None:
    """Show every recipient in one run."""
    repo = _open_repo()
    try:
        run_row = repo.get_run(run_id)
        if run_row is None:
            warn(f"No run called {run_id!r}.")
            raise SystemExit(1)
        results = repo.failed_recipients(run_id) if failed_only else repo.run_results(run_id)
    finally:
        repo.db.close()

    for result in results:
        status = str(result["status"])
        colour = {"sent": "green", "failed": "red"}.get(status, "yellow")
        line = f"  {click.style(status.ljust(7), fg=colour)}  {result['email']}"
        if result["error"]:
            line += click.style(f"  {result['error']}", dim=True)
        click.echo(line)
    click.echo()
    info(f"{len(results)} row(s) in run {run_id}")


@cli.group()
def suppress() -> None:
    """Manage the do-not-contact list."""


@suppress.command("add")
@click.argument("emails", nargs=-1, required=True)
@click.option("--reason", default="manual")
def suppress_add(emails: tuple[str, ...], reason: str) -> None:
    """Never send to these addresses again."""
    repo = _open_repo()
    try:
        repo.suppress_many([(e, reason, "") for e in emails])
    finally:
        repo.db.close()
    ok(f"Suppressed {len(emails)} address(es).")


@suppress.command("list")
def suppress_list() -> None:
    """Show suppressed addresses."""
    repo = _open_repo()
    try:
        rows = repo.list_suppressed()
    finally:
        repo.db.close()
    if not rows:
        info("Nothing suppressed.")
        return
    for row in rows:
        click.echo(f"  {row['email']}  {click.style(str(row['reason']), dim=True)}")


@suppress.command("remove")
@click.argument("emails", nargs=-1, required=True)
def suppress_remove(emails: tuple[str, ...]) -> None:
    """Allow sending to these addresses again."""
    repo = _open_repo()
    try:
        for address in emails:
            repo.unsuppress(address)
    finally:
        repo.db.close()
    ok(f"Removed {len(emails)} address(es).")


@cli.command()
def where() -> None:
    """Show where sahajmails keeps its files."""
    click.echo(f"  data      {default_data_dir()}")
    click.echo(f"  database  {default_data_dir() / 'sahajmails.db'}")


_STARTER_TEMPLATE = """\
Hi {{ first_name | default("there") }},

This is a starter template. Everything in it is identical for every recipient
except the placeholders, which are filled in from your contacts file.

Columns become placeholders: {{ company }} works if you have a "company" column,
and casing does not matter — {{ firstName }} and {{ first_name }} are the same.

{% if company %}Nice to see someone from {{ company }} on this list.{% endif %}

To personalize one sentence with AI, wrap it in an ai block. The model fills
only that gap and never sees the rest of the message:

{% ai "opener" max_words=25 fallback="Hope your week is going well." %}
One friendly sentence for {{ first_name }} at {{ company }}. No greeting.
{% endai %}

Best,
Your Name
"""

_STARTER_CONTACTS = """\
email,first_name,company
alice@example.com,Alice,Acme Corp
bob@example.com,Bob,Startup XYZ
"""


def main(argv: Sequence[str] | None = None) -> int:
    """Console-script entry point."""
    try:
        cli.main(args=list(argv) if argv is not None else None, standalone_mode=False)
    except click.ClickException as exc:
        exc.show()
        return exc.exit_code
    except click.Abort:
        click.echo("Aborted.", err=True)
        return 130
    except SahajMailsError as exc:
        die(exc)
    except SystemExit as exc:
        return int(exc.code or 0)
    return 0


if __name__ == "__main__":
    sys.exit(main())
