# CLI reference

`sahajmails` with no arguments opens the web UI. Everything the UI does is also
a subcommand.

```
sahajmails                 open the web interface
sahajmails run             the same, with options
sahajmails init            starter config, template and contact list
sahajmails check           pre-flight without sending
sahajmails preview         render emails to the terminal
sahajmails send            send a campaign
sahajmails runs list|show  past sends
sahajmails suppress …      manage the do-not-contact list
sahajmails providers       list SMTP presets
sahajmails where           where files are kept
```

## run

```bash
sahajmails run [--host 127.0.0.1] [--port 8000] [--no-browser] [--allow-remote]
```

Binds loopback only. `--allow-remote` is required to bind anything else and
prints a warning, because this process can send mail as you.

## check

```bash
sahajmails check contacts.csv -t email.md -s "Hi {{ first_name }}"
```

Renders every contact and reports problems. Exits non-zero if anything blocks.

## preview

```bash
sahajmails preview contacts.csv -t email.md -s "Hi {{ first_name }}" -n 3 [--html]
```

## send

```bash
sahajmails send contacts.csv -t email.md -s "Hi {{ first_name }}" [options]
```

| Option | |
|---|---|
| `--dry-run` | Write `.eml` files instead of sending |
| `--outdir DIR` | Where `--dry-run` writes (default `outbox`) |
| `--limit N` | Only the first N contacts |
| `--resume RUN_ID` | Continue a stopped run. Never re-sends. |
| `--from`, `--from-name`, `--reply-to` | Override the sender |
| `--provider KEY` | Override the provider preset |
| `-a, --attach FILE` | Attach a file (repeatable) |
| `--rate N` | Messages per minute |
| `--concurrency N` | Parallel connections |
| `--missing error\|blank\|keep` | What to do about unresolved placeholders |
| `-y, --yes` | Skip the confirmation |

Ctrl-C finishes the message in flight and stops cleanly, so the ledger stays
accurate and `--resume` works.

## Credentials

Never pass a password on the command line — it lands in your shell history.

```bash
export SAHAJMAILS_SMTP_PASSWORD='your app password'
```

Every setting has a `SAHAJMAILS_*` variable: `SENDER_EMAIL`, `SENDER_NAME`,
`PROVIDER`, `SMTP_HOST`, `SMTP_PORT`, `SMTP_USERNAME`, `RATE_PER_MINUTE`,
`AI_PROVIDER`, `AI_MODEL`, `AI_API_KEY`, `AI_BASE_URL`, and the rest.

Resolution order: command-line flag → environment → `./sahajmails.toml` →
`~/.sahajmails/config.toml` → saved settings → provider defaults.
