# Native graphics and backend focus

Herdr Web keeps each browser selection local.
Original Herdr 0.9.5 changes its default focus when a native graphics connection selects another tab.
This backend limit remains unresolved.
Issue #6 records the investigation and the required upstream change.

## Pinned compatibility

This decision applies to original Herdr 0.9.5, commit `2ecf19a92fc3a7fff75b6ce3655ef04f4625ec01`.
The public protocol is 22.
The native endpoint generation is 1.
Herdr Web does not patch the backend, migrate to herdr-core, or send compensating focus requests.

A native endpoint is a backend connection for rendered terminal data.
Herdr Web uses that connection to receive decoded images and their placements.
The public terminal attachment supplies text and input.
Both paths refer to an explicit pane.

## Source findings

These paths refer to the pinned original Herdr source.

| Source | Finding |
| --- | --- |
| `src/protocol/endpoint.rs` | The hello message accepts geometry and rendering capabilities. It has no initial tab or pane selector. |
| `src/server/headless.rs`, `ClientShellHello` | A new client starts at the backend default target. |
| `src/server/headless/endpoint_requests.rs` | Supported navigation requests require an active surface and enter the client request handler. |
| `src/server/headless/client_views.rs`, `handle_client_shell_api_request` | Navigation changes the client location, then calls `set_default_shell_target_from_client`. |
| `src/server/headless/client_views.rs`, `set_default_shell_target_from_client` | This helper changes the shared workspace and tab selection. |
| `src/server/headless.rs`, `ClientShellPaneInput` | Targeted input requires an already visible pane. An empty input batch cannot select another view. |
| `src/server/headless/surface_interest.rs` | An inactive surface stops image delivery. It does not supply an independent selection path. |
| `src/protocol/wire.rs`, `ObserveTerminal` | The observer accepts a terminal target without selecting the default tab. |
| `src/server/headless/render.rs`, `TerminalObserve` | The observer renders text frames. It does not produce the semantic image assets from `ClientShell` rendering. |
| `src/api/schema/panes.rs`, `PaneReadResult` | Public reads return text and revision information. They do not expose image assets or placements. |

Generation 1 accepts unknown optional controls but ignores unsupported controls.
Inventing a new control in Herdr Web alone cannot change the backend behavior.
Restoring global focus after navigation creates a race with other clients.
It also routes public focus changes to other native views.

## Required upstream change

The backend needs an explicit operation that selects a rendering target for one client without updating default focus.
The backend must advertise support before Herdr Web uses that operation.
It must return success or an explicit error for a missing target.

The operation must retain a valid client target across resize and state updates.
Reconnect must select the requested target through the same operation.
Client disposal must leave default focus unchanged.
Rendering must retain decoded image data, placements, and existing resource limits.
The backend must define how geometry ownership works when several clients view different tabs.

After upstream supplies that operation, add coverage against the supporting backend version.
Keep generation 1 compatibility explicit.
Do not claim default-focus preservation for 0.9.5.

## Current acceptance

`test/view_edges.py` drives actual space and tab controls in Chromium and Firefox.
It waits for the requested pane and native graphics before reading backend focus.
It asserts that each new view changes default focus to its selected tab on 0.9.5.
It then exercises resize, reconnect, and disposal of the second view.
The first view retains its selection and real inline image pixels throughout those steps.
The test rejects public focus requests.

These assertions describe the current limit.
They do not count as a fix for shared default focus.
`test/graphics_browser.py` separately covers image cropping, resizing, reloading, deletion, and bitmap cleanup.
