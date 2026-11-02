# Herdr Web

[![CI](https://github.com/sinamoeini/herdr-web/actions/workflows/ci.yml/badge.svg)](https://github.com/sinamoeini/herdr-web/actions/workflows/ci.yml)

The Herdr desktop interface in your browser, powered by the original Herdr server.
One Rust executable includes the UI, fonts, icons, and terminal renderer. Closing
the browser or restarting the web interface preserves backend-owned terminals.

Native plugin integration is implemented, with recorded Chromium and Firefox
acceptance results. This project is not a claim of upstream endorsement or complete
desktop parity. See [acceptance and limitations](docs/ACCEPTANCE.md).

## Supported platform

- Linux x86_64; release binaries are built on Ubuntu 24.04 with glibc.
- Original Herdr **0.9.5**, public protocol **22**, installed separately.
- Browser panels/assets pinned to Herdr app **0.1.11**; native desktop engine fixes are not included.
- Chromium or Firefox and an existing interactive Herdr session.

Other operating systems, architectures, and Herdr versions are not certified.
The interface listens on loopback only; public/shared hosting is unsupported.

## Install a release

Once a release is published, download its `.tar.gz` archive and `SHA256SUMS` from
the [Releases page](https://github.com/sinamoeini/herdr-web/releases). Cargo, Python, and a JavaScript toolchain are
not needed to run the downloaded package.

For the initial version, verify and extract on Linux:

```sh
sha256sum --check SHA256SUMS
tar -xzf herdr-web-v0.1.0-x86_64-unknown-linux-gnu.tar.gz
cd herdr-web-v0.1.0-x86_64-unknown-linux-gnu
herdr plugin link .
herdr plugin action invoke local.herdr-web.start
herdr plugin action invoke local.herdr-web.status
```

Substitute the downloaded version in the archive and directory names. Keep the
extracted directory available: the linked plugin runs its bundled executable.
For a named session, put `--session NAME` before `plugin` in each command.
Open the URL reported by Status, normally `http://127.0.0.1:8004`.

Alternatively, run the downloaded executable directly:

```sh
HERDR_BIN="$(command -v herdr)" ./herdr-web
# Or select an existing named session:
HERDR_BIN="$(command -v herdr)" HERDR_SESSION=example ./herdr-web
```

Without `HERDR_SESSION`, standalone operation requires the session named `default`.
An occupied port fails safely; set `PORT=0` to select an available loopback port.

## Build from source

Install Rust via rustup, a C linker, and Git. The checked-in toolchain and Cargo
lockfile pin the build; no Node, npm, Bun, bundler, or reference checkout is needed.

```sh
git clone https://github.com/sinamoeini/herdr-web.git
cd herdr-web
./build.sh
herdr plugin link .
herdr plugin action invoke local.herdr-web.start
```

Source checkouts must be built before linking. Use `CARGO_BUILD_JOBS=1 ./build.sh`
on small hosts. `tools/install.sh` provides an optional standalone installation,
with rollback copies, without starting or stopping services.

## Features

- Persistent terminals, tabs, spaces, splits, and independent browser views.
- Desktop panels, file previews, Git changes/worktrees, themes, and layouts.
- Kitty graphics, image paste/drop, parking, agents, and managed workers.
- Native Start, Stop, Restart, and Status actions with owned-resource cleanup.

Browser permissions and reserved shortcuts differ from native desktop behavior.
Native graphics can change backend default-focus fields in Herdr 0.9.5, even
though existing browser selections remain independent. Host reboots do not
preserve shell processes. See the acceptance ledger before relying on a feature.

## Documentation

- [Technical guide and test commands](docs/GUIDE.md)
- [Installation, safe updates, and rollback](docs/OPERATIONS.md)
- [Acceptance results and known limitations](docs/ACCEPTANCE.md)
- [Local API](docs/API.md) and [Git operations](docs/GIT.md)
- [Contributing](CONTRIBUTING.md) and [publishing releases](docs/RELEASING.md)
- [Changelog](CHANGELOG.md) and [security policy](SECURITY.md)
- [Source provenance and adaptations](PORT.md)

## License

Project code is [Apache-2.0](LICENSE). Copied panels, fonts, icons, and dependencies
retain their original licenses; see [third-party notices](THIRD_PARTY_NOTICES.md).
