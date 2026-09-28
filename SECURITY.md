# Security policy

## Supported versions

| Version | Supported |
|---|---|
| 2.x | ✅ |
| 1.x | ❌ — please upgrade |

## Reporting a vulnerability

Please report privately, not in a public issue.

Use [GitHub's private vulnerability reporting](https://github.com/sahajrajmalla/sahajmails/security/advisories/new),
or email **mallasahajraj@gmail.com** with `SECURITY` in the subject.

Please include what you found, how to reproduce it, and what an attacker could
do with it. You will get an acknowledgement within 72 hours and an assessment
within a week. Fixes ship as a patch release, and you will be credited unless
you would rather not be.

**Never include a real app password, API key or contact list in a report.**

## Scope

In scope: the local HTTP server and its authentication, template rendering and
escaping of contact data, credential storage and handling, uploads and path
handling, SMTP and IMAP transports, the AI layer, and the plugin loader.

Out of scope: anything requiring an attacker who already has your user account
on your machine; third-party plugins; your mail provider's own systems.

## Design notes

The threat model, and what is defended against, is documented in
[docs/security.md](docs/security.md).
