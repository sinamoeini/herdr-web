# Validation and GitHub evidence

## Contents

- Local validation and browser scope
- Full Linux build and acceptance
- Authorized PR and run selection
- Inspect results and artifacts
- Failure handling and completion report

Commands run from the repository root. Check the current workflows and runner
instead of freezing historical test counts, action SHAs, tool versions, job IDs,
or backend checksums into an upgrade procedure.

## Local validation and browser scope

Start with focused provenance and logic coverage, then the repository unit suite:

```sh
python3 -m unittest discover -s test -p 'test_tools.py' -v
python3 -m unittest discover -s test -p 'test_js_logic.py' -v
python3 -m unittest discover -s test -p 'test_*.py' -v
python3 test/plugin.py --manifest-only
git diff --check
```

Use discovery from `test/`. Commands such as `python3 -m unittest test.test_tools`
can import Python's standard-library `test` package instead of this repository's
tests. That is a test invocation error, not evidence of an application regression.

`test/test_tools.py` checks pins, plan coverage, unique destinations, adapted hashes,
unaltered font bytes, and locked Rust license notices. It does not independently
certify every recorded original hash against upstream; the pinned Git-blob import
and source review establish that part of the chain.

`test/test_js_logic.py` uses system JavaScriptCore on macOS. Linux deliberately
skips that platform-specific coverage. Report skips explicitly instead of calling
the number of discovered tests the number actually executed. The acceptance-runner
unit tests deliberately exercise failed subprocesses, so failure text in their
captured output is not itself a failed unit suite; inspect the suite's exit status.

For a built executable and Python Playwright with Chromium/Firefox installed:

```sh
python3 test/browser.py --foundation --binary target/release/herdr-web
```

Read the current argument handling: without `--foundation`, this entrypoint
delegates to native integration rather than running the foundation-only path.
The foundation path includes adapter controls and the virtual picker fixture.
Actual keyboard/wheel checks on fixtures establish DOM translation behavior, not
real remote machines, terminals, or native backend compatibility.

The current full native runner does **not** automatically invoke every standalone
test file: `browser.py` foundation checks are separate from its enumerated suites.
If changed behavior needs coverage not included in CI, run that specific test on
a suitable host or deliberately integrate the regression into an existing CI
suite. Do not claim a test ran merely because its helper lives in the repository.

When native behavior changes, use the sibling
[native browser evidence skill](../../native-browser-evidence/SKILL.md). Prefer
actual visible controls in both browsers, record page errors, and test ownership
and cancellation rather than asserting that text is present. Do not install
system dependencies, change live services, or send test input into an active user
terminal just because a local test prerequisite is missing.

## Full Linux build and acceptance

Read `.github/workflows/ci.yml`, `.github/workflows/acceptance.yml`,
`tools/run_acceptance.py`, and `docs/ACCEPTANCE.md`. The existing PR workflow runs:

- Linux and macOS unit/provenance checks, plugin manifest validation, shell syntax.
- Pinned-toolchain `cargo test --locked --workspace`.
- `./build.sh`, an optimized standalone Linux release executable.
- `tools/package_release.py`, archive SHA256 verification, extraction, and an
  extracted-executable `--help` smoke test.
- Linux native acceptance against the workflow's checksum-pinned original Herdr
  binary, both browsers, isolated SSH, and headed clipboard/audio permissions.

The runner currently enumerates `integration`, `ui`, `graphics_browser`,
`view_edges`, `services`, `security`, `resources`, `git_failures`,
`watch_resources`, `plugin`, `ssh`, and `permissions`. Derive the expected set from
the current `SUITES` declaration if it changes. A successful build alone does not
establish that these suites ran.

For an already prepared, isolated Linux host:

```sh
cargo test --locked --workspace
./build.sh
HERDR_BIN="$(command -v herdr)" python3 tools/run_acceptance.py
```

Use the manifest's `target/release/herdr-web` binary. The full runner rejects a
different `HERDR_WEB_BINARY`, since plugin lifecycle must test the manifest's
actual executable too. Read the workflow for prerequisite versions and setup;
the local runner is not a package installer. GitHub-hosted disposable runners are
an alternative when the local host lacks Rust, Linux, the backend, or permissions.

## Authorized PR and run selection

A request for a local upgrade does not by itself authorize publishing it. Follow
the user's authorization and active instructions for branch/commit/push/PR actions.
If a PR is requested, use it to run the existing full workflow rather than
requiring a second, redundant manual dispatch. Do not merge or publish a release.

Before publishing, inspect remotes/authentication without printing credentials,
fetch the intended base, and examine its relationship to the local branch:

```sh
git remote -v
gh auth status
git fetch origin main
git rev-list --left-right --count HEAD...origin/main
gh pr list --state open --json number,title,headRefName,url
```

Use the actual base if it is not `main`. Reuse the existing upgrade branch/PR
when continuing the same work; do not accidentally add the upgrade to an unrelated
PR. Avoid force pushes and broad cleanup. Stage only reviewed task files.

Once branch creation, commit, push, and PR publication are authorized, a typical
sequence for a new upgrade is:

