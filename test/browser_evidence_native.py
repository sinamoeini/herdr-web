"""Verify browser evidence and empty-state click stability with real events."""

import base64
import hashlib
import json
import pathlib
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from browser_evidence import BrowserEvidence, NON_REPOSITORY
from playwright.sync_api import sync_playwright


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    removed = set()

    def log_message(self, format, *arguments):
        pass

    def do_GET(self):
        if self.path == "/terminal/deleted" and 'deleted' in self.removed:
            self.send_error(400)
            return
        if self.path == "/missing":
            self.send_error(404)
            return
        if self.path.startswith("/terminal/"):
            self.close_connection = True
            if self.path == "/terminal/slow":
                time.sleep(0.3)
            key = self.headers["Sec-WebSocket-Key"]
            digest = hashlib.sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()).digest()
            self.send_response(101)
            self.send_header("Upgrade", "websocket")
            self.send_header("Connection", "Upgrade")
            self.send_header("Sec-WebSocket-Accept", base64.b64encode(digest).decode())
            self.end_headers()
            self.connection.settimeout(3)
            try:
                self.connection.recv(4096)
                if self.path != "/terminal/drop":
                    self.connection.sendall(b"\x88\x02\x03\xe8")
            except OSError:
                pass
            return
        body = b"<!doctype html><html><body>Browser evidence gate fixture</body></html>"
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length", "0")))
        body = json.dumps({"code": 128, "stdout": "", "stderr": NON_REPOSITORY + "\n",
                           "error": "Git exited unsuccessfully; see stderr"}).encode()
        self.send_response(422)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def expect_gate_failure(evidence, category):
    try:
        evidence.assert_clean()
    except AssertionError:
        assert evidence.report()["unexpected"][category], evidence.report()
    else:
        raise AssertionError(f"Browser error gate accepted an unexpected {category} failure")


def empty_session_checks(page):
    source = (pathlib.Path(__file__).resolve().parents[1] / 'public/app.js').read_text()
    renderer = source.split('function renderTerminals(){', 1)[1].split('\nfunction render(){', 1)[0]
    page.evaluate("""renderer => {
        const content = document.createElement('div');
        content.id = 'empty-session-check';
        document.body.append(content);
        globalThis.emptyActions = [];
        globalThis.renderEmpty = new Function('terminalContent', 'desktop', 'model', 'terminalViews', 'dividers', 'run',
            'function renderTerminals(){' + renderer + ';return renderTerminals;')(
                content, {panes: () => []}, {selected: {tab: null}}, new Map(), new Map(),
                action => emptyActions.push(action));
        renderEmpty();
        globalThis.emptyButton = content.querySelector('button');
    }""", renderer)
    button = page.get_by_role('button', name='Create space', exact=True)
    bounds = button.bounding_box()
    page.mouse.move(bounds['x'] + bounds['width'] / 2, bounds['y'] + bounds['height'] / 2)
    page.mouse.down()
    page.evaluate('renderEmpty()')
    page.mouse.up()
    assert page.evaluate('document.querySelector("#empty-session-check button") === emptyButton'), 'Refresh replaced the in-progress click target'
    assert page.evaluate('emptyActions') == ['space.new'], 'Refresh swallowed the real Create space click'
    page.evaluate('document.querySelector("#empty-session-check").remove()')


