# Headed Linux clipboard and audio checks

## Prerequisites and scope

Use Python Playwright with a real headed browser on a private X11 display, for example `xvfb-run -a <venv-python> <test>`. Check prerequisites explicitly; do not silently install system packages or change the user's audio service. The source project used Chromium and Firefox and an owned PulseAudio null sink on a hardware-less Linux host.

A synthetic `ClipboardEvent`, `dispatchEvent`, successful upload POST, or Playwright permission grant does not prove native permission behavior. Automation-generated UI clicks/keys can drive the actual browser path; assert the resulting native events are trusted.

## Clipboard-image recipe

1. Start a disposable backend/session and select its actual terminal pane.
2. Attach a passive event recorder before testing:

   ```js
   const pasteEvidence = [];
   document.addEventListener("paste", event => {
     pasteEvidence.push({
       trusted: event.isTrusted,
       types: [...(event.clipboardData?.types || [])],
     });
   }, true);
   ```

3. Use a real clicked button whose handler invokes `navigator.clipboard.write` with `ClipboardItem` and a valid image Blob. Record `navigator.userActivation.isActive` inside the handler and write success/error. Do not pregrant permissions in this test.
4. On X11, optionally verify the OS clipboard has an owner using `XGetSelectionOwner(display, XInternAtom(display, "CLIPBOARD", False))`. A nonzero owner plus the trusted image paste provides stronger evidence than a JS promise alone.
5. Block the isolated shell on `read`, focus the actual terminal textarea, and press the platform paste shortcut. Await the real upload response and inspect the server file: expected signature/dimensions, private directory/file modes and path ownership.
6. Require a trusted paste event containing image/file data. Verify the shell completion marker is still absent, then deliberately send Enter and check the quoted path. Clean up only files created by this test, after consumers finish.

If xterm consumes Ctrl+V, the native paste event may never arrive. A custom key handler can yield that shortcut to the browser; keep normal terminal input and bracketed paste behavior intact. Do not emulate success by directly calling the upload handler.

## Private audio null sink

Create a private mode-0700 temporary directory. An example PulseAudio configuration, with a socket inside that directory:

```text
load-module module-native-protocol-unix socket=/PRIVATE_TEST_DIR/pulse.sock auth-anonymous=1
load-module module-null-sink sink_name=browser_test
set-default-sink browser_test
```

The uppercase path is an explicitly substituted example, not a runnable default. Anonymous access is appropriate only behind the private directory permissions; do not expose a public socket or TCP listener. Launch an owned foreground process:

```text
pulseaudio --daemonize=no --use-pid-file=no --exit-idle-time=-1 -n --file=/PRIVATE_TEST_DIR/pulse.pa
```

Wait for socket readiness and ensure the process is still alive. Pass `PULSE_SERVER=unix:/PRIVATE_TEST_DIR/pulse.sock` only to test browser processes. Terminate/reap the owned daemon in fixture cleanup. Do not stop a user/system daemon.

Instrument the actual media element's `play()` outcome, `playing`, `ended`, `error`, trusted-event status, and activation state. Before a gesture, test the browser's default denial policy where applicable; after the application's real Play click, require successful playback events without a media-sink error. `play()` resolution alone did not catch Firefox's missing-audio-sink failure in the source project.

The pre-gesture probe must run before automation introduces activation. In the observed Firefox setup, early DOM automation contaminated that measurement; a page init-script recorded the sample before subsequent interaction. Record activation with the sample. Prefer an explicit pre-interaction handshake; an observed quiet delay is a fixture-specific fallback, not a universal sleep rule.

This proves the browser's playback/permission path and decoded audio reaching a null sink. It does not prove physical audibility. Clipboard-denial UX, physical drag/drop, other desktop environments and hardware speakers need separate evidence.
