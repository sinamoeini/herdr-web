# Acceptance ledger — October 31, 2026

**Implemented, with scoped acceptance and explicit remaining limitations.** This document summarizes recorded test results, not the status of any personal deployment. Successful suites do not establish every interaction in the implementation contract. No core requirement is reclassified outside scope merely because it lacks evidence.

## Environment and evidence

GitHub CI is configured to run all 12 current native acceptance suites on every
pull request and push to `main`, and before creating a draft release. Workflow
artifacts include per-suite exit codes/logs, browser evidence, executable/backend
hashes, and the tested source SHA. Configuration alone is not a passing run: the
results below remain historical until a specific new workflow run is verified.
Physical hardware, external-host SSH, and the scoped/manual cases below remain
distinct from automated CI acceptance.

Ubuntu 24.04 x86_64; Rust/Cargo **1.99.1**; original Herdr **0.9.5**, public protocol **22**, native generation **1**. Python Playwright **1.64.1**; Chromium **156.0.8078.4** and Firefox **157.0**. No application Node/npm/Bun/bundler workflow. Optional headed permission tests use Xvfb, X11 clipboard ownership, and PulseAudio **16.1** with a private null sink.

Historical raw logs and deployment-specific evidence are kept in a private archive outside the repository. They are not shipped as plugin assets or independently available from this checkout. Fresh runs write logs/screenshots under ignored `test-results/`. A multi-suite invocation can fail overall while individual suites pass; only successful sections count as evidence.

| Suite | Recorded successful scope |
| --- | --- |
| Locked Rust suite | **34 tests** and release build. |
| `ui.py`, `integration.py` | Both browsers: desktop UI, actual tab/space clicks, startup delay, independent views, blocked reads, PID/variable persistence through web restart, and Kitty pixels/removal. |
| `security.py`, `resources.py` | HTTP rejection, 32 attachments, busy ownership, stalled-output cleanup, and all 83 selected license/provenance files fetched byte-for-byte. |
| `ssh.py`, `graphics_browser.py`, `git_failures.py`, `services.py` | Isolated loopback SSH, graphics, Git fault handling, and bounded services. |
| `watch_resources.py` | Watch-resource capacity and cancellation cycles. |
| `plugin.py` | Native lifecycle, including registration-root replacement. |
| `permissions.py` | Headed real clipboard paste/private upload/no Enter and audio gesture restrictions, both browsers. |
| `view_edges.py` | Actual sidebar/tab clicks, independent views after native scenes, park/repeated park/preview typing/keep/kill, both browsers. |

Supplementary local JavaScriptCore/provenance suite: **13 tests**. These are not Linux browser tests. `test/plugin.py` exercises the manifest's release executable, not `HERDR_WEB_BINARY`. Supporting candidate tests use that override. Expected busy-attachment failures, nonrepository Git inspection failures, and canceled watch requests must not be mistaken for uncaught page errors.

## Required areas

“Tested” means the stated invariant has evidence. “Scoped” calls out narrower coverage. “Limited” names a genuine unresolved guarantee. None means a pixel-perfect or exhaustive parity certification.

