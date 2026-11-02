"""Real browser controls for the frontend regressions tracked in issue #8."""

import json
import shlex
import subprocess

from harness import wait_for, wait_for_browser
from playwright.sync_api import expect


def selected(page):
    return page.evaluate('async()=>(await import("/desktop/runtime/herdr.js")).model.selected')


def terminal(page, pane):
    view = page.locator(f'.terminal-pane[data-pane="{pane}"]')
    expect(view).to_have_attribute('data-ready', 'true', timeout=20000)
    return view


def type_command(page, pane, command):
    terminal(page, pane).locator('.xterm-helper-textarea').focus()
    page.keyboard.type(command)
    page.keyboard.press('Enter')


def choose_space(page, space):
    page.locator(f'[id="space-{space}"]').click()
    wait_for_browser(page,
        'async id=>(await import("/desktop/runtime/herdr.js")).model.selected.space===id', arg=space)


def create_space(page):
    with page.expect_response(lambda response: response.url.endswith('/api/command') and response.request.method == 'POST' and response.request.post_data_json.get('method') == 'workspace.create') as response:
        page.locator('#new-space-button').click()
    assert response.value.ok, response.value.status
    created = response.value.json()['root_pane']
    wait_for_browser(page,
        'async id=>(await import("/desktop/runtime/herdr.js")).model.selected.pane===id',
        arg=created['pane_id'])
    value = selected(page)
    expect(terminal(page, created['pane_id']).locator('.xterm-helper-textarea')).to_be_focused()
    return value


def pane_menu(page, pane):
    # A header stays outside xterm's mouse protocol, including in program mode.
    terminal(page, pane).locator('.pane-header').click(button='right')
    expect(page.locator('.web-popup #menu')).to_be_visible()


def shortcuts_and_dividers(page, fixture, first):
    print('Frontend shortcuts:', first, flush=True)
    pane = first['pane']
    type_command(page, pane, "printf 'HISTORY_%s\\n' SEARCH_MARKER")
    wait_for(lambda: 'HISTORY_SEARCH_MARKER' in fixture.read(pane))
    page.keyboard.press('Control+r')
    page.keyboard.type('SEARCH_MARKER')
    wait_for(lambda: 'reverse-i-search' in fixture.read(pane))
    page.keyboard.press('Control+c')
    before = len(fixture.rpc('session.snapshot')['snapshot']['panes'])
    with page.expect_response(lambda response: response.url.endswith('/api/command') and response.request.method == 'POST' and response.request.post_data_json.get('method') == 'pane.split') as response:
        page.keyboard.press('Control+Shift+O')
    assert response.value.ok, response.value.status
    split_pane = response.value.json()['pane']['pane_id']
    wait_for(lambda: len(fixture.rpc('session.snapshot')['snapshot']['panes']) == before + 1)
    wait_for_browser(page,
        'async id=>(await import("/desktop/runtime/herdr.js")).model.selected.pane===id', arg=split_pane)
    expect(terminal(page, split_pane).locator('.xterm-helper-textarea')).to_be_focused()
    expect(page.locator('.terminal-content .terminal-pane')).to_have_count(2)

    divider = page.get_by_role('separator', name='Resize panes')
    expect(divider).to_have_attribute('aria-orientation', 'vertical')
    page.locator('#new-space-button').focus()
    page.keyboard.press('Shift+Tab')
    expect(divider).to_be_focused()
    page.keyboard.press('ArrowRight')
    wait_for_browser(page,
        'async()=>(await import("/desktop/runtime/herdr.js")).model.backendLayout.root.ratio>.5')
    expect(divider).to_be_focused()
    page.keyboard.press('ArrowRight')
    wait_for_browser(page,
        'async()=>(await import("/desktop/runtime/herdr.js")).model.backendLayout.root.ratio>.59')
    page.keyboard.press('Home')
    expect(divider).to_have_attribute('aria-valuenow', '10')
    page.keyboard.press('End')
    expect(divider).to_have_attribute('aria-valuenow', '90')
    page.reload()
    terminal(page, selected(page)['pane'])
    expect(page.get_by_role('separator', name='Resize panes')).to_have_attribute('aria-valuenow', '90')
    page.get_by_role('separator', name='Resize panes').focus()
    page.keyboard.press('ArrowLeft')
    wait_for_browser(page,
        'async()=>(await import("/desktop/runtime/herdr.js")).model.backendLayout.root.ratio<.9')


