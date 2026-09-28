# Migrating from 1.x

## If you just used the app

```bash
pip install --upgrade sahajmails
sahajmails
```

It opens the same way. The interface is new but the flow is the same: contacts,
compose, preview, test, send. Your Gmail app password works unchanged.

Three differences worth knowing:

**Missing placeholders now stop the send.** 1.x rendered them blank, so a typo
shipped "Hi ," to everyone. Add a default where a column may be empty:

```
Hi {{ first_name | default("there") }},
```

**You choose a format.** 1.x always ran your text through Markdown. Now pick
Formatted, Plain, Markdown or HTML — Plain is what you want for a formal letter.

**`.xls` is refused.** Save as `.xlsx` or CSV.

## If you imported the library

`sahajmails.app` no longer exists.

| 1.x | 2.0 |
|---|---|
| `from sahajmails.app import build_personalized_body` | `EmailTemplate(...).render(contact)` |
| `from sahajmails.app import render_email_body` | same — the HTML part of `render()` |
| `from sahajmails.app import connect_smtp` | `sahajmails.transport.SmtpTransport` |
| `from sahajmails.app import create_mime_message` | `sahajmails.message.build_message` |

```python
# 1.x
from sahajmails.app import build_personalized_body, render_email_body
body = build_personalized_body(template, row, columns)
html = render_email_body(body)

# 2.0
from sahajmails import EmailTemplate, load_contacts
template = EmailTemplate(subject="Hi {{ first_name }}", body=body_source)
rendered = template.render(load_contacts("contacts.csv").contacts[0])
rendered.html, rendered.text
```

See the [Python API](python-api.md).

## Requirements

Python 3.11+. 1.x claimed 3.8 but its dependencies never supported it.

## What you get

Resume-after-crash, a real plain-text part, `List-Unsubscribe`, pre-flight
checks, a suppression list, run history, any SMTP provider, optional AI
personalization, plugins — and an install that is 373 MB smaller because
Streamlit and pandas are gone.

Full detail in the [changelog](../CHANGELOG.md).
