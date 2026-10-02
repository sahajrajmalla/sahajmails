# Changelog

All notable changes to this project are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/).

## [2.0.0] — 2026-10-02

A complete rebuild. 1.x was a single Streamlit script; 2.0 is a library, a CLI
and a local web application.

### Breaking

- **Streamlit is gone.** `sahajmails.app` no longer exists. `sahajmails` still
  opens a web UI, now served by FastAPI on `http://localhost:8000`.
- **pandas is gone.** Contact loading uses the standard library plus `openpyxl`.
  Install drops from ~373 MB to ~49 MB, and from ~40 s to ~5 s.
- **Python 3.11+** is required (was 3.8, which the dependencies never supported).
- **Missing placeholders now raise by default** instead of rendering blank. Use
  `{{ name | default("there") }}`, or set the policy to `blank` for 1.x behaviour.
- **The legacy `.xls` format is refused** with a clear message. `xlrd` dropped
  support for it in 2020; save as `.xlsx` or CSV.
- `requirements.txt` is removed; `pyproject.toml` is the single source of truth.

### Added

- **Local web application** — campaigns, live preview with a recipient switcher,
  send console over Server-Sent Events, pause/resume/cancel, run history.
- **Four body formats** — formatted (rich text), plain, Markdown, and raw HTML.
- **AI personalization** via `{% ai %}` slots. The model fills only the labelled
  gap; review and edit before anything sends. Claude, OpenAI, and any
  OpenAI-compatible endpoint including local Ollama and LM Studio.
- **Resume** — an append-only ledger means a crashed run never double-sends.
- **Pre-flight** — renders every contact and reports problems before you send.
- **Deliverability** — plain-text alternative part, `List-Unsubscribe` and
  `List-Unsubscribe-Post` (RFC 8058), preheader text, SPF/DKIM/DMARC lookup,
  and a spam-content linter.
- **Suppression list** with CSV import and export, enforced on every send.
- **Cross-run duplicate detection** — warns when recipients were mailed recently.
- **Any SMTP provider** — presets for Gmail, Outlook, Yahoo, Zoho, iCloud,
  Fastmail, SES, Mailgun, Postmark, SendGrid, Resend, Brevo, plus custom.
- **Plugin system** — transports, AI backends, contact sources, template
  filters, send hooks, UI panels and CLI commands.
- **Real CLI** — `run`, `init`, `check`, `preview`, `send`, `runs`, `suppress`,
  `providers`, `where`, with `--dry-run` and `--resume`.
- Typed public API with `py.typed`.

### Fixed

- **Contact data was interpolated into HTML unescaped.** A spreadsheet cell
  containing `<script>` or a link tag reached recipients as live markup.
- **`re.sub` treated contact data as a replacement template.** `C:\temp` was
  corrupted; a value containing `\1` raised `re.error` and silently skipped
  the contact.
- **The progress bar could exceed 1.0 and crash the UI** — it divided by the
  DataFrame index label instead of a counter, so any filtered CSV broke it.
- **`sahajmails --help` never printed help.** Both branches of the dispatcher
  were identical, so it forwarded `--help` to Streamlit as a script argument.
- **`python -m sahajmails` produced no UI**, running Streamlit code bare.
- **Excel support was advertised but impossible** — `openpyxl` was never a
  declared dependency, so every `.xlsx` upload failed.
- **The wheel installed `docs/` and `tests/` into site-packages**, colliding
  with any other package of those names.
- **Attachments were re-encoded per recipient** — a 5 MB PDF to 1000 contacts
  meant 1000 base64 passes.
- **A 35-second stall on every SMTP connection** on machines with slow reverse
  DNS: `smtplib` calls `socket.getfqdn()` for the EHLO name.
- **A dropped connection abandoned the whole batch** with no record of who had
  already been mailed. Connections now reconnect, and 4xx is distinguished
  from 5xx so dead addresses are not retried.
- Clipboard copy and click-to-insert placeholders, documented in 1.x but never
  implemented.

### Security

- The web server binds loopback only; anything else requires `--allow-remote`.
- Session token, `Host` allowlist (DNS-rebinding defence), CSRF header
  requirement, and cross-origin rejection.
- Passwords are session-only by default; storing them is explicit and opt-in.
- Sandboxed template engine; uploaded filenames sanitised; upload size capped;
  contact tokens cannot escape the upload directory.
- Email previews render in a fully sandboxed iframe.

## [1.0.1] — 2025-11-13

Last Streamlit release. See the [1.x branch](https://github.com/sahajrajmalla/sahajmails/tree/main).
