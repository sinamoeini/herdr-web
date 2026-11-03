"""Fail UI acceptance on unexpected HTTP, console, or WebSocket errors."""

import json
import re
import time
from collections import deque
from contextlib import contextmanager
from urllib.parse import unquote, urlsplit

PREFIX = "HERDR_BROWSER_EVIDENCE:"
NON_REPOSITORY = "fatal: not a git repository (or any of the parent directories): .git"
GIT_PROBES = (
    ["--no-optional-locks", "-c", "core.fsmonitor=false", "status", "--porcelain=v2", "--branch"],
    ["rev-parse", "--show-toplevel"],
    ["rev-parse", "--path-format=absolute", "--git-common-dir"],
)
RESOURCE_ERROR = "Failed to load resource: the server responded with a status of 422 (Unprocessable Entity)"
RESOURCE_ERRORS = {RESOURCE_ERROR, RESOURCE_ERROR.replace("Unprocessable Entity", "Unprocessable Content")}
FIREFOX_SOCKET_ERROR = re.compile(
    r'^(?:\[JavaScript Error: ")?(?:Firefox can[’\']t establish a connection to the server at '
    r'|The connection to )(?P<url>wss?://[^\s"<>]+?)(?:\.| was interrupted while the page was loading\.)'
    r'(?:" \{file: "[^"\n]+" line: \d+\}\])?$'
)
REMOVED_PANE_HANDSHAKE = re.compile(
    r"^WebSocket connection to '(?P<url>wss?://[^'\s]+)' failed: Error during WebSocket handshake: Unexpected response code: 400$"
)

SOCKET_OBSERVER = """(() => {
    const NativeWebSocket = globalThis.WebSocket;
    const sockets = new Set();
    const documentId = globalThis.crypto?.randomUUID?.() ?? `${Date.now()}:${Math.random().toString(36).slice(2)}`;
    let unloading = false;
    let sequence = 0;
    const nativeFetch = globalThis.fetch.bind(globalThis);
    globalThis.fetch = async (...args) => {
        const request = new Request(...args);
        if (new URL(request.url).origin !== location.origin) return nativeFetch(...args);
        const requestId = documentId + ':fetch:' + ++sequence;
        const headers = new Headers(request.headers);
        headers.set('X-Herdr-Test-Request', requestId);
        const body = request.headers.get('Content-Type')?.includes('application/json') ? request.clone() : null;
        const response = await nativeFetch(args[0], {...args[1], headers});
        if (response.status >= 400) {
            const failure = {kind: 'http-error', requestId, status: response.status, url: response.url, method: request.method};
            try {
                if (body) failure.request = await body.json();
                failure.response = await response.clone().json();
            } catch (error) {
                failure.capture_error = String(error);
            }
            console.debug('HERDR_BROWSER_EVIDENCE:' + JSON.stringify(failure));
        }
        return response;
    };
    const record = (kind, socket) => console.debug('HERDR_BROWSER_EVIDENCE:' + JSON.stringify({
        kind, documentId, socketId: socket.evidenceId, url: socket.url,
        clientClosed: Boolean(socket.evidenceClientClosed), unloading,
        connecting: socket.readyState === NativeWebSocket.CONNECTING,
    }));
    globalThis.WebSocket = class extends NativeWebSocket {
        constructor(...args) {
            super(...args);
            this.evidenceId = ++sequence;
            sockets.add(this);
            this.addEventListener('error', () => record('socket-error', this));
            this.addEventListener('close', event => {
                if (!event.wasClean) record('unclean-close', this);
                sockets.delete(this);
            });
        }
        close(...args) {
            const result = super.close(...args);
            this.evidenceClientClosed = true;
            record('client-close', this);
            return result;
        }
    };
    addEventListener('beforeunload', event => {
        if (!event.isTrusted) return;
        unloading = true;
        for (const socket of sockets) record('unload', socket);
    });
})();"""


def same_origin(url, origin, websocket=False):
    parsed, expected = urlsplit(url), urlsplit(origin)
    scheme = {"http": "ws", "https": "wss"}.get(expected.scheme) if websocket else expected.scheme
    return parsed.scheme == scheme and parsed.netloc == expected.netloc


def expected_git_probe(failure, origin):
    response, request = failure.get("response", {}), failure.get("request", {})
    return (
        same_origin(failure["url"], origin)
        and urlsplit(failure["url"]).path == "/api/git"
        and failure["status"] == 422 and failure["method"] == "POST"
        and isinstance(request, dict) and request.get("args") in GIT_PROBES
        and isinstance(request.get("cwd"), str) and bool(request["cwd"])
        and isinstance(response, dict) and response.get("code") == 128
        and isinstance(response.get("stderr"), str) and response["stderr"].strip() == NON_REPOSITORY
        and response.get("stdout") == ""
        and response.get("error") == "Git exited unsuccessfully; see stderr"
        and "capture_error" not in failure
    )


