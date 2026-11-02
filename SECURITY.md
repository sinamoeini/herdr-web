# Security policy

## Reporting a vulnerability

Do not disclose suspected vulnerabilities in public issues or pull requests.
Use this repository's **Security → Advisories → Report a vulnerability** to
submit a private report:

https://github.com/sinamoeini/herdr-web/security/advisories/new

If private reporting is unavailable, ask the maintainer to enable it without
including sensitive details in a public issue. Do not attach secrets, real
customer data, private terminal transcripts, or credentials to a report.

Include the affected release/commit, original Herdr version, operating system,
browser version where relevant, impact, and a minimal reproduction using an
isolated session. Describe the expected security boundary and any workaround.
Coordinate disclosure privately while the report is investigated. There is no
guaranteed response time, bounty, or backport commitment.

## Supported scope

The project is preparing its initial release. Report against current `main` or,
once available, the newest published release. Older releases do not have a
separate maintenance commitment. Compatibility is currently scoped to Linux
x86_64 and original Herdr 0.9.5; see `docs/ACCEPTANCE.md` for verification limits.

The web executable has the local user's file and terminal privileges. It listens
on loopback and is not designed for unauthenticated public/shared hosting.
Trusted custom panels run with page privileges, not in a sandbox. Never expose
the listener through a public proxy without a separate authentication design.

Potential issues include Host/Origin validation bypasses, unintended terminal
input or destructive actions, path containment failures, and unbounded resource
use. Use disposable sessions for reproduction, not a user's active shell.
