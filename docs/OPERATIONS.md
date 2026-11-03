# Installation and operation

Herdr Web supports Linux with original Herdr 0.9.5. See `README.md` for
prerequisites, environment variables, native plugin actions, and test commands.
No cloud provider or personal infrastructure is required.

## Installation

Release archives contain `herdr-web` and a native plugin manifest pointing at
that executable. Verify the published checksum, extract the archive, then run
`herdr plugin link .` from the extracted directory. Do not run `build.sh` for a
downloaded binary release. Keep the directory available while it is linked.

For a source checkout, build before linking the native plugin:

```sh
./build.sh
herdr plugin link .
herdr plugin action invoke local.herdr-web.start
herdr plugin action invoke local.herdr-web.status
```

For a named backend session, put `--session NAME` before `plugin` in each command.
The source manifest runs the release executable inside the linked checkout. Keep
that checkout available while the plugin is registered.

For a standalone installation, `tools/install.sh` runs the locked Rust tests,
builds the release, preserves any existing installed binary and process inventory
under `$HOME/.local/state/herdr-web/`, and atomically installs to
`$HOME/.local/bin/herdr-web`. It does not start or stop services. This installed
copy is separate from the native plugin's checkout executable.

## Docker

CI builds and smoke-tests the Linux amd64 image on pull requests and publishes
passing pushes to `main` as `ghcr.io/sinamoeini/herdr-web:sha-<full-commit-SHA>`.
The current passing tip of `main` also receives `:latest`. Each pushed tip is
built; intermediate commits in a single multi-commit push are not built separately.
If GHCR creates the package as private, authenticate to pull it, or change its
visibility in the repository owner's package settings to allow anonymous pulls.

```sh
docker build --platform linux/amd64 -t herdr-web:local .
docker run -d --name herdr-web --platform linux/amd64 --stop-timeout 20 \
  -p 127.0.0.1:8000:8000 -v herdr-web-home:/home/herdr herdr-web:local
docker logs herdr-web
docker stop --time 20 herdr-web
```

The image runs as UID 10001 with an isolated, checksum-pinned Herdr 0.9.5 backend.
It does not attach to a host Herdr session or start the native desktop app.
The supervisor waits up to 30 seconds for backend discovery, starts the web
server, and stops both children on container shutdown or either child's exit.
The health check requests the real backend-backed `/api/state` endpoint.
The backend binary and its supplied upstream/vendor licenses are checksum-verified during build;
project notices are in `/usr/local/share/doc/herdr-web`, and embedded browser/Rust
notices remain available over HTTP.
The base images, Rust toolchain, Cargo graph, and backend are pinned. Distribution
packages resolve during each build; byte-for-byte container reproduction is not
claimed. Building and running the image currently requires `linux/amd64`.

The default listener is `0.0.0.0:8000` **inside the container**. Publish it only to
host loopback, as shown. Host/Origin validation remains enabled; there is no login
or public-hosting mode. Untrusted containers must not share its Docker network.
Do not mount the Docker socket, credentials, or sensitive host directories.
Files and terminals belong to the container. A named home volume retains files,
not running shell processes, across container restarts. Back up important files
before replacing or removing volumes. Bind-mounted directories must be writable
by UID 10001.

To change only the host port, use `-p 127.0.0.1:9000:8000` and open port 9000.
To change the server's internal port too, use `-e PORT=9000` with
`-p 127.0.0.1:9000:9000`. No Dockerfile change is required.
Passing a command bypasses automatic backend startup, for example
`docker run --rm --platform linux/amd64 herdr-web:local herdr-web --help`.

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
   or standalone web process. A binary archive uses `herdr-web`; a source checkout
   uses `target/release/herdr-web`. Native plugin Restart uses
   `herdr plugin action invoke local.herdr-web.restart`.
5. Check plugin status and HTTP availability at the reported URL. Test input in
   an isolated terminal. If validation fails, restore the saved web executable
   and repeat the same web-only restart procedure.

Stopping or restarting the web interface preserves backend-owned terminals.
A host reboot does not preserve shell processes or variables. A new backend
requires an explicit plugin Start; no automatic startup hook exists.

## Access and private records

Native startup binds to loopback only. Docker's container-network listener must
be published to host loopback as described above. For remote access, use the documented SSH
forwarding workflow or a separately managed authenticated transport. Public/shared
hosting is unsupported without a separate authentication design.

Keep cloud account details, instance identifiers, credential profiles, host paths,
access commands, and deployment inventories outside this repository. Never store
credentials in project files. Test logs/screenshots are generated under ignored
`test-results/`; review and sanitize evidence before sharing it. There is no
personal deployment configuration in the plugin build or runtime.
