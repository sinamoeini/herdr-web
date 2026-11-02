# Contributing

Keep changes focused and preserve backend ownership of shells and terminals.
Read [the technical guide](docs/GUIDE.md), [the porting notes](PORT.md), and
[known limitations](docs/ACCEPTANCE.md) before changing integration behavior.

## Issues and pull requests

Use the issue chooser to file a bug, request a feature, or ask a usage question.
Bug reports should include versions, platform/browser details, reproduction
steps, expected/actual behavior, and sanitized evidence. Follow `SECURITY.md`
for vulnerabilities rather than opening a public issue.

Complete the pull request template, describe checks actually run, and call out
untested behavior. Add user-facing changes to `CHANGELOG.md`. Repository ownership
is recorded in `.github/CODEOWNERS`; it does not replace required CI/merge rules.

## Local checks

Python 3.11 or newer is required for the maintenance tests:

```sh
python3 -m unittest discover -s test -p 'test_*.py' -v
python3 test/plugin.py --manifest-only
cargo test --locked --workspace
./build.sh
```

JavaScriptCore logic tests run on macOS; Linux skips those tests. Every pull
request and push to `main` also runs all 12 current Linux acceptance suites using
checksum-pinned original Herdr 0.9.5, Chromium and Firefox, real terminal/backend
fixtures, isolated loopback SSH, and headed clipboard/audio checks under Xvfb
with a private PulseAudio null sink. Failed suites do not prevent later suites
from running; the overall acceptance job fails if any suite fails. Logs,
screenshots, and per-suite outcomes are retained as workflow artifacts for seven
days, including on failure.

To run the same suite on an isolated Linux development host with the documented
prerequisites already installed:

```sh
HERDR_BIN="$(command -v herdr)" python3 tools/run_acceptance.py
```

The runner requires `target/release/herdr-web`, including for plugin lifecycle
tests. CI installs prerequisites only on disposable GitHub-hosted runners; the
local runner never installs packages or changes system services. These automated
checks do not certify physical speaker output, physical drag/drop, external-host
SSH topology, or every manual case in the acceptance ledger.

## Assets and licenses

Change upstream-panel adaptations in `tools/vendor.py`, not just generated files.
Preserve original bytes, licenses, and provenance checksums for copied assets.
Dependency changes require reviewing the locked Linux license inventory.
Dependabot proposes weekly Cargo and GitHub Actions updates; it does not merge
them automatically. Cargo changes may need the generated license inventory
refreshed with `tools/vendor_rust_notices.py` on Linux before checks pass.

## Repository-local skills

The five Herdr-specific agent skills live in `.agents/skills/`, including their
references and UI metadata. They cover asset packaging, desktop DOM adapters,
browser acceptance evidence, persistent terminal bridges, and safe deployment.
They are development guidance, not application code or release assets. They do
not grant permission to change live services, cloud resources, or credentials.

## Privacy and reports

Keep credentials, cloud identifiers, personal deployment inventories, and raw
operational records outside the repository. Generated artifacts and test logs
are ignored. Before sharing a bug report, sanitize terminal output, file paths,
screenshots, environment variables, and logs. Never post credentials or an active
user terminal transcript in an issue.
