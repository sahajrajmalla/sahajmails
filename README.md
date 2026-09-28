<div align="center">

<img src="https://raw.githubusercontent.com/sahajrajmalla/sahajmails/main/sahajmails/server/static/img/logo.svg" width="72" alt="SahajMails">

# SahajMails

**Personalized bulk email that runs on your machine.**

*Sahaj* means simple and natural. So does this.

[![PyPI](https://img.shields.io/pypi/v/sahajmails?color=0e8f5d)](https://pypi.org/project/sahajmails/)
[![Python](https://img.shields.io/badge/python-3.11%2B-0e8f5d)](https://python.org)
[![License](https://img.shields.io/badge/license-MIT-0e8f5d)](LICENSE)
[![CI](https://github.com/sahajrajmalla/sahajmails/actions/workflows/ci.yml/badge.svg)](https://github.com/sahajrajmalla/sahajmails/actions)

</div>

---

```bash
pip install sahajmails
sahajmails
```

That's it. A local web app opens. Load a spreadsheet, write your email, send.

Your contacts, your password and your drafts never leave your computer. There is
no account, no server of ours, and no telemetry.

---

## What it does

|  | |
|---|---|
| **Works with your own mailbox** | Gmail, Outlook, Yahoo, Zoho, iCloud, Fastmail, SES, Mailgun, Postmark, SendGrid, Resend, or any SMTP server. |
| **Real personalization** | `{{ first_name }}` from any column, case-insensitive, with defaults and conditionals. |
| **Write how you like** | A formatted editor, plain formal text, Markdown, or your own HTML. |
| **Optional AI, fully controlled** | The model fills one labelled gap. It never sees the rest of your email, and you review every line before it sends. |
| **Never sends twice** | Every attempt is journalled. Crash, close the lid, lose Wi-Fi — resume and nobody is mailed again. |
| **Lands in inboxes** | Plain-text alternative, `List-Unsubscribe`, preheader, SPF/DKIM/DMARC check, spam-content linter. |
| **Tells you before you send** | Pre-flight renders *every* contact and reports what will break. |
| **Extensible** | Plugins add transports, AI providers, contact sources and UI panels. |

---

## The 60-second version

```bash
pip install sahajmails
sahajmails                 # opens http://localhost:8000
```

1. **Settings** → your address and an app password → **Test connection**
2. **New campaign** → drop your CSV → write your email → watch the live preview
3. **Review** → pre-flight + send yourself a test
4. **Send**

Prefer a terminal? Everything is a subcommand:

```bash
sahajmails init                                   # starter files here
sahajmails check contacts.csv -t email.md -s "Hi {{ first_name }}"
sahajmails send  contacts.csv -t email.md -s "Hi {{ first_name }}" --dry-run
```

Or import it:

```python
from sahajmails import send

send(contacts="contacts.csv", template="email.md",
     subject="Hello {{ first_name }}")
```

---

## Writing the email

Your spreadsheet columns become placeholders. Casing and spacing don't matter —
`{{ firstName }}`, `{{ first_name }}` and `{{ FIRST_NAME }}` are the same column.

```
Hi {{ first_name | default("there") }},

{% if company %}I saw the news about {{ company }} — congratulations.{% endif %}

The rest of this email is identical for everyone.

Best,
Sahaj
```

`default(...)` matters: without it, a blank cell stops the send rather than
quietly mailing "Hi ,". That is deliberate.

### AI personalization

Wrap the one sentence you want personalized. **Everything outside the block is
byte-identical for every recipient** — that is enforced by the design, not by
asking the model nicely.

```
Hi {{ first_name }},

{% ai "opener" max_words=25 fallback="Hope your week is going well." %}
One warm sentence about {{ company }}. No greeting, no exclamation marks.
{% endai %}

I'm writing because …
```

- The model receives **only** that instruction and that contact's variables.
- Output is capped, stripped of markup, and checked against your banned phrases.
- Anything that fails becomes your `fallback`. A flaky API can never block a send.
- **You review every line before anything is sent**, and your edits are used verbatim.

Works with OpenAI or any OpenAI-compatible endpoint — including
**Ollama and LM Studio running locally**, so the "nothing leaves your machine"
promise stays true.

---

## Documentation

| | |
|---|---|
| [Getting started](docs/getting-started.md) | Install, connect, first send |
| [AI personalization](docs/ai-personalization.md) | Slots, review, cost, local models |
| [Deliverability](docs/deliverability.md) | SPF, DKIM, DMARC, unsubscribe, bounces |
| [Providers](docs/providers.md) | Every preset, limits, app passwords |
| [CLI reference](docs/cli-reference.md) | Every command and flag |
| [Python API](docs/python-api.md) | Using it as a library |
| [Writing plugins](docs/writing-plugins.md) | A worked example |
| [Security](docs/security.md) | What runs where, and how it is protected |
| [Troubleshooting](docs/troubleshooting.md) | When something goes wrong |
| [Migrating from 1.x](docs/migrating-from-1.x.md) | What changed and why |

---

## Security in one paragraph

The app binds `127.0.0.1` only. It is protected by a session token, a `Host`
allowlist (which is what stops DNS-rebinding attacks that same-origin policy does
not), CSRF headers, and a sandboxed template engine that escapes contact data so
a spreadsheet can never inject markup into your mail. Passwords are kept in
memory unless you explicitly ask to store them. Full detail in
[docs/security.md](docs/security.md); report anything you find via
[SECURITY.md](SECURITY.md).

---

## Contributing

```bash
git clone https://github.com/sahajrajmalla/sahajmails.git
cd sahajmails
make install     # needs pip >= 25.1
make check       # ruff, mypy --strict, pytest
```

See [CONTRIBUTING.md](CONTRIBUTING.md). The whole test suite runs offline —
there is a built-in SMTP stub and a fake AI backend, so you never need real
credentials to develop.

---

## License

[MIT](LICENSE) — free for personal and commercial use.

<div align="center">
<sub>Built by <a href="https://github.com/sahajrajmalla">Sahaj Raj Malla</a></sub>
</div>
