# SSM transport and promotion primitives

## AWS SSM as an optional transport

Use the user's selected access method. SSM Run Command is useful when the host is managed but the local Session Manager client plugin is absent; an application SSH-forwarding test is a different feature.

- Pass the actual profile, region and instance explicitly. Do not persist one user's values in a reusable script/skill.
- Run build/tests as the intended unprivileged user. Under a root SSM shell, an argument-quoted `runuser -u <user> -- bash -c <script>` prevents accidentally building root-owned artifacts or using the wrong HOME/XDG paths. Construct the remote script with a deliberate PATH/environment, not interpolated arbitrary shell fragments.
- Capture the command ID immediately. Poll that command with a bounded deadline/backoff. On transport timeout or missing output, inspect the existing invocation/process before submitting another mutation.
- Keep full remote logs and per-suite exit codes. API summaries can truncate output; save the SSM JSON and fetch full artifacts when needed. `set -e` can skip diagnostic tails after a failed test; explicitly capture the test status, print/save diagnostics, then return that status.
- For source transfer, prefer existing secure transfer/storage tooling. For small payloads without it, a trusted tar archive can be base64-chunked through a private temporary file. Respect the transport's payload limits, verify the decoded archive SHA-256 before extraction, and use a known destination. Do not extract untrusted archives as root or create world-readable credential/source files.
- Do not confuse `InvalidInstanceId`, expired credentials, inaccessible SSM and a stopped host. Report the actual state and preserve the user's authority over power operations.

## Process identity, not just a PID

Linux primitive used for before/after continuity:

```python
def process_identity(pid):
    text = pathlib.Path(f"/proc/{pid}/stat").read_text()
    fields = text.rsplit(")", 1)[1].split()
    return {"pid": int(pid), "start_ticks": fields[19]}
```

The tail begins at field 3; index 19 is field 22, start time. Also verify the listener's owning executable and supervisor/parent identity. Hash `/proc/PID/exe` when saving the exact running binary; an on-disk path might already have been replaced. Limit environment capture to necessary named launch/context variables rather than dumping secrets.

## Atomic executable replacement

Stage on the destination filesystem, then rename; never truncate the executable at the live path:

```python
def atomic_executable_copy(source, destination):
    descriptor, name = tempfile.mkstemp(
        prefix=".candidate-", dir=destination.parent
    )
    os.close(descriptor)
    temporary = pathlib.Path(name)
    try:
        shutil.copyfile(source, temporary)
        temporary.chmod(0o755)
        with temporary.open("rb") as handle:
            os.fsync(handle.fileno())
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)
```

Prerequisites: `source` is the verified candidate; destination directory is expected, owned and not an attacker-controlled symlink; rollback copies already exist. Adapt executable modes/ownership to the deployment. For power-loss durability, also fsync the parent directory after rename. This primitive makes one path atomic, not a transaction across release and installed copies; record both and restore both on failure.

Replacing bytes does not restart a running process, and proving the path hash is not proving the process uses those bytes. Invoke the existing supervisor/plugin restart, wait for a new process, then verify its running hash and HTTP health. For foreground workflow plugins, keep ownership in the original application rather than detaching a helper daemon.

## Rollback and evidence pitfalls

- Capture backend process identity and terminal-to-shell mappings before restart. After restart, compare them read-only. A host reboot is not a web restart and cannot preserve shell memory.
- Build the replacement before stopping the old service. Never stop an unrelated listener to free the desired port.
- After a Stop acknowledgment, wait for shutdown/lock release rather than repeatedly sending Stop. The lifetime lock must cover full listener drainage.
- Do not run production rollback through a readiness test requiring a newly added route or asset; that can falsely mark a successfully restored old release unhealthy.
- A browser smoke against an isolated backend proves the promoted executable's behavior, not that input was sent through the production listener. State both scopes accurately: production HTTP/process continuity and isolated browser input.
- Preserve old failing test logs, pre-reboot deployment records and rollback metadata. Mark historical PIDs as historical. Refresh the user's open page to load newly embedded JavaScript after promotion.