def remembered_tabs(page, fixture, first):
    other_tab = fixture.post('/api/command', {
        'method': 'tab.create', 'params': {'workspace_id': first['space'], 'label': 'Remember me', 'focus': False}
    })
    tab = other_tab['tab']['tab_id']
    page.locator(f'[id="tab-{tab}"]').click()
    second = create_space(page)
    choose_space(page, first['space'])
    wait_for_browser(page,
        'async tab=>(await import("/desktop/runtime/herdr.js")).model.selected.tab===tab', arg=tab)
    page.reload()
    page.wait_for_selector(f'[id="tab-{tab}"]')
    assert selected(page)['tab'] == tab

    other = page.context.new_page()
    try:
        other.goto(fixture.url)
        choose_space(other, first['space'])
        other.locator(f'[id="tab-{first["tab"]}"]').click()
        choose_space(page, second['space'])
        choose_space(page, first['space'])
        assert selected(page)['tab'] == tab
        assert selected(other)['tab'] == first['tab']
        fixture.post('/api/command', {'method': 'tab.close', 'params': {'tab_id': tab}})
        expect(page.locator(f'[id="tab-{tab}"]')).to_have_count(0)
        wait_for_browser(page,
            'async tab=>(await import("/desktop/runtime/herdr.js")).model.selected.tab===tab', arg=first['tab'])
        choose_space(page, second['space'])
        choose_space(page, first['space'])
        wait_for_browser(page,
            'async tab=>(await import("/desktop/runtime/herdr.js")).model.selected.tab===tab', arg=first['tab'])
    finally:
        other.close()
    pane = wait_for_browser(page,
        '''async ({space,tab})=>{
            const current=(await import("/desktop/runtime/herdr.js")).model.selected;
            return current.space===space&&current.tab===tab&&current.pane;
        }''', arg=first)
    terminal(page, pane)


def appearance_and_reset(page, fixture):
    page.keyboard.press('Control+,')
    expect(page.locator('#st-ap-Light')).to_be_visible()
    expect(page.locator('#st-t-paper')).to_be_visible()
    page.locator('#st-t-graphite').click()
    page.locator('#st-ap-Dark').click()
    page.wait_for_function('()=>getComputedStyle(document.documentElement).getPropertyValue("--background").trim()==="#17191d"')
    page.locator('#st-ap-Light').click()
    page.wait_for_function('()=>getComputedStyle(document.documentElement).getPropertyValue("--background").trim()==="#ffffff"')
    expect(page.locator('.terminal-content .terminal-viewport').first).to_have_css('background-color', 'rgb(255, 255, 255)')
    page.keyboard.press('Escape')
    page.reload()
    page.wait_for_function('()=>getComputedStyle(document.documentElement).getPropertyValue("--background").trim()==="#ffffff"')
    page.keyboard.press('Control+,')
    page.locator('#st-ap-System').click()
    page.emulate_media(color_scheme='dark')
    page.wait_for_function('()=>getComputedStyle(document.documentElement).getPropertyValue("--background").trim()==="#17191d"')
    page.emulate_media(color_scheme='light')
    page.wait_for_function('()=>getComputedStyle(document.documentElement).getPropertyValue("--background").trim()==="#ffffff"')
    page.locator('#st-t-ink').click()
    page.wait_for_function('()=>getComputedStyle(document.documentElement).getPropertyValue("--background").trim()==="#111720"')
    page.locator('#st-t-paper').click()
    page.wait_for_function('()=>getComputedStyle(document.documentElement).getPropertyValue("--background").trim()==="#ffffff"')
    page.keyboard.press('Escape')
    page.keyboard.press('Control+Shift+L')
    wait_for_browser(page, 'async()=>(await import("/desktop/runtime/herdr.js")).shared("layout")==="terminals"')
    current = selected(page)
    pid = fixture.rpc('pane.process_info', {'pane_id': current['pane']})['process_info']['shell_pid']
    page.keyboard.press('Control+Shift+P')
    page.locator('#palette input').fill('Reset to the default screen')
    page.get_by_text('Reset to the default screen', exact=True).click()
    wait_for_browser(page, 'async()=>(await import("/desktop/runtime/herdr.js")).shared("layout")==="main"')
    assert selected(page)['pane'] == current['pane']
    assert fixture.rpc('pane.process_info', {'pane_id': current['pane']})['process_info']['shell_pid'] == pid
    terminal(page, current['pane'])
    page.reload()
    page.wait_for_selector(f'[id="tab-{current["tab"]}"]')
    assert page.evaluate('async()=>(await import("/desktop/runtime/herdr.js")).shared("layout")') == 'main'


