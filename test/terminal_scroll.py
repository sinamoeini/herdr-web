#!/usr/bin/env python3
"""Exercise wheel routing through real browsers and the original Herdr backend."""

import json
import os
import re
import subprocess

from harness import wait_for
from playwright.sync_api import sync_playwright
from terminal_selection import INIT, SelectionFixture, drag

PROGRAM = r"""
import os,select,sys,termios,time,tty
mode,path=sys.argv[1:]
old=termios.tcgetattr(0)
try:
    tty.setraw(0)
    open(path,'wb').close()
    sys.stdout.write('\x1b[?1l')
    if mode=='alternate':sys.stdout.write('\x1b[?1049h\x1b[?1007h')
    if mode=='mouse':sys.stdout.write('\x1b[?1002h\x1b[?1006h')
    for i in range(300):sys.stdout.write('SCROLLROW_%03d\r\n'%i)
    sys.stdout.write('SELECT_ALPHA_BRAVO_CHARLIE\r\nSCROLL_READY_%s\r\n'%mode)
    sys.stdout.flush()
    until=time.monotonic()+120
    while time.monotonic()<until:
        if select.select([0],[],[],.1)[0]:
            chunk=os.read(0,4096)
            with open(path,'ab') as log:log.write(chunk)
            if b'\x03' in chunk:break
finally:
    sys.stdout.write('\x1b[?1002l\x1b[?1006l')
    if mode=='alternate':sys.stdout.write('\x1b[?1049l')
    sys.stdout.flush()
    termios.tcsetattr(0,termios.TCSANOW,old)
"""


