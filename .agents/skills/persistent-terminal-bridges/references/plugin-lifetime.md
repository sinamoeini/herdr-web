# Foreground plugin service lifetime

Use this when an existing application's workflow action owns a bridge process. Do not introduce a daemon or supervisor if the plugin contract requires a foreground child.

1. Inspect the pinned plugin environment, manifest, runtime and catalog definitions. Use the exact supplied backend socket, executable, root and private state/configuration directories. Ignore conflicting ambient session selectors.
2. Namespace control state by the canonical backend socket identity, not a display name. A stable short hash avoids Unix-domain socket path overflow; do not use randomized language hash functions.
3. Create private state/control files. Acquire the instance lock with no symlink following, ownership checks and nonblocking exclusive locking. Remove stale control sockets only after winning the lock.
4. Hold the lock through complete listener shutdown. Acquiring a lock means the old listener has actually relinquished its port, not merely received a stop request.
5. A concurrent Start returns existing status. Restart sends Stop once; after acknowledgment, wait for lock release. EOF/reset/broken pipe can be normal shutdown progress. Repeated Stop polling can target a replacement or prevent a clean transition.
6. Monitor the original parent identity, not only its PID, and the selected backend's plugin registration. Parent loss, backend loss, disable, unlink or registered-root replacement should initiate owned-service cleanup according to the plugin contract.

On Linux, `/proc/PID/stat` field 22 supplies process start ticks. Parse the tail after the final `)` before indexing; the command name can contain spaces. PID plus start identity guards against PID reuse.

Keep the runtime owner alive while draining HTTP and all attachments, watches, native connections and forwarding children. A port-bind failure must release only owned transient state; do not stop the listener occupying the port.

Lifecycle tests need isolated `XDG_CONFIG_HOME`, `XDG_STATE_HOME`, `XDG_DATA_HOME`, `XDG_RUNTIME_DIR`, backend config and plugin files. A unique backend session alone may not isolate a shared plugin catalog. Cover concurrent Start, Stop/Restart, stale sockets, occupied ports, parent/backend loss, disable/unlink and root changes. Never register test plugins into the user's catalog.

Source: Herdr Web `server/src/plugin.rs`, runtime ownership in `server/src/main.rs`, and `test/plugin.py`. Exact environment names and polling limits are backend-version-specific; these lifecycle invariants are the reusable part.
