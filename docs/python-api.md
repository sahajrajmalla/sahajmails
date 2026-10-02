# Python API

The library is fully typed and ships `py.typed`.

## One call

```python
from sahajmails import send

report = send(
    contacts="contacts.csv",
    template="email.md",
    subject="Hello {{ first_name }}",
)
print(report.summary())  # "48 sent, 2 failed"
```

## Full control

```python
from sahajmails import BodyFormat, EmailTemplate, load_contacts
from sahajmails.config import load_settings
from sahajmails.sender import BulkSender, SendOptions
from sahajmails.storage import Database, Repository

settings = load_settings({"sender_email": "you@example.com"})
contacts = load_contacts("contacts.csv")

template = EmailTemplate(
    subject="Hello {{ first_name }}",
    body="Hi {{ first_name | default('there') }},\n\nWelcome aboard.",
    body_format=BodyFormat.MARKDOWN,
    preheader="A short note about your account",
)

repo = Repository(Database())  # enables resume and history
sender = BulkSender(
    template=template,
    contacts=contacts,
    settings=settings,
    repo=repo,
    options=SendOptions(rate_per_minute=20, concurrency=1),
)

report = sender.send(on_progress=lambda p: print(f"{p.done}/{p.total}"))
for failure in report.failed:
    print(failure.email, failure.error)
```

Resume a stopped run by passing its id — anything already delivered is skipped:

```python
sender.send(run_id=report.run_id)
```

## Pieces

| Module | |
|---|---|
| `sahajmails.contacts` | `load_contacts`, `ContactList`, validation and dedupe |
| `sahajmails.template` | `EmailTemplate`, `BodyFormat`, `MissingPolicy`, AI slots |
| `sahajmails.message` | `build_message`, `prepare_attachments` |
| `sahajmails.transport` | `SmtpTransport`, `FileTransport`, the transport registry |
| `sahajmails.sender` | `BulkSender`, `SendOptions`, `SendReport`, `Progress` |
| `sahajmails.preflight` | `run_preflight` |
| `sahajmails.linter` | `lint_email` |
| `sahajmails.dns_check` | `check_domain` |
| `sahajmails.storage` | `Database`, `Repository` |
| `sahajmails.ai` | `SlotEngine`, backends, `FakeBackend` |
| `sahajmails.plugins` | `PluginRegistry`, `load_plugins` |

## Errors

Everything derives from `SahajMailsError` and carries a `hint` telling the user
what to do:

```python
from sahajmails import SahajMailsError

try:
    send(...)
except SahajMailsError as exc:
    print(exc)  # what went wrong
    print(exc.hint)  # what to do about it
```

## Testing against it

No credentials needed. `sahajmails.ai.FakeBackend` is a deterministic AI
backend, and `tests/smtpstub.py` is a real SMTP server you can point at
`127.0.0.1` with full control over failures.
