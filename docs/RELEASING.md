# Publishing a GitHub release

## What automation covers

Pull requests and pushes to `main` run local Python/provenance checks, manifest
validation, and shell syntax checks. macOS runs JavaScriptCore logic tests; Linux
also runs locked Rust tests, builds the executable, and exercises release
packaging. A reusable Linux acceptance job runs all 12 current native suites:
`integration.py`, `ui.py`, `graphics_browser.py`, `view_edges.py`, `services.py`,
`security.py`, `resources.py`, `git_failures.py`, `watch_resources.py`, `plugin.py`,
`ssh.py`, and `permissions.py`. It uses checksum-pinned original Herdr 0.9.5 and
Playwright 1.64.1 with Chromium and Firefox, including headed native clipboard
and audio tests on isolated X11/private PulseAudio. This is not physical-device
or external-host certification; the historical foundation-only fixture is not
used as a substitute for desktop acceptance.

A pushed `v*` tag runs the Linux release workflow. It verifies that the tag agrees
with both Cargo and plugin versions, requires the same acceptance job to pass,
runs checks, builds on Ubuntu 24.04, packages
the Linux x86_64 executable, and creates a **draft** GitHub release with the archive
and `SHA256SUMS`. The package includes licenses, provenance, documentation, and a
manifest that runs the bundled executable without a source build hook.

Workflow actions are pinned by commit; the Rust toolchain and Cargo dependencies
are pinned in source. No personal cloud account or deploy credentials are needed.
The draft-release step uses the repository's GitHub token. Acceptance uses only
read-only repository permissions and no personal credentials. Full test artifacts
and the per-suite summary are retained for seven days even on failure; later
suites still run after an earlier suite fails.

## Maintainer checklist

1. Confirm CI and the relevant isolated Linux/browser suites pass. Record what
   was actually tested and update known limitations without upgrading historical
   evidence into claims about a new build.
2. Set the same version in `server/Cargo.toml` and `herdr-plugin.toml`; update the
   workspace package entry in `Cargo.lock` with Cargo. Update download examples
   in `README.md`. Review the toolchain and license inventory if dependencies
   changed.
3. Commit and push to the chosen GitHub repository. After review, create and push
   a version tag, for example:

   ```sh
   git tag -a v0.1.0 -m "Herdr Web v0.1.0"
   git push origin v0.1.0
   ```

4. Inspect the draft release. Download the archive and checksums, verify them,
   extract outside the checkout, and check `./herdr-web --help`. With original
   Herdr installed, link the extracted directory and exercise Start/Status/Stop
   in an isolated session. Do not test in a user's active terminal.
5. Review release notes, compatibility, and limitations; mark previews as
   pre-releases as appropriate. Publish the draft manually when ready.

Do not move an already published version tag. A failed release workflow may be
rerun before it creates a draft; if a draft already exists, inspect it rather
than deleting or overwriting published assets automatically.

## Local packaging

After building on Linux x86_64:

```sh
./build.sh
python3 tools/package_release.py --tag v0.1.0
cd dist
sha256sum --check SHA256SUMS
```

The packager uses an explicit file inventory rather than archiving the checkout.
Personal files, agent skills, test logs, caches, source trees, and credentials are
not bundled. Checksums detect corruption; they are not a signature or independent
proof of authenticity. There are no macOS, Windows, ARM, or musl release binaries.
