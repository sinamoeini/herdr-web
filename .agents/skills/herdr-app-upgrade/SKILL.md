---
name: herdr-app-upgrade
description: "Upgrade this repository's vendored herdr-app panels and assets to a verified upstream release, preserve browser adaptations and provenance, and validate locally or through an authorized GitHub PR. Not for installing the native desktop app, upgrading the Herdr backend, or deploying a live service."
---

# Herdr App Upgrade

Upgrade the upstream desktop sources used by **Herdr Web**, not the user's native
desktop installation. Produce a reproducible, reviewable asset change and evidence
for the exact candidate tested. Read repository instructions before modifying files.

## Scope and source of truth

Three independent versions are involved:

- **Herdr app:** selected desktop JavaScript, layouts, fonts, icons, sounds, and
  palettes imported into `public/desktop/`.
- **Original Herdr backend:** installed separately; its reference commit is
  `HERDR` in `tools/vendor.py`. Its protocol and runtime version are separate from
  the app version. Do not replace it with `herdr-core` because an app release uses
  that newer backend.
- **Herdr Web:** the Rust package version in `server/Cargo.toml`. An upstream asset
  upgrade does not itself require a Web release or package-version bump.

Do not infer the requested target from a remembered version. A previously tested
release is not necessarily today's latest. Distinguish released fixes imported
through assets from native Rust/GPUI engine fixes this port does not use.

| Repository surface | Purpose |
| --- | --- |
| `tools/vendor.py` | Exact `DESKTOP`/`HERDR` pins and repeatable transformations |
| `tools/asset-plan.json` | Reviewed source-to-destination asset allowlist |
| `tools/asset-plan.example.json` | Example plan that must retain a consistent desktop pin |
| `public/desktop/UPSTREAM.json` | Generated source commits, hashes, and transformation descriptions |
| `public/desktop/runtime/{gpui,base,herdr}.js` | Authored browser adapters; not upstream-generated panels |
| `test/test_tools.py`, `test/test_js_logic.py`, `test/browser.py` | Provenance, macOS logic, and browser foundation checks |
| `.github/workflows/{ci,acceptance}.yml`, `tools/run_acceptance.py` | Full build, packaging, and native acceptance configuration |

## Workflow

### 1. Establish the candidate

Check the current worktree, branch, and pins. Preserve unrelated changes. Identify
whether the request is local-only, includes a PR, or includes remote verification.
The skill itself grants no commit, push, PR, release, merge, or deployment authority;
use the authorization supplied by the user and active execution instructions.
Do not ask again for actions already authorized.

Read [upstream selection and reproducible import](references/upstream-import.md)
for release-feed verification, detached external reference checkouts, diff review,
pin updates, importer protections, and the documentation checklist.

### 2. Review before regenerating

Inventory changed imported panels, imports, native API calls, and transitive asset
references. Decide which browser compatibility changes are actually required.
Update importer transformations before generating panels. Update shared runtime
adapters only when their existing semantics cannot implement the new upstream API.

Use the sibling [desktop DOM adapter skill](../desktop-dom-adapters/SKILL.md) when
event routing, focus, positioning, or lifecycle semantics change. Use the sibling
[Cargo asset packaging skill](../cargo-web-asset-packaging/SKILL.md) when new asset
types, notices, or embedding behavior require investigation. Do not rewrite panels
or change build systems merely to make an import pass.

### 3. Regenerate and validate

Import the reviewed plan at the exact target commit. Verify that repeat import is
idempotent, hashes match actual bytes, required new assets are included, and stale
importer-owned files are not accidentally retained in the embedded asset tree.

Read [validation and GitHub evidence](references/validation-github.md) for local
commands, browser boundaries, full Linux acceptance, PR creation, run selection,
and artifact verification. Run checks supported by the available environment;
record missing prerequisites rather than treating unrun tests as passed.

If remote verification is authorized, prefer the repository's existing PR CI over
an ad hoc workflow. Confirm the run tests the upgrade, not unchanged `main` or an
older PR revision. An opened PR or queued workflow is not a verified build.

### 4. Report and stop at the requested boundary

Summarize old/new app versions and commits, affected adapters, backend compatibility,
checks actually passed, skipped/unavailable checks, and PR/run identifiers when
applicable. Link verified evidence in the PR or acceptance ledger without copying
private logs into tracked files. Keep historical evidence clearly dated.

Do not merge the PR, publish a release, install the native application, restart the
backend, or deploy the built Web executable unless separately requested. A green
build establishes the checked scope, not physical-hardware or complete desktop parity.
