# Installation and operation

Herdr Web supports Linux with original Herdr 0.9.5. See `README.md` for
prerequisites, environment variables, native plugin actions, and test commands.
No cloud provider or personal infrastructure is required.

## Installation

Build before linking the native plugin:

```sh
./build.sh
herdr plugin link .
herdr plugin action invoke local.herdr-web.start
herdr plugin action invoke local.herdr-web.status
```

For a named backend session, put `--session NAME` before `plugin` in each command.
The manifest runs the release executable inside the linked checkout. Keep that
checkout available while the plugin is registered.

For a standalone installation, `tools/install.sh` runs the locked Rust tests,
builds the release, preserves any existing installed binary and process inventory
under `$HOME/.local/state/herdr-web/`, and atomically installs to
`$HOME/.local/bin/herdr-web`. It does not start or stop services. This installed
copy is separate from the native plugin's checkout executable.

## Safe updates and rollback

1. Record the active session, plugin root, listener, executable, and launch method
   in private operational notes outside the repository.
2. Build a candidate with `CARGO_TARGET_DIR=target/candidate ./build.sh`. Test it
   against isolated sessions and ephemeral ports, never an active user terminal.
   `HERDR_WEB_BINARY` selects the candidate in supporting test harnesses;
   `test/plugin.py` instead uses the manifest's release path.
3. Save the exact active executable and launch details before replacing it.
   Coordinate a web-only restart window; do not stop Herdr or unrelated listeners.
4. Atomically replace the appropriate executable and restart only the web plugin
   or standalone web process. Native plugin Restart uses
   `herdr plugin action invoke local.herdr-web.restart`.
5. Check plugin status and HTTP availability at the reported URL. Test input in
   an isolated terminal. If validation fails, restore the saved web executable
   and repeat the same web-only restart procedure.

Stopping or restarting the web interface preserves backend-owned terminals.
A host reboot does not preserve shell processes or variables. A new backend
requires an explicit plugin Start; no automatic startup hook exists.

## Access and private records

The listener binds to loopback only. For remote access, use the documented SSH
forwarding workflow or a separately managed authenticated transport. Public/shared
hosting is unsupported without a separate authentication design.

Keep cloud account details, instance identifiers, credential profiles, host paths,
access commands, and deployment inventories outside this repository. Never store
credentials in project files. Test logs/screenshots are generated under ignored
`test-results/`; review and sanitize evidence before sharing it. There is no
personal deployment configuration in the plugin build or runtime.
