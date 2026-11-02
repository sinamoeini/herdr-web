#!/usr/bin/env python3
"""Real-browser foundation regressions; not the full desktop acceptance suite."""

import argparse
import json
import os
import pathlib
import selectors
import signal
import subprocess
import tempfile
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]


def wait_url(process):
    deadline = time.monotonic() + 10
    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ)
    try:
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise RuntimeError(f"Diagnostic server exited: {process.stderr.read()}")
            for key, _ in selector.select(0.2):
                line = key.fileobj.readline().strip()
                if line.startswith("http://127.0.0.1:"):
                    return line
        raise RuntimeError("Server did not report its actual loopback URL within ten seconds")
    finally:
        selector.close()


def adapter_checks(page):
    refresh=page.evaluate('''async()=>{const {coalescedRefresh}=await import("/desktop/runtime/herdr.js");let calls=0,active=0,peak=0;const worker=coalescedRefresh(async()=>{calls++;active++;peak=Math.max(peak,active);await new Promise(resolve=>setTimeout(resolve,30));active--;});const first=worker.request();await new Promise(resolve=>setTimeout(resolve,5));const second=worker.request();await second;const completed=calls;await first;worker.dispose();return {calls,peak,completed};}''')
    assert refresh=={'calls':2,'peak':1,'completed':2},refresh
    page.evaluate("""async () => {
      const { View, div, Element, Popup, MouseButton } = await import('/desktop/runtime/gpui.js');
      document.body.replaceChildren();
      window.testEvents = [];
      window.ghostClicks = 0; window.nestedFocus = 0; window.keyCancelled = false;
      class Controls extends View {
        constructor() { super(); this.root = '/panel-owned-folder'; this.count = 0; this.popup = new Popup(); this.own(() => this.popup.dispose()); }
        render() {
          return div().id('controls').p(20).children(['tab-one', 'space-one', 'tab-two'].map(name =>
            div().id(name).p(12).child(name).on_mouse_down(MouseButton.Right, event => {
              window.testEvents.push({target: name, prevented: event.defaultPrevented, x: event.webAnchor.x, y: event.webAnchor.y});
              this.popup.show(event, [{label: 'Rename', action: () => {
                const dialog = document.createElement('dialog');
                dialog.innerHTML = '<form method="dialog"><label>Name <input id="rename" /></label><button>Cancel</button></form>';
                dialog.dataset.target = name;
                document.body.append(dialog); dialog.showModal(); dialog.querySelector('input').focus();
              }}]);
            })
          )).child(new Element('input').id('preserved-input').attr('value', ''))
            .child(div().id('key-control').attr('tabindex', '0').child('Keyboard control').on_key_down((event,cx) => {
              if(event.key==='enter'){document.querySelector('#focus-button').focus();cx.stop_propagation();window.keyCancelled=event.defaultPrevented;}
            }))
            .child(new Element('button').id('focus-button').attr('type','button').child('Focus target').on('focus',()=>window.nestedFocus++).on_click(()=>window.ghostClicks++))
            .child(div().id('counter').child(String(this.count)));
        }
      }
      window.fixture = new Controls().mount(document.body);
    }""")
    page.locator('#key-control').focus()
    page.keyboard.press('Enter')
    assert page.evaluate('({cancelled:keyCancelled,focus:nestedFocus,clicks:ghostClicks})')=={'cancelled':True,'focus':1,'clicks':0}
    for target in ["tab-one", "space-one", "tab-two"]:
        bounds=page.locator(f"#{target}").bounding_box()
        page.mouse.click(bounds['x']+bounds['width']/2,bounds['y']+bounds['height']/2,button="right")
        assert page.locator(".web-popup").count() == 1
        assert page.evaluate("window.testEvents.at(-1).target") == target
        assert page.evaluate("window.testEvents.at(-1).prevented") is True
    page.get_by_role("menuitem", name="Rename").click()
    assert page.locator("dialog").get_attribute("data-target") == "tab-two"
    page.locator("#rename").fill("typed into dialog")
    assert page.locator("#rename").input_value() == "typed into dialog"
    page.get_by_role("button", name="Cancel").click()
    assert page.locator("dialog[open]").count() == 0
    page.locator("#preserved-input").fill("keep this text")
    page.evaluate("window.savedInput = document.querySelector('#preserved-input'); window.fixture.count++; window.fixture.notify()")
    page.wait_for_function("()=>document.querySelector('#counter').textContent === '1'")
    assert page.evaluate("window.savedInput === document.querySelector('#preserved-input')") is True
    assert page.locator("#preserved-input").input_value() == "keep this text"
    page.locator("#tab-one").dispatch_event("contextmenu", {"button": 2, "clientX": 0, "clientY": 0, "bubbles": True, "cancelable": True})
    assert page.evaluate("window.testEvents.at(-1).y > 0") is True
    assert page.locator(".web-popup").count() == 1
    page.evaluate("window.fixture.dispose()")
    assert page.locator(".web-popup").count() == 0
    assert page.locator("#controls").count() == 0
    assert page.evaluate("window.fixture.root") == '/panel-owned-folder'
    picker_checks(page)


