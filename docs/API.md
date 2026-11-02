# Local browser API

These endpoints run with the local user's privileges, not as a public service. Every POST and WebSocket upgrade must send `Origin: http://HOST` exactly matching its accepted Host authority. JSON requests require `Content-Type: application/json`. Ordinary bodies cap at 16 KiB; only clipboard image uploads allow 10 MiB. Validation/backend failures use a non-success status and `{ "error": "message" }`; Git operation failures also retain code/stdout/stderr.

IDs are opaque and distinct. Never substitute a terminal ID for a pane ID, or a workspace ID for a tab ID. Browser callers explicitly refresh state after mutations; ordinary local selection need not mutate backend focus.

## HTTP routes

| Route | Request | Successful response |
| --- | --- | --- |
| `GET /`, known assets | Embedded asset path | File with content type; unknown/source paths 404 |
| `GET /api/state?tab=ID` | Optional existing tab ID | `{snapshot,layout,home}`; raw Herdr snapshot and exported layout, null layout if no tabs |
| `POST /api/command` | `{method,params}` from table below | Original Herdr operation's `result`, without JSON-RPC wrapper |
| `POST /api/watch` | `{path}` | `{changed:false}` after about 17 seconds, `{changed:true}`, or `{changed:true,missing:true}` |
| `POST /api/files` | `{action:"read",path}` or `{action:"list",path}` | `{text}` or `{entries:[{name,dir}]}` |
| `POST /api/git` | `{cwd,args}` | `{code,stdout,stderr}`; see `docs/GIT.md` |
| `GET /api/agents` | None | Bare array of installed supported program names, e.g. `["codex"]` |
| `POST /api/agent-start` | `{pane,program}` | `{launched:true}` for an installed catalog entry |
| `POST /api/terminal-launch` | `{pane,argv}` | `{launched:true}`; empty argv returns `{launched:false}` |
| `POST /api/child-terminal` | `{parent,name?,argv}` | `{terminal,pane,parent}`; parent is a terminal ID |
| `POST /api/terminal-action` | `{action,terminal,space?,confirmed?}` | Original `pane.move`/`pane.close` result; repeated move into current workspace gives `{terminal,pane,unchanged:true}` |
| `POST /api/clipboard-image` | Raw image bytes and matching image MIME | `{path}`; absolute private server-side path, not browser URL |
| `GET /api/mods` | None | Bare array of registered trusted panel names, e.g. `["notes"]` |
| `GET /user-mods/NAME/PATH` | Approved contained custom asset | JS/JSON/CSS/SVG/PNG bytes, at most 2 MiB |
| `GET /api/machines` | None | Bare array `[{host,port,local_port,url,pid}]` of active owned forwards |
| `POST /api/machines` | `{action:"connect",host,port?}` | `{host,port,local_port,url,pid}`, only after forwarding readiness; port defaults to 8004 |
| `POST /api/machines` | `{action:"disconnect",host,port?}` | `{disconnected:true}` or `{disconnected:false}` |

`/api/state` falls back to an existing focused/first tab when the requested tab was removed. Reading another tab's layout does not focus it globally. File paths/cwd must be bounded absolute paths. Terminal actions are `park`, `keep`, or `kill`; only intentional kill with `confirmed:true` is accepted. Program names must be installed catalog entries. Explicit launch arguments are quoted individually, not executed as a server-side script.

## Allowlisted commands

Only the following method/parameter names are accepted; unknown fields, invalid types, and missing required IDs fail before backend mutation:

| Method | Parameters |
| --- | --- |
| `workspace.create` | `label?`, `focus?`, `cwd?` |
| `workspace.focus`, `workspace.close` | `workspace_id` |
| `workspace.rename` | `workspace_id`, `label` |
| `workspace.move` | `workspace_id`, `insert_index` |
| `tab.create` | `workspace_id`, `label?`, `focus?`, `cwd?` |
| `tab.focus`, `tab.close` | `tab_id` |
| `tab.rename` | `tab_id`, `label` |
| `tab.move` | `tab_id`, `insert_index` |
| `pane.split` | `target_pane_id`, `direction`, `focus?` |
| `pane.focus`, `pane.close`, `pane.zoom` | `pane_id` |
| `pane.rename` | `pane_id`, `label` |
| `pane.neighbor`, `pane.focus_direction`, `pane.swap` | `pane_id`, `direction` |
| `layout.set_split_ratio` | `tab_id`, `path`, `ratio` |

