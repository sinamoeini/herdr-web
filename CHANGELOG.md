# Changelog

## Unreleased

The initial release is in preparation. No binary release is published yet.

### Added

- Build and smoke-test a Docker image in CI; publish passing `main` pushes to GHCR with commit-SHA and latest tags.
- Start an isolated Herdr backend and the web server automatically when the Docker container starts.
- Select the remote web port in the machine dialog and view each connection's port.
- Connect to separate Herdr Web instances on one SSH host and disconnect each forward independently.
- Run opt-in acceptance against an existing isolated remote instance with `test/ssh_remote.py`.
- Record the native focus limit, required upstream change, and browser lifecycle coverage for original Herdr 0.9.5.

### Fixed

- Preserve the empty-session Create space button across refreshes so an in-progress click is not lost.
- Observe the audio permission baseline passively before test automation can grant user activation.
- Serialize split-divider saves, preserve the latest pending ratio, and verify backend persistence before reload in UI acceptance.
- Fail UI acceptance on unexpected HTTP, console, page, and active WebSocket errors; retain narrowly classified expected errors in artifacts.
- Capture expected acceptance-runner self-test output and explicitly install the pinned Rust toolchain in CI.
- Prevent a terminal attachment that becomes ready late from stealing focus from the newly selected split pane.
- Change the default native, plugin, and remote-machine web port to 8000.

Ctrl+L now reaches the focused terminal, so the shell can clear the screen.

- Restore child-agent launch from the pane menu through the existing backend.
- Allow Ctrl+R shell history search and the default Ctrl+Shift+O split shortcut.
- Implement the right-click preference and preserve access to the menu through pane headers.
- Remember each space's selected tab across space changes and page reloads.
- Restore Paper, Light/Dark/System appearance, and light terminal colors.
- Add keyboard pane resizing with persistent ratios and visible divider focus.
- Keep Tab navigation inside dialogs and restore focus when they close. Terminal previews retain Tab input.
- Return to the main layout when the user resets the default screen.
- Keep the final terminal row fully visible by excluding pane padding from the terminal size.
- Preserve configured Git author and committer identity in browser commits. Reject missing identity before committing.

### Changed

- Upgrade browser-adapted Herdr app panels/assets from v0.1.9 to v0.1.11.
- Retain upstream file-panel context isolation, picker selection visibility, sidebar fixes, and binding-aware shortcut hints.
- Adapt virtual picker positioning and native wheel direction to browser events. Original Herdr 0.9.5/public protocol 22 remains unchanged; native desktop engine fixes are not part of this asset upgrade.

### Added

- Browser-adapted desktop panels in a standalone Cargo-built executable.
- Native plugin Start, Stop, Restart, and Status actions.
- Persistent terminal attachments, native graphics, and independent browser views.
- File/Git panels, layouts, parking, agent workflows, and owned SSH forwarding.
- Linux browser/native acceptance CI and draft binary-release packaging.

### Compatibility and limitations

- Linux x86_64 and original Herdr 0.9.5/public protocol 22.
- Loopback-only access; public/shared hosting is unsupported.
- Scope and remaining limitations are recorded in `docs/ACCEPTANCE.md`.