def picker_checks(page):
    page.evaluate("""async () => {
      const { default: Picker } = await import('/desktop/mods/picker/main.js');
      document.body.replaceChildren();
      const container = document.createElement('div');
      Object.assign(container.style, {width: '500px', margin: '60px'});
      document.body.append(container);
      class PickerFixture extends Picker {
        init(properties, cx) {
          super.init(properties, {...cx, spawn() {}});
          this.machines = Array.from({length: 20}, (_, index) => `machine-${index}`);
        }
      }
      window.pickerFixture = new PickerFixture().mount(container);
    }""")
    page.locator('#pk-list').wait_for(state='visible')
    page.locator('input').focus()
    page.keyboard.press('ArrowUp')
    last = page.locator('#pk-space-machine-19')
    last.wait_for(state='visible')
    bounds = last.bounding_box()
    viewport = page.locator('#pk-list').bounding_box()
    assert bounds['y'] >= viewport['y'] - 1, (bounds, viewport)
    assert bounds['y'] + bounds['height'] <= viewport['y'] + viewport['height'] + 1, (bounds, viewport)
    page.keyboard.press('ArrowDown')
    page.wait_for_function('window.pickerFixture.scroll === 0')
    page.mouse.move(viewport['x'] + viewport['width'] / 2, viewport['y'] + viewport['height'] / 2)
    page.mouse.wheel(0, 120)
    page.wait_for_function('window.pickerFixture.scroll > 0')
    previous = page.evaluate('window.pickerFixture.scroll')
    page.mouse.wheel(0, -60)
    page.wait_for_function('(previous) => window.pickerFixture.scroll < previous', arg=previous)
    page.evaluate('window.pickerFixture.dispose()')
    assert page.locator('#pk-list').count() == 0


