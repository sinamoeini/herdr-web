#!/usr/bin/env python3
"""Share real Herdr terminals across independent Chromium and Firefox contexts."""

import concurrent.futures
import json
import os
import subprocess

from harness import BINARY, Fixture, wait_for
from playwright.sync_api import expect, sync_playwright
from resources import children
from terminal_selection import INIT, SelectionFixture
from websocket_client import WebSocket


def connect(fixture, pane):
    socket = WebSocket(fixture.url, "/terminal/" + pane)
    assert socket.status == 101
    socket.ready()
    return socket


def raw_connections():
    with Fixture("shared-terminal-lifecycle") as fixture:
        created = fixture.rpc("workspace.create", {"label": "Shared attachment", "focus": False})
        pane = created["root_pane"]["pane_id"]
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            first, second = list(pool.map(lambda _: connect(fixture, pane), range(2)))
        try:
            pids = children(fixture.web.pid)
            assert len(pids) == 1, pids
            first.send({"type": "input", "data": "printf 'FIRST_%s\\n' WINDOW\r"})
            wait_for(lambda: "FIRST_WINDOW" in fixture.read(pane))
            second.send({"type": "input", "data": "printf 'SECOND_%s\\n' WINDOW\r"})
            wait_for(lambda: "SECOND_WINDOW" in fixture.read(pane))
            first.close()
            assert children(fixture.web.pid) == pids
            second.send({"type": "input", "data": "printf 'SURVIVING_%s\\n' WINDOW\r"})
            wait_for(lambda: "SURVIVING_WINDOW" in fixture.read(pane))
            # A malformed sibling must not terminate the shared attachment.
            invalid = connect(fixture, pane)
            invalid.send({"type": "resize", "cols": 0, "rows": 24})
            while True:
                opcode, payload = invalid.receive()
                if opcode == 1 and json.loads(payload).get("error"):
                    break
                assert opcode != 8, payload
            invalid.close()
            assert children(fixture.web.pid) == pids
            # Retirement and immediate reattachment must serialize child cleanup.
            second.close()
            for _ in range(8):
                replacement = connect(fixture, pane)
                assert len(children(fixture.web.pid)) == 1
                replacement.close()
            wait_for(lambda: not children(fixture.web.pid))
            assert fixture.rpc("pane.get", {"pane_id": pane})["pane"]["terminal_id"] == created["root_pane"]["terminal_id"]
        finally:
            first.close()
            second.close()
    return {"concurrent_join_one_child": True, "both_clients_send_input": True,
            "disconnect_and_invalid_peer_isolation": True, "rapid_reconnect": True}


