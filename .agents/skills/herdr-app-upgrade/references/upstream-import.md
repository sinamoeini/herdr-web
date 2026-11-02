# Upstream selection and reproducible import

## Contents

- Establish existing pins and ownership
- Resolve a released target
- Review changes and browser compatibility
- Update pins and import safely
- Check provenance, documentation, and rollback

Commands below run from the repository root. Substitute the actual requested
release; do not reuse an example version as a claim about the latest release.

## Establish existing pins and ownership

Start with `git status --short`, the current branch, and applicable `AGENTS.md`
instructions. Read `tools/vendor.py`, both asset-plan files, the generated manifest,
`README.md`, and `PORT.md`. Determine the old app release from its pinned commit
rather than from the Rust package version or the installed backend.

Record the prior desktop/backend commits and preserve the current manifest before
importing. A manifest copy in an external temporary audit directory is useful for
comparing old/new destinations. Existing adapted files may contain user changes;
do not discard them or bypass the importer's overwrite guard.

The importer reads exact Git blobs, not the reference checkout's working-tree
files. Reference repositories must resolve **outside this repository** and their
`HEAD` must equal the requested frozen commit. Use an owned detached checkout or
an already suitable clean reference; do not reset somebody else's checkout.

## Resolve a released target

For a request for the latest release, query the official feed at execution time:

```sh
curl --fail --show-error --location --retry 2 \
  https://releases.herdr.dev/app/latest.json
git ls-remote --tags https://github.com/herdrdev/herdr-app.git
```

Read the feed's `version`, `core_min`, and available targets. A `core_min` field
describes the native app's backend dependency; it does not authorize changing this
Web port's original Herdr backend. Do not run the native app installer for an
asset upgrade.

Use the feed's released version and its exact upstream tag, not default-branch
`HEAD`, unreleased commits, lexicographic tag ordering, or an assumed GitHub
`releases/latest` entry. This project has distributed releases through its own
release feed even when GitHub's releases endpoint had no release object.

For a user-specified version, resolve that version's tag and changelog. If the feed,
tag, version metadata, or changelog disagree, investigate before selecting a pin;
do not silently substitute `master` or an older tag. If the requested pin already
matches the repo, report that rather than creating a cosmetic upgrade.

Example acquisition into a newly owned external directory:

```sh
TARGET_VERSION=0.1.11
REFERENCE=$(mktemp -d "${TMPDIR:-/tmp}/herdr-app-reference.XXXXXX")
git clone --filter=blob:none https://github.com/herdrdev/herdr-app.git "$REFERENCE"
git -C "$REFERENCE" checkout --detach "v$TARGET_VERSION"
TARGET_COMMIT=$(git -C "$REFERENCE" rev-parse 'HEAD^{commit}')
git -C "$REFERENCE" show -s --format='%H %cI %s' HEAD
```

`0.1.11` is a historical example, not a maintained latest-version setting. Resolve
annotated tags to their commit, and record the full SHA. Read the selected release's
`CHANGELOG.md` and package version. This workflow pins a commit; it does not imply
that a signed release/tag has been cryptographically verified.

## Review changes and browser compatibility

Set `OLD_COMMIT` from the existing desktop pin. Review both the complete change
summary and the selected import surface:

```sh
git -C "$REFERENCE" diff --stat "$OLD_COMMIT" "$TARGET_COMMIT"
git -C "$REFERENCE" diff "$OLD_COMMIT" "$TARGET_COMMIT" -- \
  defaults assets src/settings.rs LICENSE
```

Inspect changed panels individually when a broad diff is large. Read relevant
upstream `.d.ts` declarations or tests to understand new native API semantics;
do not use them as permission to vendor an entire app/backend repository.

For every changed imported panel, inspect:

- Bare and relative imports, named exports, and newly referenced modules.
- Images, SVGs, fonts, sound files, themes, and dynamically assembled asset paths.
- New fluent methods or API fields used from `gpui.js`, `base.js`, or `herdr.js`.
- Asynchronous callbacks, instance properties, folder watches, timers, and disposal.
- Selection, drag/drop, keyboard, context-menu, overlay, and scroll behavior.
- Privileged native actions that must remain unsupported or use bounded server APIs.

The v0.1.9-to-v0.1.11 upgrade illustrates why a source-only copy is insufficient:

- Tabs gained four `win-*.svg` controls. Even Linux-first adapters should not leave
  imported code referring to missing upstream assets without an explicit decision.
- The picker began positioning rows absolutely. The browser list needed its own
  relative containing block, implemented reproducibly in `tools/vendor.py`.
- Native wheel deltas represented content motion, opposite to DOM `deltaY`; the
  shared adapter needed the correct sign. Preserve fractional touchpad movement
  and regression-test both wheel directions instead of blindly inverting again.
