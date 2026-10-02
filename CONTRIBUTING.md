# Contributing

Thanks for helping. This is a small, focused tool and the bar is quality, not
volume.

## Setup

```bash
git clone https://github.com/sahajrajmalla/sahajmails.git
cd sahajmails
python -m venv .venv && source .venv/bin/activate
make install          # needs pip >= 25.1 for dependency groups
```

## The loop

```bash
make check            # ruff, mypy --strict, pytest with coverage — what CI runs
make format           # fix formatting and lint
make run              # start the app locally
```

Everything runs offline. There is a real SMTP server in `tests/smtpstub.py` and
a deterministic `FakeBackend` for AI, so you never need credentials — or a
network — to develop or test.

## What we look for

**Tests that would have caught the bug.** Every fix should come with one. Look
at `tests/test_security.py` for the style: each test says what breaks in the
real world if the behaviour regresses.

**Errors that tell people what to do.** Every exception takes a `hint`. An error
someone cannot act on is a bug in its own right.

**Comments that explain *why*.** The code says what it does. Comments are for
the reasoning that is not obvious — a workaround, a trade-off, a standard being
followed.

**Scope discipline.** This tool does bulk email well. It is not becoming a CRM,
an analytics dashboard, or a scheduler. The [README](README.md) and the plugin
system exist so it does not have to.

## Style

Enforced by `ruff` and `mypy --strict`; run `make check` before pushing. Public
functions get docstrings and type annotations. Line length is 100.

## Pull requests

Branch, commit, open a PR describing what changed and why. Link the issue if
there is one. CI runs on Python 3.11–3.14 across Linux, macOS and Windows.

Adding a dependency needs a good argument — the small install is a feature.

## Reporting bugs

Include what you ran, what happened, what you expected, and
`sahajmails --version`. Never paste an app password or a real contact list.

Security issues go to [SECURITY.md](SECURITY.md) instead.