def browsers(fixture, browser):
    created = fixture.rpc("workspace.create", {"label": "Two browsers", "cwd": str(fixture.root), "focus": False})
    pane = created["root_pane"]["pane_id"]
    selected = {"space": created["workspace"]["workspace_id"], "tab": created["tab"]["tab_id"], "pane": pane}
    contexts, errors = [], []

    def open_view(width, height, url=None, ready=True, hold_ready=False):
        context = browser.new_context(viewport={"width": width, "height": height})
        contexts.append(context)
        if hold_ready:
            context.add_init_script(INIT.replace("selectionTerminals.push(term);", """
              const write=term.write.bind(term);
              term.write=(data,callback)=>{
                if(data===''&&callback)globalThis.heldReady=callback;
                else write(data,callback);
              };
              selectionTerminals.push(term);
            """))
            context.add_init_script("""
              const NativeWebSocket=globalThis.WebSocket;
              globalThis.WebSocket=class extends NativeWebSocket{
                constructor(url,...args){
                  super(url,...args);
                  if(new URL(url).pathname.startsWith('/terminal/'))globalThis.readySocket=this;
                }
              };
            """)
        else:
            context.add_init_script(INIT)
        context.add_init_script("sessionStorage.setItem('herdr-web.selection.v1'," + json.dumps(json.dumps(selected)) + ");")
        page = context.new_page()
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(url or fixture.url)
        if ready:
            page.wait_for_selector('[data-ready="true"] .xterm-helper-textarea', state="attached")
        return page

    def contains(page, text):
        page.wait_for_function("text => document.querySelector('.xterm-rows').innerText.includes(text)", arg=text)

    def command(page, text):
        page.locator(".xterm-helper-textarea").focus()
        page.keyboard.type(text)
        page.keyboard.press("Enter")

    def grid(page):
        return page.evaluate("()=>({cols:selectionTerminals[0].cols,rows:selectionTerminals[0].rows})")

    try:
        first = open_view(1400, 900)
        command(first, "printf '\\033[31mIDLE_BEFORE_JOIN_猫\\033[0m\\n'")
        colored_row = """() => {
          const buffer=selectionTerminals[0].buffer.active;
          for(let i=0;i<buffer.length;i++){
            const row=buffer.getLine(i);
            if(row.translateToString(true).trimEnd()==='IDLE_BEFORE_JOIN_猫')
              return {color:row.getCell(0).getFgColor(),mode:row.getCell(0).getFgColorMode()};
          }
          return null;
        }"""
        first.wait_for_function(colored_row)
        color = first.evaluate(colored_row)
        pids = children(fixture.web.pid)
        assert len(pids) == 1
        second = open_view(1400, 900)
        second.wait_for_function(colored_row)
        assert second.evaluate(colored_row) == color
        assert children(fixture.web.pid) == pids
        assert grid(first) == grid(second)
        command(second, "printf 'BOTH_%s\\n' WINDOWS")
        contains(first, "BOTH_WINDOWS")
        contains(second, "BOTH_WINDOWS")
        second.set_viewport_size({"width": 900, "height": 600})
        wait_for(lambda: grid(first) == grid(second) and grid(first)["cols"] < 100)
        small = grid(second)
        # Polling and refitting the wider window must not resize the shared grid.
        first.set_viewport_size({"width": 1450, "height": 950})
        first.wait_for_timeout(400)
        assert grid(first) == small == grid(second)
        # Verify application mouse modes and alternate-screen content on a late join.
        program = fixture.root / "shared-mouse.py"
        program.write_text(r"""
import os,sys,termios,tty
old=termios.tcgetattr(0)
try:
    tty.setraw(0)
    sys.stdout.write('\x1b[?1049h\x1b[?1002h\x1b[?1006h\x1b[2J\x1b[HALTERNATE_猫_READY')
    sys.stdout.flush()
    while os.read(0,1)!=b'q':pass
finally:
    sys.stdout.write('\x1b[?1002l\x1b[?1006l\x1b[?1049l')
    sys.stdout.flush()
    termios.tcsetattr(0,termios.TCSANOW,old)
""")
        fixture.post("/api/terminal-launch", {"pane": pane, "argv": ["python3", str(program)]})
        contains(first, "ALTERNATE_猫_READY")
        third = open_view(1100, 750)
        contains(third, "ALTERNATE_猫_READY")
        third.wait_for_function("() => selectionTerminals[0].modes.mouseTrackingMode !== 'none'")
        third.locator(".xterm-helper-textarea").focus()
        third.keyboard.type("q")
        wait_for(lambda: fixture.rpc("pane.process_info", {"pane_id": pane})["process_info"]["foreground_process_group_id"] ==
                 fixture.rpc("pane.process_info", {"pane_id": pane})["process_info"]["shell_pid"])
        contexts[1].close()
        contexts[2].close()
        wait_for(lambda: grid(first)["cols"] > small["cols"])
        command(first, "printf 'CLOSED_PEERS_%s\\n' PRESERVED")
        contains(first, "CLOSED_PEERS_PRESERVED")
        assert children(fixture.web.pid) == pids
        # A separate server must still respect the existing owner, present one
        # stable error, and reconnect only after the user clicks Retry.
        from browser import wait_url
        peer = fixture.process([str(BINARY)], "busy-peer.log", stdout=subprocess.PIPE)
        peer_url = wait_url(peer)
        busy = open_view(1400, 900, url=peer_url, ready=False)
        try:
            status = busy.locator(".terminal-pane .terminal-status")
            expect(status).to_contain_text("busy")
            # Count terminal connection attempts on a fresh load.
            busy.add_init_script("""
              globalThis.terminalAttempts=0;
              const NativeWebSocket=globalThis.WebSocket;
              globalThis.WebSocket=class extends NativeWebSocket{
                constructor(url,...args){
                  super(url,...args);
                  if(new URL(url).pathname.startsWith('/terminal/'))terminalAttempts++;
                }
              };
            """)
            busy.reload()
            expect(status).to_contain_text("busy")
            busy.wait_for_timeout(3300)
            assert busy.evaluate("terminalAttempts") == 1
            command(first, "printf 'BUSY_PEER_%s\\n' SAFE")
            contains(first, "BUSY_PEER_SAFE")
            contexts[0].close()
            wait_for(lambda: not children(fixture.web.pid))
            status.get_by_role("button", name="Retry", exact=True).click()
            busy.wait_for_selector('[data-ready="true"] .xterm-helper-textarea', state="attached")
            assert busy.evaluate("terminalAttempts") == 2
            command(busy, "printf 'EXPLICIT_RETRY_%s\\n' WORKS")
            contains(busy, "EXPLICIT_RETRY_WORKS")
        finally:
            busy.context.close()
            peer.terminate()
            peer.wait(timeout=20)
        delayed = open_view(1400, 900, ready=False, hold_ready=True)
        delayed.wait_for_function("() => typeof heldReady === 'function'")
        delayed.evaluate("() => readySocket.close(1000)")
        expect(delayed.locator(".terminal-status")).to_be_visible()
        delayed.evaluate("() => heldReady()")
        expect(delayed.locator(".terminal-pane")).to_have_attribute("data-ready", "false")
        expect(delayed.locator(".terminal-status")).to_be_visible()
        delayed.context.close()
        assert not errors, errors
        return {"browser_version": browser.version, "idle_screen_and_unicode": True,
                "shared_live_output_and_input": True, "smallest_viewport_and_expansion": True,
                "late_join_alternate_screen_and_mouse_modes": True, "same_child_after_disconnect": True,
                "external_owner_no_takeover_no_retry_loop": True, "explicit_retry_after_release": True,
                "delayed_parser_callback_cannot_revive_closed_connection": True}
    except BaseException:
        for index, context in enumerate(contexts):
            for page in context.pages:
                page.screenshot(path=str(fixture.logs / f"{browser.browser_type.name}-{index}.png"))
                (fixture.logs / f"{browser.browser_type.name}-{index}.json").write_text(json.dumps(
                    page.evaluate("""()=>selectionTerminals.map(terminal=>({
                      connected:terminal.element?.isConnected,
                      rows:Array.from({length:terminal.buffer.active.length},(_,i)=>terminal.buffer.active.getLine(i).translateToString(true))
                    }))"""), indent=2))
        raise
    finally:
        for context in contexts:
            context.close()
        wait_for(lambda: not children(fixture.web.pid))


def main():
    evidence = {"lifecycle": raw_connections(), "browsers": {}}
    with SelectionFixture("shared-terminal") as fixture, sync_playwright() as playwright:
        evidence["backend_version"] = subprocess.check_output([str(fixture.herdr), "--version"], text=True).strip()
        for name in ["chromium", "firefox"]:
            browser = getattr(playwright, name).launch(
                executable_path=os.environ.get(f"HERDR_TEST_{name.upper()}_EXECUTABLE"))
            try:
                evidence["browsers"][name] = browsers(fixture, browser)
            finally:
                browser.close()
        (fixture.logs / "results.json").write_text(json.dumps(evidence, indent=2) + "\n")
        print(json.dumps(evidence, indent=2), flush=True)


if __name__ == "__main__":
    main()
