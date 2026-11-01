---
name: desktop-dom-adapters
description: "Port existing desktop JavaScript panels or fluent native controls to ordinary DOM elements while preserving panel structure, event semantics, focus, and independent browser views. Use for context-menu races and adapter lifecycle bugs, not a generic frontend rewrite."
---

# Desktop DOM Adapters

Inventory the controls the selected upstream panels actually call. Build the shared translation layer around those semantics rather than rewriting each panel independently or adding successful no-op methods. Retain pinned provenance for copied panels; put version-specific adaptations in a reproducible importer.

## High-value invariants

- Translate native right-button down callbacks into browser `contextmenu`, not ordinary `mousedown`. Prevent the handled browser default, preserve upstream propagation behavior, and use coordinates from that actual event. A later browser contextmenu event can otherwise hit an overlay opened by the earlier mousedown.
- Maintain one contextual popup. A right-click on another target must replace it rather than being swallowed by the old overlay. Keep left-button dragging/selection unchanged.
- Save and restore the **previous** active-event/view context in `finally`; do not reset it to null after nested callbacks. A focus call can synchronously emit another event inside a key handler.
- Store asynchronous panel properties on the instance. Ambient `props()` tied to the currently rendering view can address the wrong panel after `await`.
- Avoid adapter field names used by upstream panels. In the source project, native Files/Changes used `root` for a filesystem path; naming the DOM root `webRoot` prevented destructive aliasing.
- Reconcile stable keyed elements; preserve terminal objects, focused inputs, text selections and scroll positions. Dispose listeners, timers, observers and pending work when the owning panel closes.
- Separate window-local selection from shared backend state. Clone snapshots before projection, keep per-window selections in session storage, and avoid public backend-focus mutations for ordinary selection when the architecture supports it. Backend/native side effects still need independent verification.

Read [event and refresh recipes](references/events-and-refresh.md) for the tested translation patterns and race regressions. For file or terminal panels, cancellation must reach the actual long poll/decoder/socket, not merely suppress a render.

Exercise actual controls in Chromium and Firefox: right-click tab, space, another tab, then Rename/Cancel; keyboard context menu; nested focus during key handling; panel hide/disposal; two views selecting different tabs. Wait for visible controls before clicks. Directly invoking adapter commands during startup is a separate API-readiness test, not evidence of real user interaction.

Source: Herdr Web `public/desktop/runtime/{gpui,base,herdr}.js`, `public/app.js`, `tools/vendor.py`, `test/{ui,view_edges,integration}.py`. Do not generalize Herdr-specific palette/layout shapes into requirements for unrelated applications.