def dialog_focus(page):
    current = selected(page)
    terminal(page, current['pane']).locator('.xterm-helper-textarea').focus()
    page.keyboard.press('F2')
    field = page.locator('#prompt input')
    expect(field).to_be_focused()
    dialog = page.get_by_role('dialog', name='Rename tab')
    expect(dialog).to_have_attribute('aria-modal', 'true')
    for key in ['Shift+Tab', 'Tab', 'Tab', 'Shift+Tab']:
        page.keyboard.press(key)
        assert dialog.evaluate('(element)=>element.contains(document.activeElement)')
    page.keyboard.press('Escape')
    expect(page.locator('#prompt')).to_have_count(0)
    expect(terminal(page, current['pane']).locator('.xterm-helper-textarea')).to_be_focused()


def child_agent(page, fixture, first):
    configuration = fixture.root / 'config.toml'
    previous = configuration.read_text()
    def reload():
        subprocess.run([str(fixture.herdr), '--session', fixture.name, 'server', 'reload-config'],
                       env=fixture.environment, check=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    try:
        configuration.write_text('[terminal]\ndefault_shell = ' + json.dumps(str(fixture.root / 'bin' / 'bash')) + '\nshell_mode = "non_login"\n')
        reload()
        type_command(page, first['pane'], 'export PATH=' + shlex.quote(str(fixture.root / 'bin')) + ':"$PATH"')
        launch_child_agent(page, fixture, first)
    finally:
        configuration.write_text(previous)
        reload()


def launch_child_agent(page, fixture, first):
    pane = first['pane']
    terminal(page, pane).locator('.xterm-helper-textarea').focus()
    type_command(page, pane, "printf 'AGENT_PATH=%s\\n' \"$(command -v codex)\"")
    stub = str(fixture.root / 'bin' / 'codex')
    wait_for(lambda: 'AGENT_PATH=' + stub in fixture.read(pane))
    before = {p['terminal_id'] for p in fixture.rpc('session.snapshot')['snapshot']['panes']}
    pane_menu(page, pane)
    page.get_by_text('New child agent…', exact=True).click()
    expect(page.locator('#new-menu')).to_be_visible()
    expect(page.locator('#new-menu #space')).to_have_count(0)
    with page.expect_response(lambda response: response.url.endswith('/api/child-terminal') and response.request.method == 'POST') as response:
        page.locator('#new-menu #codex').click()
    result = response.value.json()
    assert response.value.status == 200, result
    assert result['parent'] == next(p['terminal_id'] for p in fixture.rpc('session.snapshot')['snapshot']['panes'] if p['pane_id'] == pane)
    assert result['terminal'] not in before
    wait_for(lambda: 'CHILD_AGENT_READY' in fixture.read(result['pane']))
    expect(page.locator('.web-dialog .terminal-pane')).to_have_attribute('data-pane', result['pane'])
    assert selected(page)['tab'] == first['tab']
    type_command(page, result['pane'], "printf 'CHILD_%s\\n' INPUT_OK")
    wait_for(lambda: 'CHILD_INPUT_OK' in fixture.read(result['pane']))
    type_command(page, result['pane'], "IFS= read -r -p \"$(printf 'CHILD_TAB_%s' READY)\" reply; if [[ $reply == $'\\t' ]]; then printf 'CHILD_TAB_%s\\n' RECEIVED; fi")
    wait_for(lambda: 'CHILD_TAB_READY' in fixture.read(result['pane']))
    page.keyboard.press('Tab')
    expect(terminal(page, result['pane']).locator('.xterm-helper-textarea')).to_be_focused()
    page.keyboard.press('Enter')
    wait_for(lambda: 'CHILD_TAB_RECEIVED' in fixture.read(result['pane']))
    page.locator('.web-dialog #close').click()
    expect(page.locator('.web-dialog')).to_have_count(0)
    assert fixture.rpc('pane.process_info', {'pane_id': result['pane']})['process_info']['shell_pid']


def right_click_modes(page, fixture, first):
    pane = first['pane']
    script = fixture.root / 'mouse-reports.py'
    script.write_text(
        "import os, termios, tty\n"
        "old = termios.tcgetattr(0)\n"
        "try:\n"
        " tty.setraw(0)\n"
        " os.write(1, b'\\x1b[?1000h\\x1b[?1006hMOUSE_READY\\r\\n')\n"
        " while True:\n"
        "  data = os.read(0, 256)\n"
        "  if b'q' in data: break\n"
        "  os.write(1, b'MOUSE_REPORT:' + data.hex().encode() + b'\\r\\n')\n"
        "finally:\n"
        " os.write(1, b'\\x1b[?1000l\\x1b[?1006l')\n"
        " termios.tcsetattr(0, termios.TCSADRAIN, old)\n"
        " os.write(1, b'MOUSE_STOPPED\\r\\n')\n"
    )
    type_command(page, pane, 'python3 ' + shlex.quote(str(script)))
    wait_for(lambda: 'MOUSE_READY' in fixture.read(pane))
    viewport = terminal(page, pane).locator('.xterm-screen')
    viewport.click(button='right', position={'x': 30, 'y': 35})
    expect(page.locator('.web-popup #menu')).to_be_visible()
    assert 'MOUSE_REPORT:' not in fixture.read(pane)
    page.get_by_text('Send right-clicks to pane', exact=True).click()
    terminal_id = next(p['terminal_id'] for p in fixture.rpc('session.snapshot')['snapshot']['panes'] if p['pane_id'] == pane)
    wait_for_browser(page,
        'async id=>(await import("/desktop/runtime/herdr.js")).shared("right_click."+id)==="program"', arg=terminal_id)
    viewport.click(button='right', position={'x': 30, 'y': 35})
    wait_for(lambda: 'MOUSE_REPORT:1b5b3c32' in fixture.read(pane))
    expect(page.locator('.web-popup')).to_have_count(0)
    # Shift+right-click is a browser override in Firefox. The header provides
    # a normal context-menu target for restoring menu mode in both browsers.
    pane_menu(page, pane)
    page.get_by_text('Use Herdr right-click menu', exact=True).click()
    wait_for_browser(page,
        'async id=>(await import("/desktop/runtime/herdr.js")).shared("right_click."+id)==="menu"', arg=terminal_id)
    before = fixture.read(pane).count('MOUSE_REPORT:')
    viewport.click(button='right', position={'x': 30, 'y': 35})
    expect(page.locator('.web-popup #menu')).to_be_visible()
    assert fixture.read(pane).count('MOUSE_REPORT:') == before
    page.keyboard.press('Escape')
    terminal(page, pane).locator('.xterm-helper-textarea').focus()
    page.keyboard.type('q')
    wait_for(lambda: 'MOUSE_STOPPED' in fixture.read(pane))
    type_command(page, pane, "printf 'MOUSE_%s\\n' RECOVERED")
    wait_for(lambda: 'MOUSE_RECOVERED' in fixture.read(pane))


def run_frontend_regressions(page, fixture):
    first = create_space(page)
    cases = [
        (shortcuts_and_dividers, (page, fixture, first)),
        (remembered_tabs, (page, fixture, first)),
        (appearance_and_reset, (page, fixture)),
        (dialog_focus, (page,)),
        (child_agent, (page, fixture, first)),
        (right_click_modes, (page, fixture, first)),
    ]
    for case, args in cases:
        print('Frontend case: ' + case.__name__, flush=True)
        case(*args)
    return {
        'shell_history_and_split_shortcuts': True,
        'keyboard_resize_focus_bounds_and_persistence': True,
        'remembered_tabs_reload_independent_views_and_deleted_tab': True,
        'light_dark_system_appearance_and_palette_selection': True,
        'default_screen_reset_preserves_shell': True,
        'dialog_tab_cycle_and_focus_restoration': True,
        'child_agent_menu_backend_launch_preview_and_input': True,
        'right_click_mode_menu_and_real_terminal_mouse_reports': True,
    }