def http_checks(request, url, directory):
    origin = {"Origin": url, "Content-Type": "application/json"}
    response = request.get(url + "/")
    assert response.status == 200
    assert "text/html" in response.headers["content-type"]
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    for path in ["/missing.js", "/Cargo.toml", "/.git/config", "/server/src/main.rs"]:
        assert request.get(url + path).status == 404
    assert request.get(url, headers={"Host": "evil.example"}).status == 403
    assert request.get(url, headers={"Origin": "https://evil.example"}).status == 403
    assert request.post(url + "/api/files", data="{}", headers={"Content-Type": "application/json"}).status == 403
    assert request.post(url + "/api/files", data="{}", headers={"Origin": url, "Content-Type": "text/plain"}).status == 415
    assert request.post(url + "/api/files", data="x" * 16385, headers=origin).status == 413
    for payload in [
        {"method": "server.stop", "params": {}},
        {"method": "tab.close", "params": {"tab_id": "tab", "force": True}},
        {"method": "pane.split", "params": {"target_pane_id": "pane", "direction": "left"}},
    ]:
        assert request.post(url + "/api/command", data=payload, headers=origin).status == 400
    text = directory / "text.txt"
    text.write_text("猫 <script>must stay text</script>")
    result = request.post(url + "/api/files", data={"action": "read", "path": str(text)}, headers=origin)
    assert result.status == 200 and result.json()["text"] == text.read_text()
    assert request.post(url + "/api/files", data={"action": "read", "path": "/dev/null"}, headers=origin).status == 400
    assert request.get(url + "/api/mods").json() == ["notes"]
    assert request.get(url + "/user-mods/notes/main.js").status == 200
    assert request.get(url + "/user-mods/notes/escape.js").status == 400
    assert request.get(url + "/api/state").status == 503


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--foundation", action="store_true", help="Run only implemented foundation regressions, not desktop acceptance")
    parser.add_argument("--binary", type=pathlib.Path, default=pathlib.Path(os.environ.get('HERDR_WEB_BINARY', ROOT / "target/release/herdr-web")))
    parser.add_argument("--artifacts", type=pathlib.Path, default=ROOT / "test-results/browser")
    arguments = parser.parse_args()
    if not arguments.foundation:
        os.environ['HERDR_WEB_BINARY'] = str(arguments.binary.resolve())
        from integration import main as integration
        integration()
        return
    if not arguments.binary.is_file():
        parser.exit(2, f"Missing built Rust executable: {arguments.binary}\n")
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        parser.exit(2, "Python Playwright is not installed. No browser test ran.\n")
    arguments.artifacts.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="herdr-web-browser-") as temporary:
        directory = pathlib.Path(temporary)
        mods = directory / "mods"
        notes = mods / "notes"
        notes.mkdir(parents=True)
        (notes / "main.js").write_text('import {View, div} from "/desktop/runtime/gpui.js"; export default class Notes extends View { render() { return div().child("notes"); } }')
        (directory / "outside.js").write_text("secret")
        (notes / "escape.js").symlink_to(directory / "outside.js")
        environment = {**os.environ, "PORT": "0", "HERDR_WEB_MODS": str(mods)}
        process = subprocess.Popen([str(arguments.binary.resolve()), "--diagnostics"], env=environment, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            url = wait_url(process)
            with sync_playwright() as playwright:
                for name in ["chromium", "firefox"]:
                    browser = getattr(playwright, name).launch()
                    context = browser.new_context(viewport={"width": 1200, "height": 800})
                    page = context.new_page()
                    errors = []
                    console = []
                    page.on("pageerror", lambda error: errors.append(str(error)))
                    page.on("requestfailed", lambda request: errors.append(f"{request.url}: {request.failure}"))
                    page.on("console", lambda message: console.append({"type": message.type, "text": message.text}))
                    try:
                        http_checks(context.request, url, directory)
                        page.goto(url)
                        page.wait_for_function("()=>document.querySelector('#connection').textContent.includes('No backend')")
                        adapter_checks(page)
                        assert not errors, errors
                    except Exception:
                        page.screenshot(path=str(arguments.artifacts / f"{name}-failure.png"), full_page=True)
                        raise
                    finally:
                        (arguments.artifacts / f"{name}.json").write_text(json.dumps({"browser": name, "version": browser.version, "scope": "foundation only", "errors": errors, "console": console}, indent=2))
                        context.close()
                        browser.close()
            print("Chromium and Firefox foundation checks passed; no Herdr terminal or desktop acceptance was exercised.")
        finally:
            if process.poll() is None:
                process.send_signal(signal.SIGTERM)
            try:
                stdout, stderr = process.communicate(timeout=25)
            except subprocess.TimeoutExpired:
                process.kill()
                stdout, stderr = process.communicate()
            (arguments.artifacts / "server.log").write_text(stdout + stderr)


if __name__ == "__main__":
    main()