Strings cap at 512 UTF-8 bytes and reject NUL. `focus` is Boolean. Insertion indices are integers 0–9,999; split paths are at most 32 Booleans and ratios 0.1–0.9. Splits accept right/down; directional operations accept left/up/right/down. This route intentionally has no raw-RPC passthrough.

## WebSocket routes

### `/terminal/PANE_ID`

Resolves the pane to its terminal from a fresh snapshot and starts a temporary CLI attachment. The local capability exchange is answered immediately but does not advertise input readiness: it happens before backend ownership is accepted. Text `{"ready":true}` is sent only after forwarding the first complete terminal ANSI frame. With pinned Herdr 0.9.5, successful frames are bracketed by `CSI ? 2026 h/l`; a busy/rejected attachment emits no accepted frame and must never advertise readiness. Terminal output is **binary**; it is not JSON. Browser input is text JSON:

```json
{"type":"input","data":"echo hello\r"}
{"type":"resize","cols":80,"rows":24}
```

Errors are text `{"error":"message"}` followed by closure with a reason. The browser retries abnormal/busy attachment closures without takeover. Limits: 32 attachments, 1 MiB WebSocket message, 65,536 input bytes, 1–500 columns, 1–200 rows, 8 input/32 output queue entries, 5-second stall. Disposing the attachment must not send newline/EOF or close the persistent terminal.

### `/api/events`

Text messages are `{"type":"heartbeat"}` about every 15 seconds or `{"type":"refresh","event":HERDR_ENVELOPE}` for subscription acknowledgments/events. The event is retained as backend JSON, not flattened into IDs. Errors use `{"error":"message"}` and closure. The browser coalesces refreshes and replaces the subscription when its pane-ID set changes, keeping pane-specific agent status subscriptions current. Events/watch requests share capacity 64.

### `/graphics/PANE_ID`

Browser must send the initial viewport within ten seconds, and sends the same shape on resize:

```json
{"cols":80,"rows":24,"cw":8,"ch":16}
```

Cell widths are 1–100 pixels, heights 1–200; terminal dimensions have the same limits as text attachments. The server negotiates generation-1 full-frame codecs on a separate native connection; it never merges native frames into xterm output. Text JSON scenes have this shape:

```json
{
  "assets":[{"key":"opaque-binary-identity-and-fingerprint","width":8,"height":8,"format":1,"data":"base64"}],
  "placements":[{"key":"opaque-binary-identity-and-fingerprint","id":1,"x":0,"y":0,"cols":1,"rows":1,"sx":0,"sy":0,"sw":8,"sh":8,"ox":0,"oy":0,"z":0}],
  "retained":["opaque-binary-identity-and-fingerprint"]
}
```

Formats 0/1/2 are RGB/RGBA/PNG. Keys are complete opaque identities, not numeric IDs. Placements use terminal-local cells plus crop/pixel offsets/z-order. Each scene replaces placements; assets remain only if retained. Bounds: frame 32 MiB, image 8,388,608 pixels, decoded scene image bytes 16 MiB, pending encoded scene 24 MiB, browser cache 32 MiB per visible terminal, two queued native frames. Errors are separate text `{error}` messages; the ordinary terminal connection can remain usable.

## Verification scope

The native decoder validates and skips legacy tag-19 `PaneSurfacePatch` text-only updates, which Herdr 0.9.5 emits even with optional delta negotiation disabled. Such updates leave graphics unchanged. Named `endpoint.surface-delta.v1`, scrolling, and reuse extensions remain disabled; an unsolicited unsupported extension fails visibly. Frame reads run independently of viewport changes, so resize cannot cancel a partly read frame.

This document describes the implemented contract, not a claim that every route/scenario passed full acceptance. Consult `docs/ACCEPTANCE.md` for scoped tests and remaining limitations.