| Area | State | Evidence and remaining scope |
| --- | --- | --- |
| Cargo-only build | Tested | Locked release builds, deterministic embedded assets, moved-executable smoke. Cargo-only shell wrapper; no JS build tool. |
| Static routes | Tested | Real MIME/header/asset checks; unknown assets, source files and `.git` return 404. Candidate also serves extensionless licenses and icon attributions as plain text; all 83 selected notice/provenance files match source bytes (`security.py`). |
| HTTP boundary | Tested | Foreign/malformed Host, Origin, upgrade, JSON type and oversized bodies rejected by real server. |
| Public API | Tested | Real Unix sockets: fragmented UTF-8/errors, missing newline, 8-MiB bound, four-second timeout, protocol-23 rejection, concurrent independent connections. |
| Command validation | Tested | Unit validation and real rejection-before-mutation checks; supported backend mutations pass. |
| Terminal startup | Tested | Both browsers delay input by 750 ms; actual prompt/output has no startup-reply garbage. |
| Capability parser | Tested | All fragmentation points, incomplete EOF, unrelated escapes/Unicode/literal replies and later identical queries. |
| PTY disposal | Tested | Real blocked shell survives detach without newline/EOF; duplicated descriptor primitive tested. |
| Terminal persistence | Tested | PID and variable survive reload, disconnect and web restart; native-plugin restart also tested. Host reboot is explicitly excluded. |
| Terminal ownership | Tested | Busy response/retry without takeover; existing owner survives. |
| Backpressure | Scoped tested | 32 attachment cap/release; fault-injected stalled PTY producer remains bounded and child reaped (observed RSS growth 1,052 KiB). Not an exhaustive network soak. |
| Desktop visual design | Scoped tested | Official panels/fonts/icons/palettes, main/alternate layouts, screenshots and dimension assertions. No claim of native pixel identity or exhaustive visual comparison. |
| Empty session | Tested | Both browsers create first space from usable empty state. |
| Core operations | Scoped tested | Real create/rename/move/split/ratio/swap/zoom/close service checks; UI local split focus. Every drag gesture/destructive dialog combination is not certified. |
| Independent views | Limited upstream guarantee | Both browsers keep distinct selected tabs even after native graphics scenes; no public focus mutation. Native endpoint changes backend default-focus fields: see limitation below. |
| Focus routing | Tested | Rename typing, palette/theme, terminal input, native Run, local split focus; browser-reserved shortcut handling checked. |
| Context menus | Tested | Tab → space → other tab replaces exactly one popup and suppresses handled browser context menu, both browsers. |
| Context coordinates | Tested | Direct contextmenu coordinates and keyboard anchor beside correct target. |
| Context actions | Tested | Release retains popup; Rename/Cancel targets correct object, both browsers. |
| Live events | Scoped tested | External mutations update views; web restart reconnects; changed pane subscriptions exercised. Dedicated backend-recovery stress remains untested. |
| Folder observation | Tested | External changes/missing folder, ignored dirs, panel cancellation; 64-watch capacity and three descriptor/permit recovery cycles. |
| Text previews | Scoped tested | UTF-8/highlight/plain large text, binary/FIFO/device rejection, size limits; growth bounded by reader implementation. Deterministic concurrent-growth race not separately fault-injected. |
| Git | Scoped tested | Disposable status/diff/stage/commit/worktree create/remove; official Changes preview. Every worktree warning-dialog gesture not automated. |
| Git failures | Tested | Real 15-second timeout, stdout/stderr excess, argument rejection, four-job limit, cancellation/shutdown reap. |
| Parking | Tested | Real park/repeat/preview typing/keep/confirmed kill in both browsers, shell PID unchanged until intentional kill. |
| Child workers | Scoped tested | Child/grandchild metadata, moves, parent close, process/long-name persistence through web restart. Validation/depth/cycle guards exist; exhaustive externally forged metadata chains not live-tested. |
| Agent launch | Scoped tested | Approved executable catalog and explicit launch/rejection; no automatic launch. Every third-party agent executable is not installed/exercised. |
| Launch quoting | Tested | Real shell argv spaces/quotes/empty strings; count/length/NUL bounds. |
| Image upload | Scoped tested | Format/signature/private files; real trusted X11 clipboard paste and no Enter while shell blocked, both browsers on candidate. Physical drag/drop/denied clipboard path still manual. |
| Native graphics | Tested | Real Kitty pixels/crop/reload/zoom/resize/delete in both browsers; obsolete bitmaps closed. |
| Graphics limits | Scoped tested | Bounded integer/frame/image decoding, truncation, text-patch regression and stale browser-generation logic. Full adversarial native-server partial-read/resize fault matrix not executed. |
| Layout editor | Tested | Copy/edit/save/switch/download/delete and invalid-layout preservation; built-ins immutable. |
| Custom panels | Tested | Trusted test panel reload/disposal, containment/traversal/symlink rejection. Trusted application code, not sandboxed. |
| Herdr 98 | Scoped tested | Desktop/taskbar/Start/submenus/Run real terminal/windows drag/games/Clippy; original fonts. Audio playback/permission events pass in null sink; physical audibility and full game completion not certified. |
| SSH rejection | Tested | Injection/bad ports; isolated real sshd unknown key and refused listener; no registered/orphan tunnel. |
| SSH success | Local fixture tested; external untested | Real isolated loopback SSH forwarding, HTTP/terminal input, disconnect and shutdown. No `remote_test_host` supplied. |
| Plugin context | Tested | Supplied plugin socket wins over conflicting ambient session; isolated XDG catalog. |
| Plugin races | Scoped tested | Concurrent starts one server; restart/stop; occupied unrelated listener survives. Exhaustive start/stop scheduling interleavings not stress-certified. |
| Plugin lifecycle | Tested | Status/Stop/Restart/disable/unlink/parent loss/backend loss/registration-root change/stale-control recovery; shell preserved where backend remains alive. |
| Resource cleanup | Scoped tested | Repeated PTY/watch teardown, Git cancellation, graphics bitmaps, owned SSH and plugin cleanup. Not an unbounded-duration cross-service soak test. |

## Resolved regressions and remaining limitations

- The former file-preview assertion raced scheduled DOM disposal. Its eventual cleanup assertion now passes; hidden watches are separately checked for cancellation.
- Native graphics originally rejected legacy text patch tag 19. The pinned protocol distinguishes it from optional surface deltas; bounded parsing/skipping now passes real graphics and Rust regressions.
- Real Ctrl+V was consumed by xterm before image paste. Key routing now yields to browser paste; real trusted clipboard tests pass.
- The extension-only embed filter omitted extensionless licenses and icon attribution Markdown. The asset builder explicitly includes these as plain text and passes HTTP byte comparisons. Source copies were already preserved.
- The first view-edge fixture and the older integration fixture directly invoked adapter selection during startup rather than waiting for visible controls. Both now drive real sidebar/tab clicks and assert the intended selected tab and ready pane; the view-edge test also requires native scenes. Both pass in Chromium and Firefox. Earlier failure logs remain in the private archive.
- **Upstream default-focus limitation:** in Herdr 0.9.5 native tab selection calls `set_default_shell_target_from_client`. `view_edges.py` observes the public default tab change from the first to second window's tab, while both browser selections remain independent. Strict unchanged backend default focus is not achieved. Changing it requires an upstream-compatible protocol/backend change or a graphics architecture decision, not a hidden compensating public focus RPC. Do not upgrade the user's Herdr silently.
- No external SSH host, physical desktop drag/drop/clipboard-denial verification, or physical speaker test was supplied. Loopback SSH and real X11/null-sink tests are not substitutes for those claims.

The remaining scoped cases above are untested, not claimed blocked by optional remote access. Current source is usable and extensively tested, but the full contract's exhaustive local acceptance is not declared complete. Deployment procedures and rollback guidance are in `docs/OPERATIONS.md`; personal deployment records remain outside the repository.
