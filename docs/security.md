# Security

SahajMails can send mail as you. Everything below follows from that.

## What runs where

Everything runs on your machine. There is no account, no server of ours, and no
telemetry. Your contacts, drafts, credentials and send history live in
`~/.sahajmails/` and go nowhere else.

The one exception is AI personalization, which is off by default. Turn it on
with a cloud provider and the slot instructions and that contact's variables go
to that provider. Point it at Ollama or LM Studio instead and nothing leaves the
machine at all.

## The local server

Running an HTTP server that holds mail credentials is the real cost of having a
web UI, so it is defended properly:

**Loopback only.** `127.0.0.1`. Binding anything else needs `--allow-remote`,
prints a warning, and requires a password.

**A session token.** Generated at startup, delivered in the URL the terminal
prints, then exchanged for an `HttpOnly; SameSite=Strict` cookie. Without it,
any process on your machine — and any page in your browser — could drive the API.

**A `Host` allowlist.** The non-obvious one. Same-origin policy does *not*
protect a localhost server from DNS rebinding: a hostile site can point its own
domain at `127.0.0.1`, at which point its JavaScript is a same-origin peer.
Checking the `Host` header is what stops that.

**CSRF.** Cross-origin requests are refused outright, and state-changing routes
require a custom header that a plain cross-site form post cannot set.

**Uploads.** Size-capped, extension-checked, streamed to a temp directory, and
stored under a name we generate — an uploaded filename is never used as a path.
Contact tokens cannot escape the upload directory.

## Credentials

Passwords are **session-only by default** and forgotten when you stop the
server. Storing one is explicit and opt-in; it then goes to a file only your
user account can read, or to your OS keyring if you installed the extra.

Passwords and API keys are never logged, never returned by the API, never
included in a send report, and never emitted over the event stream. The settings
endpoint returns bullets.

Prefer the environment for automation:

```bash
export SAHAJMAILS_SMTP_PASSWORD='your app password'
```

## Contact data is untrusted

A spreadsheet can come from anywhere, so it is treated as hostile input:

- Rendered through a **sandboxed Jinja2 environment with autoescape**, so a cell
  containing `<script>` or a link tag becomes visible text, never live markup.
- Newlines are stripped from anything that becomes a header, so a cell cannot
  append a `Bcc:`.
- Custom headers cannot shadow `From`, `To`, `Subject`, `Date` or `Message-ID`.
- Attachment filenames are stripped of path components.
- Email previews render in a fully sandboxed iframe with no script execution and
  no network access.

1.x got two of these wrong: it interpolated contact data into HTML unescaped,
and it fed values to `re.sub` as a *replacement template*, so a value containing
`\1` crashed and `C:\temp` was corrupted. Both are fixed structurally by the
template engine and covered by regression tests.

## Plugins

A plugin is arbitrary Python running with your privileges — the same trust model
as any `pip install`. There is no sandbox and we do not pretend otherwise.
Plugins dropped into the local folder are disabled until you explicitly enable
them, because that is a much lower bar than installing a package.

## Reporting a vulnerability

See [SECURITY.md](../SECURITY.md). Please do not open a public issue, and never
paste an app password into a bug report.
