# Changelog

## Unreleased

The initial release is in preparation. No binary release is published yet.

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