class BrowserEvidence:
    def __init__(self, origin):
        self.origin = origin
        self.http_failures = []
        self.console_errors = []
        self.socket_events = []
        self.page_errors = []
        self.pending_responses = deque()
        self.pages = set()
        self.fetch_failures = {}
        self.removals = []

    @contextmanager
    def expect_pane_removal(self, panes, confirm):
        removal = {"panes": sorted(panes), "start": time.monotonic()}
        yield
        assert confirm(), "Backend did not confirm the expected pane removal"
        removal["confirmed"] = time.monotonic()
        self.removals.append(removal)

    def expected_removal(self, url, timestamp):
        if not same_origin(url, self.origin, websocket=True):
            return False
        parts = urlsplit(url).path.split("/", 2)
        if len(parts) != 3 or parts[1] not in {"terminal", "graphics"}:
            return False
        pane = unquote(parts[2])
        return any(pane in removal["panes"] and removal["start"] <= timestamp <= removal["confirmed"] + 2
                   for removal in self.removals)

    def attach(self, page):
        context = page.context
        context.add_init_script(SOCKET_OBSERVER)
        context.on("response", self.response)
        context.on("requestfinished", self.finished)
        context.on("page", self.attach_page)
        for existing in context.pages:
            self.attach_page(existing)

    def attach_page(self, page):
        if page in self.pages:
            return
        self.pages.add(page)
        page.on("console", self.console)
        page.on("pageerror", lambda error: self.page_errors.append(str(error)))

    def response(self, response):
        if response.status < 400:
            return
        failure = {"status": response.status, "url": response.url, "method": response.request.method}
        self.http_failures.append(failure)
        self.pending_responses.append((response, failure))

    def capture_responses(self):
        while self.pending_responses:
            response, failure = self.pending_responses.popleft()
            self.capture_response(response, failure)

    def finished(self, request):
        for response, failure in tuple(self.pending_responses):
            if response.request == request:
                self.pending_responses.remove((response, failure))
                self.capture_response(response, failure)
                break

    def capture_response(self, response, failure):
        try:
            request_id = response.request.headers.get("x-herdr-test-request")
            if request_id:
                failure["request_id"] = request_id
            if "application/json" in response.request.headers.get("content-type", ""):
                failure["request"] = response.request.post_data_json
            failure["response"] = response.json()
        except Exception as error:
            failure["capture_error"] = str(error)

    def console(self, message):
        if message.type == "debug" and message.text.startswith(PREFIX):
            event = json.loads(message.text[len(PREFIX):])
            event["time"] = time.monotonic()
            if event["kind"] == "http-error":
                self.fetch_failures[event["requestId"]] = event
            else:
                self.socket_events.append(event)
        elif message.type == "error":
            self.console_errors.append({"text": message.text, "location": message.location, "time": time.monotonic()})

    def expected_console(self, error):
        url = error["location"].get("url")
        if error["text"] in RESOURCE_ERRORS:
            matches = [failure for failure in self.http_failures if failure["url"] == url]
            return bool(matches) and all(expected_git_probe(failure, self.origin) for failure in matches)
        removed = REMOVED_PANE_HANDSHAKE.fullmatch(error["text"])
        if removed:
            return self.expected_removal(removed["url"], error["time"])
        match = FIREFOX_SOCKET_ERROR.fullmatch(error["text"])
        if not match or not same_origin(match["url"], self.origin, websocket=True):
            return False
        path = urlsplit(match["url"]).path
        if path != "/api/events" and not path.startswith(("/terminal/", "/graphics/")):
            return False
        if self.expected_removal(match["url"], error["time"]):
            return True
        related = [event for event in self.socket_events
                   if event["url"] == match["url"] and abs(event["time"] - error["time"]) <= 2]
        failures = [event for event in related if event["kind"] == "socket-error"]
        if failures:
            closest = min(failures, key=lambda event: abs(event["time"] - error["time"]))
            return closest["clientClosed"] or closest["unloading"]
        return any((event["clientClosed"] or event["unloading"])
                   and abs(event["time"] - error["time"]) <= .25 for event in related)

    def report(self):
        self.capture_responses()
        for failure in self.http_failures:
            captured = self.fetch_failures.get(failure.get("request_id"))
            if "capture_error" in failure and captured and "capture_error" not in captured:
                if all(failure[key] == captured[key] for key in ("url", "status", "method")) and failure.get("request") == captured.get("request"):
                    failure["body_capture_error"] = failure.pop("capture_error")
                    failure["response"] = captured["response"]
                    failure["body_source"] = "browser-response-clone"
        unexpected = {
            "http": [failure for failure in [*self.http_failures, *self.fetch_failures.values()]
                     if not expected_git_probe(failure, self.origin)],
            "console": [error for error in self.console_errors if not self.expected_console(error)],
            "websocket": [event for event in self.socket_events
                          if event["kind"] in {"socket-error", "unclean-close"}
                          and not event["clientClosed"] and not event["unloading"]
                          and not self.expected_removal(event["url"], event["time"])],
            "page": self.page_errors,
        }
        return {"http_failures": self.http_failures, "console_errors": self.console_errors,
                "fetch_failures": list(self.fetch_failures.values()),
                "confirmed_pane_removals": self.removals,
                "socket_events": self.socket_events, "page_errors": self.page_errors, "unexpected": unexpected}

    def assert_clean(self):
        unexpected = self.report()["unexpected"]
        assert not any(unexpected.values()), json.dumps(unexpected, indent=2)
