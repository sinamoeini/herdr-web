# Native graphics without corrupting text

Use a separate connection for decoded images if the backend supplies one. Preserve CLI/PTY text/input behavior independently: a graphics error must not enter terminal bytes or kill the shell.

## Framing and cancellation

Do not put a partially progressing `read_exact` future directly into a resize-vs-frame `select!` loop. Dropping that read on resize can consume part of a length/header/payload, leaving the next read misaligned. Give frame reads a dedicated task with a bounded queue; send viewport updates independently. A stalled consumer must eventually cancel the entire connection, not restart decoding halfway through a frame.

Inspect exact pinned definitions for field order, enum variants, integer encoding, handshake, socket derivation and codec capabilities. Bound frame size, integer/collection counts, image pixels, raw channel length, decoded scene bytes and outgoing encoded bytes before allocating. Validate PNG dimensions before browser decoding. Keep asset keys opaque and complete, including fingerprints.

## Herdr-specific trap, not a general protocol rule

For original Herdr 0.9.5 at commit `2ecf19a92fc3a7fff75b6ce3655ef04f4625ec01`, relevant source is `src/protocol/{wire,endpoint,surface_reuse}.rs`, `src/server/render_stream.rs`, and `src/server/headless/tests/pane_graphics.rs`.

- Native framing uses a little-endian four-byte length and bincode-2 standard encoding. Check the welcome generation/codecs and backend boot identity rather than guessing from traffic.
- Legacy variant **19**, `PaneSurfacePatch`, contains text-cell updates. It is distinct from optional named surface-delta extensions, and can arrive even when those optional capabilities are disabled. Validate/consume the supported legacy text patch without altering the image scene; reject unknown or unsupported variants visibly.
- Native view sizing includes an extra scrollbar column. Map placements into the target pane's inner rectangle, with bounded viewport-correction attempts.
- A native connection's tab selection can call `set_default_shell_target_from_client` in `src/server/headless/{endpoint_requests,client_views}.rs`. Thus browser views may stay independent while public snapshot default-focus fields change. Do not claim that no public focus RPC implies no backend focus side effect. Do not race to restore global focus or silently upgrade/patch the backend.

## Browser decoder lifetime

Tie each connection to a generation token. Before and after asynchronous image decoding, check that generation/disposal state still matches. Close newly decoded bitmaps that have become stale. Apply the newest complete placements/retained-key set; removal must release obsolete assets. When coalescing scenes, preserve assets referenced by the newest scene even if their bytes arrived in an older pending scene. Account for cell dimensions, crop, offsets, stacking and device pixel ratio; canvases should not intercept terminal input.

Validate with pixels from a real image-emitting terminal program, not merely a canvas element count. Exercise crop, reload, resize, zoom and deletion, and instrument bitmap creation/closure. Malformed native-server fault tests and successful real-image tests are separate evidence categories.