def exercise(fixture, browser, macos_branch):
    workspace = fixture.rpc(
        "workspace.create",
        {"label": "Wheel routing", "cwd": str(fixture.root), "focus": False},
    )
    pane = workspace["root_pane"]["pane_id"]
    selected = {
        "space": workspace["workspace"]["workspace_id"],
        "tab": workspace["tab"]["tab_id"],
        "pane": pane,
    }
    context = browser.new_context(viewport={"width": 1400, "height": 900})
    context.add_init_script(INIT)
    if macos_branch:
        context.add_init_script(
            "Object.defineProperty(navigator,'platform',{get:()=> 'MacIntel'});"
        )
    context.add_init_script(
        "sessionStorage.setItem('herdr-web.selection.v1',"
        + json.dumps(json.dumps(selected)) + ");"
    )
    page = context.new_page()
    errors, failed_requests = [], []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.on("response", lambda response: failed_requests.append(
        [response.status, response.url]) if response.status >= 400 else None)
    program = fixture.root / "wheel-program.py"
    program.write_text(PROGRAM)

    def offset():
        return fixture.rpc("pane.get", {"pane_id": pane})["pane"]["scroll"]["offset_from_bottom"]

    def top_row():
        rows = re.findall(r"SCROLLROW_(\d{3})", page.locator(".xterm-rows").inner_text())
        assert rows, "No history rows visible"
        return min(map(int, rows))

    def wheel(delta):
        bounds = page.locator(".xterm-screen").first.bounding_box()
        page.mouse.move(bounds["x"] + 100, bounds["y"] + 100)
        page.mouse.wheel(0, delta)

    def launch(mode):
        inputs = fixture.root / f"{pane.replace(':', '-')}-{mode}.input"
        fixture.post("/api/terminal-launch", {
            "pane": pane, "argv": ["python3", str(program), mode, str(inputs)],
        })
        page.wait_for_function(
            "mode => document.querySelector('.xterm-rows').innerText.includes('SCROLL_READY_'+mode)",
            arg=mode,
        )
        wait_for(lambda: inputs.exists())
        page.wait_for_function(
            "mouse => (selectionTerminals[0].modes.mouseTrackingMode !== 'none') === mouse",
            arg=mode == "mouse",
        )
        return inputs

    def stop():
        page.locator(".xterm-helper-textarea").focus()
        page.keyboard.press("Control+c")
        def shell_foreground():
            info = fixture.rpc("pane.process_info", {"pane_id": pane})["process_info"]
            return info["foreground_process_group_id"] == info["shell_pid"]
        wait_for(shell_foreground)

    result = {
        "browser_version": browser.version,
        "platform_branch": "macOS emulated on Linux" if macos_branch else "Linux",
    }
    try:
        page.goto(fixture.url)
        page.wait_for_selector('[data-ready="true"] .xterm-helper-textarea', state="attached")
        inputs = launch("main")
        wait_for(lambda: offset() == 0)
        before = top_row()
        wheel(-120)
        first = wait_for(lambda: offset())
        page.wait_for_function(
            "before => Math.min(...Array.from(document.querySelector('.xterm-rows').innerText.matchAll(/SCROLLROW_(\\d{3})/g), m=>+m[1])) < before",
            arg=before,
        )
        wheel(120)
        wait_for(lambda: offset() == 0)
        wheel(120)
        page.wait_for_timeout(150)
        assert offset() == 0
        assert inputs.read_bytes() == b"", "Wheel leaked arrow keys or raw escapes into the shell"
        result["native_wheel_moves_backend_history_and_visible_rows"] = True

        page.locator(".xterm-helper-textarea").focus()
        page.keyboard.press("PageUp")
        page_offset = wait_for(lambda: offset())
        wheel(-120)
        wait_for(lambda: offset() == page_offset + first)
        wheel(120)
        wait_for(lambda: offset() == page_offset)
        page.keyboard.press("PageDown")
        wait_for(lambda: offset() == 0)
        result["wheel_uses_current_offset_after_page_keys"] = True

        for _ in range(20):
            wheel(-8)
        wait_for(lambda: offset() > 0)
        result["small_native_pixel_deltas_accumulate"] = True
        for mode in [1, 2]:
            old = offset()
            # Explicit DOM line/page modes supplement the native pixel-wheel
            # checks. They do not claim physical trackpad-device coverage.
            page.evaluate("""mode => {
              const screen=document.querySelector('.xterm-screen');
              const bounds=screen.getBoundingClientRect();
              screen.dispatchEvent(new WheelEvent('wheel',{
                bubbles:true,cancelable:true,deltaMode:mode,deltaY:-1,
                clientX:bounds.x+100,clientY:bounds.y+100
              }));
            }""", mode)
            wait_for(lambda: offset() == old + first)
        result["synthetic_line_and_page_modes"] = True
        page.keyboard.press("PageDown")
        wait_for(lambda: offset() == 0)

        page.wait_for_function(
            "() => document.querySelector('.xterm-rows').innerText.includes('SCROLL_READY_main')"
        )
        selected_text = drag(page)
        assert selected_text["text"] == "SELECT_ALPHA_BRAVO", selected_text
        assert inputs.read_bytes() == b"", "Plain selection sent input to the shell"
        result["plain_selection_preserved_after_scrolling"] = True
        stop()

        inputs = launch("mouse")
        wheel(-120)
        wait_for(lambda: b"\x1b[<64;" in inputs.read_bytes())
        wheel(120)
        wait_for(lambda: b"\x1b[<65;" in inputs.read_bytes())
        assert offset() == 0
        result["mouse_application_receives_up_and_down_reports"] = True
        stop()

        inputs = launch("alternate")
        wheel(-120)
        wait_for(lambda: b"\x1b[A" in inputs.read_bytes())
        wheel(120)
        wait_for(lambda: b"\x1b[B" in inputs.read_bytes())
        assert b"\x1b[<" not in inputs.read_bytes()
        assert offset() == 0
        result["non_mouse_alternate_application_receives_arrow_keys"] = True
        # Run native browser zoom gestures last. Firefox changes its coordinate
        # scale, which would invalidate later Playwright drag coordinates.
        before_input = inputs.read_bytes()
        for modifier in ["Control", "Meta"]:
            page.keyboard.down(modifier)
            wheel(-120)
            page.keyboard.up(modifier)
        page.mouse.wheel(120, 0)
        page.wait_for_timeout(150)
        assert offset() == 0 and inputs.read_bytes() == before_input
        result["zoom_modifiers_and_horizontal_wheel_send_no_input"] = True
        stop()
        assert not errors, errors
        assert not failed_requests, failed_requests
        return result
    except BaseException:
        stamp = f"{browser.browser_type.name}-{'macos' if macos_branch else 'linux'}"
        page.screenshot(path=str(fixture.logs / f"{stamp}.png"))
        (fixture.logs / f"{stamp}.json").write_text(json.dumps({
            "completed": result, "page_errors": errors, "failed_requests": failed_requests,
            "body": page.locator("body").inner_text(), "backend_visible": fixture.read(pane),
            "pane": fixture.rpc("pane.get", {"pane_id": pane}),
            "process": fixture.rpc("pane.process_info", {"pane_id": pane}),
        }, indent=2) + "\n")
        raise
    finally:
        context.close()


def main():
    evidence = {
        "scope": "Native Chromium/Firefox on Linux with original Herdr. macOS platform detection is emulated. Line/page delta modes use DOM events. No physical Mac or trackpad claim.",
        "results": {},
    }
    fixture = SelectionFixture("terminal-scroll")
    subprocess.run(["git", "init", "-q", str(fixture.root)], check=True)
    with fixture, sync_playwright() as playwright:
        evidence["backend_version"] = subprocess.check_output(
            [str(fixture.herdr), "--version"], text=True).strip()
        for name in ["chromium", "firefox"]:
            executable = os.environ.get(f"HERDR_TEST_{name.upper()}_EXECUTABLE")
            for macos_branch in [False, True]:
                # Firefox native zoom can survive context closure. Isolate the
                # next platform case in a new browser process and profile.
                browser = getattr(playwright, name).launch(
                    **({"executable_path": executable} if executable else {})
                )
                try:
                    evidence["results"][f"{name}-{'macos' if macos_branch else 'linux'}"] = exercise(
                        fixture, browser, macos_branch
                    )
                finally:
                    browser.close()
    (fixture.logs / "results.json").write_text(json.dumps(evidence, indent=2) + "\n")
    print(json.dumps(evidence, indent=2))


if __name__ == "__main__":
    main()