```sh
git switch -c upgrade/herdr-app-VERSION
git add -- REVIEWED_PATHS
git commit -m "Upgrade vendored Herdr app panels to vVERSION"
git push -u origin upgrade/herdr-app-VERSION
gh pr create --base main --head upgrade/herdr-app-VERSION \
  --title "Upgrade Herdr app panels and assets to vVERSION" \
  --body-file /absolute/path/to/reviewed-pr-body.md
```

Replace `VERSION`, `REVIEWED_PATHS`, and the body-file path; these are not literal
commands to execute unchanged. Follow `.github/pull_request_template.md`: describe
the pin, compatibility, adapter changes, completed local checks, pending remote
checks, and untested scope. Do not include credentials or raw terminal transcripts.

The current `ci.yml` triggers for pull requests, pushes to `main`, and manual
dispatch. A push to a feature branch **without a PR** does not trigger its push
filter. A manual dispatch against unchanged `main` cannot validate uncommitted
local assets.

After creating/updating the PR, find the matching run:

```sh
gh pr view PR_NUMBER --json headRefOid,headRefName,url
gh run list --workflow ci.yml --branch UPGRADE_BRANCH --event pull_request \
  --limit 5 --json databaseId,headSha,status,conclusion,url
gh run watch RUN_ID --exit-status --interval 30
gh pr checks PR_NUMBER
```

Substitute the real identifiers. Require the run's `headSha` to match the current
PR head. GitHub may initially report no checks while the workflow is being
registered; look up the queued run before concluding it failed. Avoid redundant
dispatches and tight polling. A new push can cancel an older run via concurrency;
only the final current revision's results count.

## Inspect results and artifacts

After completion, inspect all required jobs and meaningful build log results:

```sh
gh run view RUN_ID --json status,conclusion,headSha,event,jobs,url
gh run view RUN_ID --log
gh api repos/OWNER/REPO/actions/runs/RUN_ID/artifacts
gh run download RUN_ID --dir /absolute/external/audit-directory
```

Derive `OWNER/REPO` from the selected remote. Keep downloads and raw logs outside
the tracked tree or in ignored test-results paths. Do not execute downloaded
artifacts merely to inspect them. The current acceptance artifact is named
`linux-acceptance-RUN_ID-ATTEMPT`; read the actual listing rather than assuming
the first attempt or a still-valid retention window.

Verify from the logs and downloaded `acceptance/summary.json`:

1. Every required CI job completed successfully, including the release build,
   checksum validation, extracted-binary smoke, and native acceptance step.
2. Every suite expected by `tools/run_acceptance.py` is present exactly once,
   has `status: passed`, `exit_code: 0`, and has its corresponding suite log.
   Read failures or contradictory results even if a job appears green.
3. The summary metadata names the expected original backend and its checksum,
   matching the pin in the workflow. Do not hardcode yesterday's checksum into
   a new verification script.
4. The summary's `binary_sha256` identifies the tested executable. Keep its
   relationship to the source explicit; do not claim an installed or downloaded
   release binary was tested if CI built a different candidate.
5. `metadata.source_sha` matches the workflow's actual checkout revision. For a
   normal `pull_request` run, `GITHUB_SHA` is generally the synthetic **merge
   commit**, while the run's `headSha` is the PR **head commit**. Inspect the
   checkout logs and `gh api repos/OWNER/REPO/pulls/PR_NUMBER`'s
   `merge_commit_sha` instead of incorrectly requiring these two SHAs to equal.
   If the base/PR moved after the run, use the run's recorded checkout/merge
   revision and parent commits rather than the PR's newly recomputed merge SHA.

Current CI packages and smoke-tests the release archive but only uploads the
acceptance evidence artifact. Do not promise a downloadable binary artifact unless
the actual workflow produced one. Opening a PR does not publish a GitHub release.

Update the PR's validation section or add a concise sanitized evidence comment:
run link, tested head/checkout SHA, actual test counts/skips, build/package result,
per-suite acceptance result, and remaining scope. A PR comment/body update can
record evidence without triggering another code revision and full CI run.

## Failure handling and completion report

On failure, inspect `gh run view RUN_ID --log-failed` and the uploaded per-suite
logs/screenshots. The acceptance runner continues after failures, so summarize
each failed/passed suite and the overall nonzero result. Distinguish application
regressions, fixture readiness, missing prerequisites, expected negative-test
output, and genuinely transient runner/network faults.

Fix an upgrade-related defect at its source, add a focused regression, regenerate
assets if the importer changes, and push only an authorized reviewed correction.
Do not weaken assertions, blanket-ignore browser errors, change backend versions,
or alter CI to conceal a red check. Investigate unrelated failures and report
their scope rather than silently expanding the upgrade.

A targeted rerun may be reasonable for an identified transient infrastructure
failure when authorized. Do not repeatedly rerun unchanged failures in search of
a green result. Stop and report when progress requires new permission, an external
fix, or a compatibility decision outside the app-asset upgrade.

Completion should distinguish **locally validated**, **GitHub verified**, and
**deployed**. Report the actual target release/commit and backend compatibility,
provide the PR/run identifiers, and state that merge/release/deployment has not
occurred unless those separate actions were requested and verified.
