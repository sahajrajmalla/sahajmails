# Troubleshooting

## "Username and Password not accepted"

Your normal password will not work. Create an app password — see
[getting started](getting-started.md#connect-your-mailbox). If you already did,
check that 2-Step Verification is on; most providers will not issue an app
password without it.

Microsoft 365 tenants often disable SMTP AUTH entirely. Your admin has to
re-enable it.

## "This page needs the session token"

Use the link the terminal printed when the server started. The bare address will
not work — the token is what stops other software on your machine from driving
your mail sender.

## "Template uses {{ x }} but your contact file has no such column"

Deliberate. 1.x quietly sent "Hi ," to everyone. Either fix the spelling, add
the column, or give it a default:

```
Hi {{ first_name | default("there") }},
```

Casing and spacing do not matter, so this is a genuinely missing column rather
than `firstName` versus `first_name`.

## "The legacy .xls format is not supported"

`xlrd` dropped `.xls` in 2020, so supporting it means an abandoned dependency.
Open it and save as `.xlsx` or CSV.

## The send stopped partway

Nothing is lost. Every attempt is journalled:

```bash
sahajmails runs list
sahajmails send contacts.csv -t email.md -s "…" --resume run-2026…
```

Anything already delivered is skipped. Nobody is mailed twice.

## Everything is going to spam

Work through [deliverability](deliverability.md). In rough order of impact:
set up SPF and DMARC on your domain, add an unsubscribe address, make sure
there is real text and not just images, and slow down.

## The send is very slow

Check the rate in Settings. Consumer providers are limited to roughly 20–30 a
minute and going faster gets you throttled. Transactional providers tolerate far
more, and raising *parallel connections* there is the biggest single win.

## Port 8000 is busy

It finds the next free port automatically and tells you. Or pass `--port`.

## Where is my data?

```bash
sahajmails where
```

`~/.sahajmails/` — the database, uploads, outbox and plugins. Delete the folder
to reset completely.

## Still stuck

Open an issue with what you ran, what happened, and the version from
`sahajmails --version`. Never paste an app password into an issue.
