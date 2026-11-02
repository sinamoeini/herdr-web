# Browser port and provenance

## Frozen inventory

- Official desktop: `herdrdev/herdr-app`, release `v0.1.11`, commit `e12fd31866f33cb613922d0b33ea506e6f23ce3c`. Selected `defaults/` frame/panels/layouts/icons/sounds/games, `assets/fonts/`, and license files are copied; palettes are extracted from `src/settings.rs`.
- Original backend behavior: `herdrdev/herdr`, commit `2ecf19a92fc3a7fff75b6ce3655ef04f4625ec01`, release 0.9.5, public protocol 22, native endpoint generation 1. No whole repository, desktop Rust backend, or community frontend is vendored.
- Browser dependencies: xterm 6.0.0, fit 0.11.0, highlight.js 11.11.1, acquired without executing Node/npm/Bun.

`tools/asset-plan.json` is the explicit selected-file inventory. `public/desktop/UPSTREAM.json` records each copied file's upstream path, commit, original/adapted SHA-256, and transformations. `public/vendor/UPSTREAM.json`, `SHA256SUMS`, and `licenses/` record browser provenance. Reference checkouts live outside the deliverable and are not build/runtime dependencies.

## Inspected pinned behavior

Protocol review covered `src/protocol/wire.rs`, `src/protocol/endpoint.rs`, `src/client/handshake.rs`, and `src/client/terminal_setup.rs`: framing, envelope fields/discriminants, bincode standard integers, codec negotiation, socket derivation, full-frame geometry/images, and startup capability exchange. Unknown public versions, native generations, codecs, and malformed frames fail visibly. The native decoder negotiates full frames, not unimplemented delta/reuse/scroll transports.

Plugin review covered `src/app/api/plugins/runtime.rs`, `env.rs`, `manifest.rs`, `panes.rs`, `context.rs`, `src/cli/plugin.rs`, and `src/plugin_paths.rs`: exact environment, action ownership, registration, paths, and pane metadata. Actual original-0.9.5 requests were exercised in isolated sessions. Workspace/tab/pane/terminal identifiers are kept distinct.

## Recorded adaptations

The repeatable importer reads original Git blobs at the frozen revision and hashes them **before** changes. It refuses to overwrite inconsistent generated output. Per-file changes in `UPSTREAM.json` include:

- Bare runtime imports become local browser modules; ambient `props()` becomes instance-owned `this.webProps` across asynchronous work.
- Files use the bounded Rust list/read APIs. Folder watches carry their owning panel and cancel on disposal; command waits are cancellable. Diff parsing/measurement stops at 1,500 rows.
- Current machine is described as Local server, updates as manual, telemetry as disabled. Unavailable feedback submission is removed, not a successful no-op.
- Commit controls disclose staging all changes. Pane menus expose rename, parking, and child-agent launch. The agent picker retains the selected parent. The parked overlay reuses the agent panel.
- Delegated Herdr 98 roles receive correct properties/cancellation. SSH messages explain browser forwarding rather than native-core setup.
- Built-in palettes become JSON. Font/image/sound binaries and font attribution files remain unchanged.

New browser runtime responsibilities:

| File | Responsibility |
| --- | --- |
| `gpui.js` | Fluent controls via DOM/CSS, keyed reconciliation, SVG masks, pointer/key/focus events, tasks/timers, cleanup |
| `base.js` | Flex/input/scroll/editor/diff controls and text-node syntax previews |
| `herdr.js` | Desktop model projection, local selection, bounded commands, programs/statuses, themes/browser preferences, refresh/watch/key routing |
| `app.js` | Official layout interpreter, keyed panel mounting, per-pane xterm, shared geometry, overlays, previews/layout editor, images/events |
| `native-images.js` | Opaque-key asset cache, retained scenes, crop/offset/z-order, device-pixel canvas sizing, async generation/disposal |

Shared right-button callbacks use **only** `contextmenu` with `preventDefault`, actual pointer coordinates or keyboard target bounds, and one replaceable application popup. The DOM root is called `webRoot` to avoid collision with official Files/Changes filesystem `root`. Panel-owned resources/abort signals prevent hidden panels from continuing long polls. Browser preferences never write native desktop configuration.

