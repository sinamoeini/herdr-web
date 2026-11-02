# Herdr Web technical guide

Official Herdr desktop panels adapted to a browser, backed by the **original Herdr 0.9.5 server**. One Rust executable embeds the interface, fonts, icons, xterm.js, and native browser modules. Cargo is the only application build system: no Node, npm, Bun, bundler, CDN, desktop Rust dependency, `herdr-core`, or full-TUI wrapper.

**Status:** native plugin integration is implemented. Real terminal persistence, desktop UI, Kitty graphics, bounded services, and plugin lifecycle have recorded Linux test evidence in Chromium and Firefox. This is not a claim of complete acceptance: see `docs/ACCEPTANCE.md` for scoped results and remaining limitations, and `docs/OPERATIONS.md` for installation and rollback guidance.

## Architecture

```text
Official desktop panels + browser DOM adapter
     per-pane xterm terminals + image canvases
                     HTTP/WebSocket
          embedded Rust web executable
       /                 |                  \
 public JSON socket   PTY + herdr attach   native endpoint
       \                 |                  /
                  original Herdr
```

Herdr owns shells, processes, workspaces/spaces, tabs, terminal IDs, and geometry. Web connections are temporary attachments. Closing a browser or stopping the web executable does not intentionally close Herdr's terminals. Text/input use CLI attachments; decoded images use separate native connections to the same selected server.

Browser selection is window-local and sends no public focus mutations. Herdr 0.9.5's native graphics connection can nevertheless update backend *default* focus fields while selecting its tab. Independent browser views do not follow those fields once selected; strict preservation of the backend default focus is an upstream limitation. Web restarts preserve terminals; a host reboot does not preserve shell processes or variables.

## Build and standalone operation

Prerequisites: Linux, Rust/Cargo supporting edition 2024 and the lockfile, a C linker, original Herdr 0.9.5, a supported interactive shell and real `HOME`, and Chromium/Firefox. Development used Ubuntu 24.04 x86_64 and Rust/Cargo 1.99.1. Git supports Changes/worktrees; OpenSSH supports remote forwarding. Other Herdr releases are not claimed compatible; public protocol versions other than 22 fail visibly.

```sh
./build.sh
# Runs: cargo build --locked --release -p herdr-web
herdr session list --json
HERDR_BIN="$(command -v herdr)" PORT=8004 ./target/release/herdr-web
# Or select an existing named session:
HERDR_BIN="$(command -v herdr)" HERDR_SESSION=example PORT=8004 \
  ./target/release/herdr-web
```

Use `CARGO_BUILD_JOBS=1 ./build.sh` on small hosts. The executable runs outside the checkout; normal assets are embedded with a deterministically sorted build table. Unknown assets/source files/`.git` return 404. No reference checkout is needed to build.

Without `HERDR_SESSION`, selection requires the session named `default`; arbitrary running sessions are not guessed. Missing/ambiguous selection fails. The exact selected Unix socket is printed to stderr and cached for the process lifetime. The listener URL is printed to stdout. `PORT=0` selects an available loopback port. An occupied port fails without stopping its owner; choose another port explicitly.

Keep the foreground process under your chosen supervisor and retain its PID/logs. SIGINT/SIGTERM cleans up owned attachments, subscriptions, watches, Git children, and SSH forwards, not Herdr. `--diagnostics` is a developer-only backend-free mode that still has local file/Git privileges; it is not an operational desktop. `tools/install.sh` builds and atomically installs the binary with rollback copies, without starting or stopping services.

### Environment

| Variable | Meaning |
| --- | --- |
| `HERDR_BIN` | Original Herdr executable; default `herdr` on `PATH` |
| `HERDR_SESSION` | Standalone session name; default `default` |
| `HERDR_CONFIG_PATH` | Intended Herdr configuration, preserved for discovery/attachments |
| `HERDR_WEB_MODS` | Optional directory of trusted custom-panel folders |
| `PORT` | HTTP port; default 8004, 0 selects an available port |
| `HOME`, `PATH`, `SHELL` | User home, installed-program discovery, supported shell environment |

