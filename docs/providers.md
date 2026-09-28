# Providers

Pick a preset and the host, port, encryption, rate limit and daily cap are
filled in for you. `sahajmails providers` lists them all.

| Key | Provider | Host | Port | Daily |
|---|---|---|---|---|
| `gmail` | Gmail | smtp.gmail.com | 587 | 500 |
| `google_workspace` | Google Workspace | smtp.gmail.com | 587 | 2,000 |
| `outlook` | Outlook / Microsoft 365 | smtp-mail.outlook.com | 587 | ~300 |
| `yahoo` | Yahoo Mail | smtp.mail.yahoo.com | 465 (SSL) | 500 |
| `zoho` | Zoho Mail | smtp.zoho.com | 587 | 500 |
| `icloud` | iCloud Mail | smtp.mail.me.com | 587 | 200 |
| `fastmail` | Fastmail | smtp.fastmail.com | 465 (SSL) | 2,000 |
| `ses` | Amazon SES | email-smtp.<region>.amazonaws.com | 587 | — |
| `mailgun` | Mailgun | smtp.mailgun.org | 587 | — |
| `postmark` | Postmark | smtp.postmarkapp.com | 587 | — |
| `sendgrid` | SendGrid | smtp.sendgrid.net | 587 | — |
| `resend` | Resend | smtp.resend.com | 587 | — |
| `brevo` | Brevo | smtp-relay.brevo.com | 587 | 300 |
| `custom` | Anything else | you supply | you supply | — |

The provider is guessed from your address, so `you@gmail.com` needs no choice at
all.

## App passwords

Your normal password will be rejected by nearly every provider. Create an app
password instead — see [getting started](getting-started.md#connect-your-mailbox).

Two providers have unusual credentials:

- **SendGrid** — the username is literally `apikey`; the password is your API key.
- **Resend** — the username is `resend`; the password is your API key.
- **Amazon SES** — use SES *SMTP credentials*, not your AWS access keys, and set
  the host to your region.

## Custom servers

Choose **Custom SMTP server** and set host, port and encryption:

- **STARTTLS** — plaintext then upgrade. Usually port 587. The normal choice.
- **SSL/TLS** — encrypted from the first byte. Usually port 465.
- **None** — no encryption. Only sane for a relay on localhost.

## Parallel connections

Consumer providers dislike more than one connection and the presets reflect
that. Transactional providers tolerate several — raising concurrency there is
the single biggest speed win on a large list.
