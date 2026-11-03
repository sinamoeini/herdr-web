#!/usr/bin/env python3
"""Test browser selection and program mouse input in disposable terminals."""

import json
import os
import time

from harness import Fixture, wait_for
from playwright.sync_api import sync_playwright

MARKER = "SELECT_ALPHA_BRAVO_CHARLIE"
INIT = """
globalThis.selectionTerminals=[];
let constructor;
Object.defineProperty(globalThis,'Terminal',{
  configurable:true,
  get(){return constructor;},
  set(value){
    constructor=new Proxy(value,{construct(target,args){
      const term=Reflect.construct(target,args);
      selectionTerminals.push(term);
      return term;
    }});
  }
});
"""
PROGRAM = r"""
import os,select,sys,termios,time,tty
old=termios.tcgetattr(0)
try:
    tty.setraw(0)
    sys.stdout.write('\x1b[?1002h\x1b[?1006h\x1b[2J\x1b[HSELECT_ALPHA_BRAVO_CHARLIE\r\nSECOND_ROW_FOR_SELECTION\r\n')
    sys.stdout.flush()
    until=time.monotonic()+60
    while time.monotonic()<until:
        if select.select([0],[],[],.1)[0]:
            chunk=os.read(0,4096)
            with open(sys.argv[1],'ab') as log:log.write(chunk)
            if b'\x03' in chunk:break
finally:
    sys.stdout.write('\x1b[?1002l\x1b[?1006l')
    sys.stdout.flush()
    termios.tcsetattr(0,termios.TCSANOW,old)
"""


class SelectionFixture(Fixture):
    def process(self, argv, name, environment=None, stdout=None):
        if name == "web.log":
            config = self.root / "web-config.toml"
            config.write_text("[ui]\nmouse_capture = false\n")
            environment = {
                **(environment or self.environment),
                "HERDR_CONFIG_PATH": str(config),
            }
        return super().process(argv, name, environment, stdout)


def selection(page):
    return page.evaluate("""() => {
      const terminal=selectionTerminals[0];
      return {text:terminal.getSelection(),
        rectangles:terminal.element.querySelectorAll('.xterm-selection div').length,
        mouse:terminal.modes.mouseTrackingMode};
    }""")


def drag(page, modifier=None):
    page.evaluate("selectionTerminals[0].clearSelection()")
    row = page.locator(".xterm-rows > div").filter(has_text=MARKER).first
    bounds = row.bounding_box()
    screen = page.locator(".xterm-screen").first.bounding_box()
    assert bounds and screen
    cell = screen["width"] / page.evaluate("selectionTerminals[0].cols")
    x, y = screen["x"] + cell * .25, bounds["y"] + bounds["height"] / 2
    if modifier:
        page.keyboard.down(modifier)
    page.mouse.move(x, y)
    page.mouse.down()
    page.mouse.move(x + cell * 18, y, steps=20)
    page.mouse.up()
    if modifier:
        page.keyboard.up(modifier)
    page.wait_for_timeout(500)
    return selection(page)


def exercise(fixture, browser, macos_branch, url=None):
    workspace = fixture.rpc(
        "workspace.create",
        {"label": "Selection", "cwd": str(fixture.root), "focus": False},
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
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    try:
        page.goto(url or fixture.url)
        page.wait_for_selector(
            '[data-ready="true"] .xterm-helper-textarea', state="attached"
        )
        page.wait_for_function(
            "() => selectionTerminals[0].modes.mouseTrackingMode === 'none'"
        )
        page.locator(".xterm-helper-textarea").focus()
        page.keyboard.type(
            "printf '\\033[2J\\033[HSELECT_ALPHA_BRAVO_CHARLIE\\nSECOND_ROW_FOR_SELECTION\\n'"
        )
        page.keyboard.press("Enter")
        page.wait_for_timeout(300)
        plain = drag(page)
        assert plain["text"] == "SELECT_ALPHA_BRAVO" and plain["rectangles"], plain

        program = fixture.root / "selection-mouse.py"
        program.write_text(PROGRAM)
        inputs = fixture.root / f"selection-input-{pane.replace(':', '-')}"
        fixture.post(
            "/api/terminal-launch",
            {"pane": pane, "argv": ["python3", str(program), str(inputs)]},
        )
        page.wait_for_function(
            "() => selectionTerminals[0].modes.mouseTrackingMode !== 'none'"
        )
        ordinary = drag(page)
        assert ordinary["text"] == "", ordinary
        wait_for(lambda: inputs.exists() and b"\x1b[<" in inputs.read_bytes())
        before = inputs.read_bytes()
        forced = drag(page, "Alt" if macos_branch else "Shift")
        assert forced["text"] == "SELECT_ALPHA_BRAVO" and forced["rectangles"], forced
        assert inputs.read_bytes() == before, "Selection sent mouse input to the program"

        page.locator(".xterm-helper-textarea").focus()
        page.keyboard.press("Control+c")
        page.wait_for_function(
            "() => selectionTerminals[0].modes.mouseTrackingMode === 'none'"
        )
        restored = drag(page)
        assert restored["text"] == "SELECT_ALPHA_BRAVO", restored
        assert not errors, errors
        return {
            "browser_version": browser.version,
            "platform_branch": "macOS emulated on Linux" if macos_branch else "Linux",
            "ordinary_shell_drag": plain,
            "program_mouse_input_preserved": True,
            "modifier_selection": forced,
            "selection_sends_no_program_mouse_input": True,
            "ordinary_selection_restored_after_program_exit": True,
            "errors": errors,
        }
    except BaseException:
        stamp = f"{int(time.time())}-{macos_branch}"
        page.screenshot(path=str(fixture.logs / f"selection-{stamp}.png"))
        (fixture.logs / f"selection-{stamp}.json").write_text(
            json.dumps({"errors": errors, "body": page.locator("body").inner_text()},
                       indent=2)
        )
        raise
    finally:
        context.close()


def main():
    evidence = {
        "scope": "Native Chromium and Firefox on Linux. macOS platform detection is emulated. No native macOS or OS clipboard claim.",
        "results": {},
    }
    fixture = SelectionFixture("terminal-selection")
    with fixture, sync_playwright() as playwright:
        for name in ["chromium", "firefox"]:
            executable = os.environ.get(f"HERDR_TEST_{name.upper()}_EXECUTABLE")
            browser = getattr(playwright, name).launch(
                **({"executable_path": executable} if executable else {})
            )
            try:
                for macos_branch in [False, True]:
                    evidence["results"][f"{name}-{'macos' if macos_branch else 'linux'}"] = exercise(
                        fixture, browser, macos_branch
                    )
            finally:
                browser.close()
    (fixture.logs / "results.json").write_text(json.dumps(evidence, indent=2) + "\n")
    print(json.dumps(evidence, indent=2))


if __name__ == "__main__":
    main()
