# PTY ownership and startup negotiation

## Writer destruction can be an input operation

This Linux/Unix Rust primitive was used with `portable-pty` 0.9.0 to avoid its special writer destructor. Integrate it with the application's error type and ownership model:

```rust
use std::fs::File;
use std::os::fd::{FromRawFd, RawFd};

fn duplicate_master(master: RawFd) -> std::io::Result<File> {
    let descriptor = unsafe { libc::fcntl(master, libc::F_DUPFD_CLOEXEC, 0) };
    if descriptor < 0 {
        return Err(std::io::Error::last_os_error());
    }
    Ok(unsafe { File::from_raw_fd(descriptor) })
}
```

The duplicate has independent descriptor ownership, not an independent open-file description. Changing `O_NONBLOCK` on one duplicate affects the others. Handle partial writes, `WouldBlock`, cancellation and deadlines; a plain `write_all` on a nonblocking descriptor is not a complete writer loop. Dropping this file avoids that library writer's injected bytes, but closing the last PTY master can still affect its child. The child here must be the disposable CLI attachment, not the persistent shell.

## First-exact startup capability filter

At original Herdr 0.9.5 commit `2ecf19a92fc3a7fff75b6ce3655ef04f4625ec01`, inspect `src/client/terminal_setup.rs` before using these bytes:

```rust
const QUERY: &[u8] = b"\x1b[?1016$p\x1b[c";
const REPLY: &[u8] = b"\x1b[?1016;0$y\x1b[?1;2c";
```

That startup path expects an answer within 250 ms. A browser-delayed answer can instead enter the shell. Answer in Rust through the serialized writer; remove only the first exact query from browser-bound output. Report unsupported pixel mouse mode when the bridge only supports cell coordinates.

The reusable algorithm, with a nonempty exact `query`:

```text
if already_answered: emit incoming bytes unchanged
otherwise:
    append incoming bytes to pending
    if pending contains the full query:
        emit prefix + suffix, removing only that first match
        clear pending; set already_answered
        enqueue exactly one reply through the normal writer
    else:
        retain the longest pending suffix that is a proper query prefix
        emit everything before that suffix
on EOF: emit pending unchanged
```

Use byte slices, not strings, since chunk boundaries can split Unicode and escapes. Retain at most `query.len() - 1` candidate bytes after each no-match push. Do not filter input by reply text or globally strip escape sequences.

Regression cases: every query split point; one byte per chunk; all incomplete prefixes at EOF; unrelated escapes; Unicode split inside a character; literal reply text; repeated queries in one chunk and later chunks. Across all chunkings, output must equal input minus exactly one first query and the reply count must be one.

## Prove disposal did not inject input

In a disposable real backend terminal:

```sh
read -r pending_value
printf 'READ_COMPLETED=%s\n' "$pending_value"
```

Send text without Enter, disconnect, reconnect, and verify no completion marker. Then deliberately press Enter and assert the exact queued text arrives. Also compare the shell PID before/after. Make completion output distinct from the echoed command, and test web-process restart separately from browser reload. No shell persistence claim extends to a host reboot.