def run():
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    origin = f"http://127.0.0.1:{server.server_port}"
    try:
        with sync_playwright() as playwright:
            for name in ("chromium", "firefox"):
                browser = getattr(playwright, name).launch()
                try:
                    context = browser.new_context()
                    page = context.new_page()
                    evidence = BrowserEvidence(origin)
                    evidence.attach(page)
                    page.add_init_script("""addEventListener('load', () => setTimeout(() => {
                        console.debug('HERDR_PASSIVE_ACTIVATION:' + JSON.stringify({active: navigator.userActivation.hasBeenActive}));
                    }, 100));""")
                    with page.expect_console_message(lambda message: message.text.startswith('HERDR_PASSIVE_ACTIVATION:')) as activation:
                        page.goto(origin)
                    assert json.loads(activation.value.text.split(':', 1)[1])['active'] is False
                    empty_session_checks(page)
                    page.evaluate("""async () => {
                        await fetch('/api/git', {method: 'POST', headers: {'Content-Type': 'application/json'},
                            body: JSON.stringify({cwd: '/tmp/fixture', args: ['rev-parse', '--show-toplevel']})});
                        await new Promise(resolve => {
                            const socket = new WebSocket(location.origin.replace('http', 'ws') + '/terminal/slow');
                            socket.addEventListener('close', resolve, {once: true});
                            socket.close();
                        });
                    }""")
                    page.wait_for_timeout(500)
                    evidence.assert_clean()
                    assert evidence.http_failures and evidence.socket_events, evidence.report()
                    page.evaluate("globalThis.pendingSocket = new WebSocket(location.origin.replace('http', 'ws') + '/terminal/slow')")
                    page.reload()
                    page.wait_for_timeout(500)
                    evidence.assert_clean()
                    assert any(event['kind'] == 'unload' for event in evidence.socket_events), evidence.report()
                    for navigation in range(3):
                        page.evaluate("""() => {
                            const button = document.createElement('button');
                            button.textContent = 'Probe then reload';
                            button.onclick = async () => {
                                const response = await fetch('/api/git', {method: 'POST', headers: {'Content-Type': 'application/json'},
                                    body: JSON.stringify({cwd: '/tmp/fixture', args: ['rev-parse', '--show-toplevel']})});
                                await response.json();
                                location.reload();
                            };
                            document.body.append(button);
                        }""")
                        with page.expect_navigation():
                            page.get_by_role('button', name='Probe then reload').click()
                        evidence.assert_clean()
                        assert len(evidence.http_failures) == navigation + 2, evidence.report()
                    Handler.removed.add('deleted')
                    with evidence.expect_pane_removal(['deleted'], lambda: 'deleted' in Handler.removed):
                        page.evaluate("""() => new Promise(resolve => {
                            const socket = new WebSocket(location.origin.replace('http', 'ws') + '/terminal/deleted');
                            socket.addEventListener('close', resolve, {once: true});
                        })""")
                    evidence.assert_clean()
                    other = page.context.new_page()
                    other.goto('about:blank')
                    other.goto(origin)
                    evidence.assert_clean()
                    other.evaluate("console.error('unexpected secondary-page error')")
                    expect_gate_failure(evidence, "console")
                    assert any(error['text'] == 'unexpected secondary-page error' for error in evidence.console_errors)
                    other.close()
                    page.evaluate("console.error('unexpected application error')")
                    expect_gate_failure(evidence, "console")
                    page.evaluate("fetch('/missing')")
                    expect_gate_failure(evidence, "http")
                    page.evaluate("setTimeout(() => { throw new Error('unexpected page exception'); }, 0)")
                    page.wait_for_timeout(100)
                    expect_gate_failure(evidence, "page")
                    context.close()

                    context = browser.new_context()
                    page = context.new_page()
                    evidence = BrowserEvidence(origin)
                    evidence.attach(page)
                    page.goto(origin)
                    page.evaluate("dispatchEvent(new Event('beforeunload'))")
                    page.evaluate("""() => new Promise(resolve => {
                        globalThis.socketOpened = false;
                        const socket = new WebSocket(location.origin.replace('http', 'ws') + '/terminal/drop');
                        socket.addEventListener('open', () => {
                            globalThis.socketOpened = true;
                            socket.send('drop after the handshake');
                        }, {once: true});
                        socket.addEventListener('close', resolve, {once: true});
                    })""")
                    page.wait_for_timeout(100)
                    assert page.evaluate('socketOpened'), "Fixture failed to establish the real WebSocket"
                    expect_gate_failure(evidence, "websocket")
                    print(f"{name} {browser.version}: real HTTP/console/WebSocket gate checks passed", flush=True)
                finally:
                    browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


if __name__ == "__main__":
    run()