## Rust adapter ownership

Public API requests, CLI PTY attachments, and native graphics all use one cached socket selection. Terminal children use duplicated master descriptors without the portable-pty writer's newline/EOF destructor behavior. Only the first exact startup query is consumed/answered locally, across arbitrary chunks; later queries and ordinary bytes pass unchanged. Output/input queues, semaphores, deadlines, image/frame budgets, defensive file reads, inotify bounds, and Git allowlists bound resource use.

Parking and worker links use real backend moves/metadata, not browser records. Plugin start/restart remains owned by its foreground action; private per-socket control state/flock lives through HTTP draining. SSH processes retain explicit ownership and strict host-key checks. Stopping the web process must not stop Herdr or persistent terminals.

## Intentional differences

Remote instances open separately, without a merged sidebar. Browser windows/fullscreen replace native controls; rendering is not pixel-identical. Clipboard/audio follow browser permissions. Some native shortcuts are browser-reserved. Updates are manual; telemetry/feedback are disabled. Trusted panels are not sandboxed. Subprocesses do not automatically become managed workers. Native clients see the parking workspace. New Herdr servers require an explicit plugin Start.

## Verification status — October 31, 2026

Locked Linux builds and 34 Rust tests pass. Real startup (750-ms delayed browser input), blocked-read disposal, PID/variable persistence through reload/web restart, independent views, and Kitty pixels pass with original Herdr 0.9.5 in Chromium 156.0.8078.4 and Firefox 157.0. The previously failing file-preview cleanup assertion has passed, along with official UI/context menus, personal layouts/custom panels, Herdr 98 games/windows/Run, graphics crop/resize/zoom/deletion, bounded services, and isolated native-plugin lifecycle checks. Thirteen local JavaScriptCore/provenance tests provide supplementary evidence, not a substitute for Linux browsers.

For Ctrl+V, xterm yields the browser paste shortcut so the trusted image paste event reaches the upload handler. Headed Chromium and Firefox pass real X11 clipboard insertion without Enter, and denied-before-gesture/allowed-after-gesture audio using a private PulseAudio null sink. These tests do not prove physical speaker audibility or non-Linux permissions.

Additional adaptations: DOM roots use `webRoot` to avoid official filesystem `root` collisions; nested event callbacks restore their previous event context; menu ancestry and popup stacking preserve Herdr 98 submenus; floating windows retain intrinsic height; refresh requests share a drain and discard stale selection revisions; explicit splits select locally. Herdr 98 executable detection uses the Rust catalog rather than an unsupported shell loop. Original files and transformations remain recorded by the importer.

The asset builder also embeds the explicitly named extensionless licenses, icon attribution Markdown, and vendor checksum list as plain text. These previously existed in source but were omitted by the extension-only filter. The security suite checks each embedded notice byte-for-byte against its source; arbitrary checkout files remain inaccessible.

### Native graphics and focus caveat

The decoder validates/skips legacy tag-19 `PaneSurfacePatch` text-only updates. Pinned `src/protocol/surface_reuse.rs`, `src/server/render_stream.rs`, and native graphics tests show these are distinct from optional named surface-delta/reuse/scroll codecs, which remain disabled. Ignoring the distinction incorrectly disconnected graphics during ordinary text updates; bounded/truncation regression tests now cover it.

Browser selections are independent and issue no public focus RPC. However, pinned `src/server/headless/endpoint_requests.rs` and `client_views.rs` route native connection tab selection through `set_default_shell_target_from_client`. Thus native graphics may change the public snapshot's default-focus fields. The strict guarantee that these fields never change is **not met by this backend**. The port neither races to restore global focus nor patches/upgrades the user's Herdr installation. Existing valid browser selections remain local.

`docs/ACCEPTANCE.md` lists scoped evidence and remaining checks rather than claiming full feature parity. External-host SSH remains untested (the real isolated loopback SSH fixture passes). `docs/OPERATIONS.md` describes generic installation, rollback, and the host-reboot distinction.