- Shortcut labels began consulting `keys()`. Check the adapter's returned binding
  shape and browser-reserved-key filtering, not merely whether the module parses.
- Files gained context tokens and saved state. Preserve cancellation and prevent
  late reads from one space overwriting another space's panel.

These are review examples, not mandatory patches for every future release. Read
the current adapters before changing them.

Preserve established browser adaptations: instance-owned `webProps`, watch owners,
local server labels, bounded file/Git APIs, manual updates, disabled telemetry and
feedback, supported SSH forwarding, and explicit stage-all commit labeling. Verify
the transformations still match the new source. A `.replace()` can silently match
nothing even though the import succeeds; an `.index()` failure often means the
upstream structure changed, not that the guard should be removed.

## Update pins and import safely

Update together:

1. `DESKTOP` in `tools/vendor.py` to the full target commit.
2. `desktop_commit` in `tools/asset-plan.json` and `tools/asset-plan.example.json`.
3. Reviewed entries for new, renamed, or removed imported assets in the real plan.
4. Version-specific transformations in `tools/vendor.py` and any necessary authored
   browser adapters, with relevant regression coverage.

Do not update `HERDR`, `Cargo.lock`, backend protocol checks, Rust dependencies, or
the Web package version unless the task independently requires those changes.
Do not globally replace version strings: app, backend, and Web versions differ.

After updating `DESKTOP`, optionally run `tools/vendor.py inventory` when a combined
source/API inventory is useful. It validates `HEAD` against `DESKTOP` just like the
import command, so running it against the target checkout before updating the pin
will fail. Set `BACKEND_REFERENCE` to an external original Herdr checkout at the
unchanged `HERDR` commit, and `AUDIT_DIR` to an existing external audit directory:

```sh
python3 tools/vendor.py inventory --desktop "$REFERENCE" \
  --herdr "$BACKEND_REFERENCE" --output "$AUDIT_DIR/inventory.json"
```

Its output is an inventory, not evidence of completed compatibility review. It is
not required just to import desktop assets and does not justify advancing the
backend pin. If the inventory reveals new requirements, revise the plan and
transformations before importing.

Run the existing importer:

```sh
python3 tools/vendor.py import --desktop "$REFERENCE" --plan tools/asset-plan.json
```

The importer automatically includes upstream `LICENSE` and extracts the configured
palette constants from `src/settings.rs`. It rewrites supported bare runtime
imports and records original/adapted SHA256 hashes and named transformations.
Adapt the palette extractor if the real source format changes; do not retain
stale colors or guess replacement values to make regeneration pass.

`safe_destination()` excludes adapter-owned runtime paths and traversal. Keep
those checks, the nonempty exact-commit plan requirement, the upstream license
requirement, and the existing-file hash guard. If the guard fails, inspect the
local diff and reconcile its owner before retrying. Do not erase `UPSTREAM.json`,
forge prior hashes, or import elsewhere and copy around the protection.

The importer stages content before writing, but it is not a filesystem transaction.
After an interrupted write, inspect the tree and reconcile only the affected
importer-owned outputs; avoid broad reset/clean commands.

## Check provenance, documentation, and rollback

Import again and compare the **whole imported output**, including the manifest,
with the first import. There should be no further changes. Review actual content
and transformation descriptions, not only the reported hashes.

Compare the prior manifest's destination set with the new manifest and reviewed
plan. The importer does not automatically delete obsolete files. Remove only
reviewed obsolete importer-owned outputs; preserve authored files and unrelated
assets. Stale JavaScript under `public/` can otherwise remain embedded and served.

Preserve supplied license and attribution bytes, including fonts and extensionless
notices. New asset types may need MIME/embedding support in `server/build.rs` and
HTTP evidence; a file existing on disk does not prove it ships in the executable.
Keep generated build/test files out of `public/`.

Update the relevant source documentation:

- `README.md`: app asset version and distinction from native engine fixes/backend.
- `PORT.md`: frozen desktop release/commit and substantive adapter changes.
- `THIRD_PARTY_NOTICES.md`: desktop pin and any changed licenses/attributions.
- `docs/GUIDE.md`: maintenance pin and workflow details that actually changed.
- `CHANGELOG.md`: user-visible upgrade, compatibility, and limitations.
- `docs/ACCEPTANCE.md`: dated evidence and explicit unrun areas; do not relabel
  historical Linux results as acceptance of the new assets.

An asset-only rollback restores the prior desktop pin, reviewed plan, transformations,
and generated outputs together. No backend/data migration should be introduced
incidentally. Live binary rollback/deployment remains a separately authorized task.
