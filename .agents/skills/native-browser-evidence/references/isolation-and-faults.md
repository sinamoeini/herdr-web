# Isolation, resource faults, and honest evidence

## Protect the user's state

Use unique backend sessions/sockets, ephemeral HTTP ports, private temporary files, disposable Git repositories and owned child records. Keep a real supported shell and HOME when directory reporting depends on shell integration. Do not force `/bin/sh` merely for fixture simplicity if it changes the backend behavior being tested.

Plugin tests may modify a user-wide catalog even with a unique backend session. Isolate all four XDG configuration/state/data/runtime directories plus the backend configuration and plugin files before linking a test plugin.

Tests of an installed executable can run it against an isolated backend without sending input to the production user's terminal. For production restart continuity, read existing terminal IDs, shell PIDs and process start identities before/after; don't inject marker commands into an active user shell.

## Faults that reveal real ownership failures

| Area | Useful fault and oracle |
| --- | --- |
| PTY output | Saturate bounded queues with an injected producer; assert bounded RSS growth, useful close, owned-child reap and permit reuse. Do not call it a real-network soak. |
| JSON RPC | Send bytes one at a time, splitting UTF-8; omit final newline; exceed length; stall mid-response. Assert actual connection closure at timeout and independent concurrent connections. |
| Native frames | Split header/payload and resize mid-read; inject truncated or oversized lengths. Decoder must never resume from the middle as a new frame. |
| Folder watch | Delete watched directory, cancel open panels/HTTP clients, repeat capacity cycles. Compare descriptors/watch counts and permit availability, not only UI refresh. |
| Git child | Produce excessive stdout/stderr separately, exceed runtime, cancel requester, saturate concurrency, then shut down. Verify exact children are reaped and new jobs can acquire capacity. |
| Images | Delay decode across connection replacement; remove placements; count bitmap closes and retained cache bytes. A canvas screenshot does not prove disposal. |
| SSH | Use an isolated local sshd/key/known-host fixture for unknown-key rejection, refusal, input and owned-process cleanup. A loopback success is not an external-host success. |
| Plugins | Concurrent starts, stale control sockets, port occupation, parent/backend loss, disable/unlink/root replacement. The unrelated listener and backend-owned shells must survive where applicable. |

Choose limits from the application's contract. Test just below/at/above boundaries and after cancellation; asserting a constant equals itself provides no meaningful coverage.

## Interpretation traps

- A single SSM/job exit code is not a per-suite result. Save each command's exit status plus full local logs; summaries can be truncated.
- Preserve failed runs and the smallest reproduction when recording a later fix. Avoid overwriting one undated `results.json` and losing which build it tested.
- An actual-controls readiness fix strengthens a UI test; merely removing a backend assertion does not. When a backend's native connection changes default focus, separately assert independent browser selection and record the observed backend side effect.
- Compilation cannot establish visual parity, native permissions, successful remote forwarding or terminal persistence.
- Use generation-specific evidence: candidate hash, live hash/PID and test binary path. A manifest hardcoded to the release path may ignore the candidate override used by other tests.
