# Event and refresh recipes

## Context-menu coordinates and translation

```js
function contextAnchor(event, target = event.currentTarget) {
  if (Number.isFinite(event.clientX) && Number.isFinite(event.clientY) &&
      (event.clientX !== 0 || event.clientY !== 0)) {
    return { x: event.clientX, y: event.clientY };
  }
  const bounds = target.getBoundingClientRect();
  return { x: bounds.left + Math.min(bounds.width / 2, 24), y: bounds.bottom };
}

function bindRightButton(element, callback) {
  const handler = event => {
    event.preventDefault();
    callback(event, contextAnchor(event, element));
  };
  element.addEventListener("contextmenu", handler);
  return () => element.removeEventListener("contextmenu", handler);
}
```

This is the handler translation, not a complete popup manager. Do not also invoke that callback on right-button `mousedown`. Do not blanket-stop propagation if upstream menus depend on bubbling. Zero coordinates are treated here as keyboard/no useful pointer coordinates; assess that heuristic for the target platform.

Prefer overlay designs that let a new context target receive the event. If a full-screen dismissal layer necessarily intercepts it, resolve the underlying target with the overlay temporarily excluded from hit testing, restore styles in `finally`, close/replace the prior popup, and route the preserved coordinates once. Guard against redispatch recursion. Such internal redispatch is not a trusted OS permission event.

## Nested callback context

```js
function dispatchInContext(event, callback) {
  const previousEvent = activeEvent;
  activeEvent = event;
  try {
    return callback(event);
  } finally {
    activeEvent = previousEvent;
  }
}
```

`activeEvent` belongs to the adapter's enclosing scope. Restore the previous active view similarly. Do not hold either ambient variable across an asynchronous task; pass the event/instance properties explicitly instead.

Failure observed: a native Run handler focused a new terminal, triggering a nested focus event. Clearing the outer event context caused `preventDefault` to miss the original key event; Enter activated an unrelated browser button. Fixing every button separately would conceal the shared adapter bug.

## Coalesced refresh callers must await the drain

```js
function coalescedRefresh(refresh, report) {
  let running = null;
  let pending = false;
  let disposed = false;
  const controller = new AbortController();
  return {
    request() {
      if (disposed) return Promise.resolve();
      pending = true;
      if (running) return running;
      running = Promise.resolve().then(async () => {
        try {
          while (pending && !disposed) {
            pending = false;
            try { await refresh(controller.signal); }
            catch (error) { if (!disposed) report(error); }
          }
        } finally { running = null; }
      });
      return running;
    },
    dispose() { disposed = true; pending = false; controller.abort(); },
  };
}
```

Returning an already-resolved promise while another refresh is running lets callers focus/render against stale state. The shared drain avoids that race. It coalesces bursts rather than guaranteeing quiet under an endless event stream.

Capture a selection revision before fetching. If it changed before the response arrives, discard that response and schedule another refresh without awaiting the same drain from inside itself. Apply selected IDs to a cloned projection, not the raw backend snapshot. Revalidate selection when objects disappear.

## Lifetime and layout traps

- A canceled panel needs both an abort signal and a disposed/generation check after awaits. An ignored abort can otherwise mount a late module into a recycled container.
- Read-only file panels should not start reads while hidden. Close should stop both long polls and fallback timers.
- Floating windows with auto height need intrinsic sizing; a flex shorthand intended for weighted layout blocks can collapse them to a few pixels. Assert measured bounds, not just element existence.
- Preserve menu ancestry and stacking. Merely increasing every popup's z-index does not fix a Start submenu that loses its open state during disposal.
- Route dialogs, application chords and terminal input separately. Yield browser paste shortcuts where native paste events are required; do not disable xterm mouse/bracketed-paste behavior globally.