Plugin context instead requires Herdr-supplied `HERDR_PLUGIN_ID`, `HERDR_SOCKET_PATH`, `HERDR_BIN_PATH`, `HERDR_PLUGIN_ROOT`, `HERDR_PLUGIN_CONFIG_DIR`, and `HERDR_PLUGIN_STATE_DIR`. The exact supplied plugin socket wins over conflicting ambient session selection. Attachment children clear unrelated `HERDR_*` values and explicitly receive the selected socket/configuration. Do not manufacture plugin context for normal installation.

## Native workflow plugin

Build **before** linking; the upstream link command does not run the build hook:

```sh
./build.sh
herdr plugin link .
herdr plugin action invoke local.herdr-web.start
herdr plugin action invoke local.herdr-web.status
herdr plugin log list --plugin local.herdr-web --limit 5
herdr plugin action invoke local.herdr-web.restart
herdr plugin action invoke local.herdr-web.stop
```

For a named session place `--session NAME` **before** `plugin` in each command. Find configuration with `herdr plugin config-dir local.herdr-web`. Its optional `port` file overrides inherited `PORT` (0 is supported). Different backend servers need available listener ports.

Start/Restart remain foreground children of the native plugin action, without a visible hosting tab or detached daemon. A private per-backend control socket/flock provides single-instance ownership. Status reports `{"running":true,"url":"http://127.0.0.1:8004","pid":12345}`. The lock stays held while HTTP drains. Disable, unlink, changed registration root, backend loss, or parent loss requests shutdown. A new Herdr server requires an **explicit Start action**; no automatic startup hook exists.

## Browser operation

Official panels provide spaces/tabs/pane controls, menus, agents, files/previews, Changes/worktrees, appearance, parked previews, and Herdr 98 components. `Ctrl+Shift+P` opens the palette; double-click a tab to rename. Browser-reserved shortcuts remain reserved. Browser-safe creation aliases are `Ctrl+Alt+T` (tab), `Ctrl+Alt+N` (space), and `Ctrl+Alt+D`/`Ctrl+Alt+E` (splits).

Selection/zoom use per-window session storage. Appearance, panel choices, layouts, and pins use local storage, never desktop configuration files. Ordinary selection does not send global Herdr focus operations; real objects and geometry remain shared. Fullscreen/browser windows replace native window management. Telemetry/feedback submission are disabled, updates are manual, and browser rasterization is not pixel-identical to native rendering.

### Personal layouts and trusted panels

The Layout editor copies, edits, saves, switches, downloads, or deletes personal layouts while leaving built-ins immutable. Invalid JSON/structure must preserve the last usable layout. Limits: 200 nodes, 20 levels, unique block IDs, exactly one `content.tab`, and a personal name of 1–64 letters/digits/underscores/hyphens. Main, terminals, and Herdr 98 assets are included; their remaining interactive verification is recorded in the ledger.

Set `HERDR_WEB_MODS` to a directory containing `notes/main.js`, for example:

```js
import { View, div } from "/desktop/runtime/gpui.js";
export default class Notes extends View {
  render() { return div().p(12).child("My panel"); }
}
```

Add a layout block with `"content":{"mod":"notes"}`, retaining its single terminal block. Registered names use fewer than 80 ASCII letters/digits/underscores/hyphens. Only contained JS/JSON/CSS/SVG/PNG assets up to 2 MiB are served; traversal/symlink escapes fail. Panels are **trusted code with page privileges, not sandboxed extensions**; nothing downloads or enables third-party panels automatically. Use view-owned cleanup/abort signals for resources.

### Parking, agents, and managed workers

Park/Keep moves the real pane while preserving terminal/process identity; Kill requires confirmation. Reserved workspace `__herdr_web_parked__` is hidden from ordinary browser spaces but remains visible in native clients. Preview uses a real attachment without takeover; another owner can make it busy. Installed supported agents are discovered but never launched automatically.

A managed worker is a separate Herdr terminal linked by backend metadata, not an arbitrary subprocess. Obtain the **real parent terminal ID** from `/api/state`'s snapshot, not a pane ID, then intentionally create it:

```sh
curl --fail-with-body \
  -H 'Origin: http://127.0.0.1:8004' -H 'Content-Type: application/json' \
  --data '{"parent":"REAL_PARENT_TERMINAL_ID","name":"reviewer","argv":["codex"]}' \
  http://127.0.0.1:8004/api/child-terminal
```

Result: `{terminal,pane,parent}`. Empty `argv` creates a terminal without launching a command. Arguments are individually shell-quoted, including empty strings/single quotes; there is no generic HTTP shell-script endpoint. Source `herdr-web` stores `web_parent`/`web_name` metadata (long names use continuation tokens). Limits: 64 children/session, 32 levels, parent/name 128 bytes, 128 arguments of 8,192 bytes each, no NUL. Closing a parent does not close children.

### Images, files, and Git

Clipboard/drop PNG/JPEG/GIF/WebP uploads require matching MIME/signature and at most 10 MiB. Files use exclusive creation, mode 0600, in a private 0700 temporary directory. The quoted server-side path is inserted **without Enter**. Uploads intentionally survive web shutdown; manually remove `herdr-web-images-*` temporary directories only after terminal programs finish reading. No automatic expiry policy is implemented. Browser permissions govern real clipboard access/audio; synthetic events do not prove OS permissions.

Native inline graphics are separate from uploads: Herdr decodes terminal protocols, and a bounded native connection supplies assets/placements to a pointer-transparent canvas. Graphics errors do not enter ordinary terminal bytes.

Read-only file previews accept regular UTF-8 files up to 512 KiB, rejecting binary/device/FIFO inputs. Lists cap at 500 entries excluding `.git`; highlighting stops above 200,000 characters and diffs at 1,500 rows. Open-panel inotify long polls have slow fallback and disposal cancellation. Git uses explicit allowlisted argv, four jobs, 15 seconds, and 1 MiB per stream. Commit stages all changes, as the button says. See `docs/GIT.md`; repository configuration can influence Git, so this is **not a sandbox**.

## Local security and remote access

The listener binds **127.0.0.1 only**. Host accepts only parsed localhost/127.0.0.1/[::1] authorities; supplied Origins must match exactly. POST/WebSocket upgrades require matching Origin; JSON mutations need JSON Content-Type. Ordinary bodies cap at 16 KiB. Responses carry no-store/nosniff/CSP protections. The application has the local user's terminal/file privileges: public/shared hosting requires a separate authentication design and is unsupported.

Remote machines must already run Herdr Web. The machine picker creates an owned noninteractive SSH forward using existing keys/known-host records and strict host-key checks, targeting remote loopback. Maximum eight forwards. Instances open in **separate windows**, not a merged sidebar. Disconnect/shutdown reaps only owned forwards, not remote services.

The dialog's remote web port defaults to 8004.
It accepts integers from 1 to 65535 and rejects invalid values before SSH starts.
SSH uses the SSH port from your existing configuration.
The connection list shows each host and remote web port.
Its Disconnect control removes that connection.
Different web ports on one host use separate forwards.
Herdr Web disables SSH connection sharing and background startup for its owned forwards.
An existing SSH master connection stays separate.

Manual equivalent:

```sh
ssh -N -T -o BatchMode=yes -o StrictHostKeyChecking=yes \
  -o ExitOnForwardFailure=yes -L 127.0.0.1:8004:127.0.0.1:8004 -- HOST
```

Successful external-host SSH remains untested without a supplied host.
See [separate-host SSH acceptance](SSH-ACCEPTANCE.md) for the opt-in harness and cleanup requirements.

The local HTTP/WebSocket request and response envelopes are documented in `docs/API.md`.

## Tests

GitHub CI runs every current native acceptance suite on isolated Linux runners,
including Chromium/Firefox and headed clipboard/audio checks. With the following
prerequisites installed, `HERDR_BIN="$(command -v herdr)" python3 tools/run_acceptance.py`
runs the same 12 suites locally and records every suite's outcome. It must use the
manifest's `target/release/herdr-web`, not a candidate-binary override.

Build and optional browser test prerequisites must be installed explicitly:

```sh
cargo test --locked --workspace
cargo build --locked --release -p herdr-web
python3 -m venv "$HOME/.local/share/herdr-web-playwright"
"$HOME/.local/share/herdr-web-playwright/bin/pip" install playwright==1.64.1
"$HOME/.local/share/herdr-web-playwright/bin/playwright" install chromium firefox
# Linux browser system libraries may also need explicit installation.
PYTHON="$HOME/.local/share/herdr-web-playwright/bin/python"
HERDR_BIN="$(command -v herdr)" "$PYTHON" test/integration.py
HERDR_BIN="$(command -v herdr)" "$PYTHON" test/ui.py
HERDR_BIN="$(command -v herdr)" "$PYTHON" test/services.py
HERDR_BIN="$(command -v herdr)" "$PYTHON" test/security.py
HERDR_BIN="$(command -v herdr)" "$PYTHON" test/resources.py
HERDR_BIN="$(command -v herdr)" "$PYTHON" test/plugin.py
HERDR_BIN="$(command -v herdr)" "$PYTHON" test/graphics_browser.py
HERDR_BIN="$(command -v herdr)" "$PYTHON" test/git_failures.py
HERDR_BIN="$(command -v herdr)" "$PYTHON" test/watch_resources.py
HERDR_BIN="$(command -v herdr)" "$PYTHON" test/view_edges.py
```

To verify a candidate without replacing a running executable:

```sh
CARGO_BUILD_JOBS=1 CARGO_TARGET_DIR=target/candidate ./build.sh
HERDR_WEB_BINARY="$PWD/target/candidate/release/herdr-web" "$PYTHON" test/integration.py
# Headed native Linux clipboard/audio checks; requires Xvfb, libX11 and PulseAudio:
HERDR_WEB_BINARY="$PWD/target/candidate/release/herdr-web" xvfb-run -a "$PYTHON" test/permissions.py
```

`HERDR_WEB_BINARY` is a test-harness override, not an application setting. `test/plugin.py` instead executes the repository manifest's `target/release/herdr-web`. The permission test uses a private audio null sink and a real X11 clipboard, without Playwright permission grants or synthetic paste. It proves trusted browser paste/playback events, not physical speaker audibility or another operating system's permission behavior. Xvfb/PulseAudio are optional test tools, not build/runtime requirements. Test them on an isolated development host rather than silently changing a workstation's packages or audio service.

`test/ssh.py` additionally needs an existing OpenSSH server and passwordless sudo for an isolated fixture with generated test keys; it does not alter production SSH configuration. Local macOS JavaScriptCore/asset checks: `python3 -m unittest discover -s test -p 'test_*.py' -v`. These are not browser/Linux acceptance. `test/browser.py --foundation` is a narrow historical adapter fixture, not a replacement for real integration/UI suites.

Integration tests use unique temporary Herdr sessions, isolated XDG/configuration, disposable Git repositories, port 0, and owned-process cleanup. They must not mutate active user sessions. Logs/screenshots are retained in ignored `test-results/`. `docs/ACCEPTANCE.md` distinguishes passes, current failures, and unexecuted tests. On a physical desktop, also check native image copy/paste, image drag/drop, clipboard denial, and audible alerts after a click. External-host forwarding must use an isolated remote instance; the loopback SSH fixture does not prove external-host success.

## Sources, licenses, and measurements

Frozen desktop release: `v0.1.11`, commit `e12fd31866f33cb613922d0b33ea506e6f23ce3c`; original Herdr: `2ecf19a92fc3a7fff75b6ce3655ef04f4625ec01`. These are reference versions, not claims about latest releases. `PORT.md` describes source inspection/adaptations. `public/desktop/UPSTREAM.json` records original/adapted digests; `public/vendor/SHA256SUMS` verifies vendored browser files. Preserve licenses/attributions in `THIRD_PARTY_NOTICES.md` and the original asset folders.

For maintenance keep exact upstream checkouts **outside** this tree, then run `python3 tools/vendor.py import --desktop /absolute/herdr-app --plan tools/asset-plan.json`. Edit importer transformations, not generated panels. Run `python3 tools/sizes.py` to separate tracked source, untracked candidates, assets, Rust source, executable, Git metadata, and build output. Local deployment records and infrastructure access details should remain outside the repository; generated test logs/screenshots remain ignored.
