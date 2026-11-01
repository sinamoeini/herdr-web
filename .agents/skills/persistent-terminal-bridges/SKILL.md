---
name: persistent-terminal-bridges
description: "Build or debug Rust PTY/WebSocket attachments to persistent terminal backends, especially startup-query corruption, disconnect-induced shell input, bounded native graphics, and plugin-owned bridge lifetimes. Not for replacing the backend with a web-owned shell."
---

# Persistent Terminal Bridges

Establish which process owns the shell before changing transport code. An attachment client is disposable; the backend terminal is not. Resolve pane IDs to terminal IDs from current state rather than assuming they are interchangeable.

## Decisions that prevent data loss

- Inspect the selected PTY library's writer destructor. In the observed `portable-pty` 0.9.0 path, dropping its writer can send newline/EOF. For a persistent shell, use an owned duplicate of the master descriptor and an ordinary file writer instead; preserve descriptor and process lifetimes explicitly.
- Run PTY reading and writing independently. Serialize browser input, terminal negotiation replies, and resize operations through an owned writer path. Bound queues and enqueue/send deadlines, not just individual messages.
- On cancellation, kill and reap only the owned attachment child. Do not call backend terminal-close operations, use takeover flags, or kill by executable name.
- Select the backend once. Sanitize unrelated backend environment variables in children, then supply the exact selected socket/configuration. Plugin-supplied context takes precedence over ambient session selectors.
- Preserve protocol bytes until framing is complete. Decode a newline-delimited JSON response only after its final newline; validate length before allocation and UTF-8 decoding. Use separate ordinary RPC connections when the protocol permits it.

## Read the relevant recipe

- [PTY and startup filter](references/pty-and-startup.md): descriptor duplication, first-exact-query handling, and the regression that detects injected Enter/EOF.
- [Native graphics](references/native-graphics.md): partial-read cancellation, bounded scenes, stale decoding, and Herdr 0.9.5-specific findings. Do not reuse its discriminants with an unverified version.
- [Plugin lifetime](references/plugin-lifetime.md): when the bridge runs as a foreground workflow action with per-backend control/locking.

## Evidence to require

Use isolated backend sessions. Compare actual shell PID and a shell variable across browser reload, detach, web shutdown, and web restart. Separately leave the shell blocked on `read`: disconnect must not complete it. Delay browser input beyond the backend's startup deadline. Test busy ownership without takeover, queue saturation, and permit recovery. A successful compilation, unchanged pane label, or newly created replacement shell proves none of these invariants.

Extracted from Herdr Web: `server/src/{live,terminal_probe,framing,backend,graphics,plugin}.rs`, `public/native-images.js`, and terminal/resource/plugin tests. Version-specific details are confined to the references; choose limits for the current application rather than copying this project's numbers unquestioningly.
