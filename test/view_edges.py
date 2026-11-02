#!/usr/bin/env python3
"""Real independent views and parked-preview input, including native focus scope."""

import json
import pathlib
import uuid

from harness import Fixture, wait_for, wait_for_browser
from playwright.sync_api import sync_playwright


GRAPHICS = '''
globalThis.graphicsSeen=Object.create(null);
globalThis.graphicsEvents=[];
globalThis.terminalEvents=[];
const NativeSocket=globalThis.WebSocket;
globalThis.WebSocket=class extends NativeSocket{
  constructor(...args){
    super(...args);
    const path=new URL(args[0],location.href).pathname;
    if(path.startsWith('/graphics/'))this.addEventListener('message',event=>{const scene=JSON.parse(event.data),pane=decodeURIComponent(path.slice(10));graphicsEvents.push({pane,error:scene.error,assets:scene.assets?.length});if(graphicsEvents.length>20)graphicsEvents.shift();if(Array.isArray(scene.assets))graphicsSeen[pane]=true;});
    if(path.startsWith('/terminal/')||path.startsWith('/graphics/')){
      const record=entry=>{terminalEvents.push({path,time:performance.now(),...entry});if(terminalEvents.length>80)terminalEvents.shift();};
      this.addEventListener('open',()=>record({event:'open'}));
      this.addEventListener('error',()=>record({event:'error'}));
      this.addEventListener('close',event=>record({event:'close',code:event.code,reason:event.reason,clean:event.wasClean}));
      this.addEventListener('message',event=>{if(typeof event.data!=='string')return;try{const value=JSON.parse(event.data);if(value.ready||value.error)record({event:'status',ready:value.ready,error:value.error});}catch{}});
    }
  }
};
'''


def main():
    results = {}
    with Fixture('view-edges') as fixture:
        with sync_playwright() as playwright:
            for name in ['chromium', 'firefox']:
                first = fixture.rpc('workspace.create', {'label': 'Preview ' + name, 'focus': False})
                second = fixture.rpc('workspace.create', {'label': 'Independent ' + name, 'focus': False})
                spare = fixture.rpc('tab.create', {'workspace_id': first['workspace']['workspace_id'], 'focus': False})
                terminal = first['root_pane']['terminal_id']
                original_pid = fixture.rpc('pane.process_info', {'pane_id': first['root_pane']['pane_id']})['process_info']['shell_pid']
                assert original_pid
                browser = getattr(playwright, name).launch()
                context = browser.new_context(viewport={'width': 1300, 'height': 900})
                context.add_init_script(GRAPHICS)
                commands = []
                errors = []
                context.on('request', lambda request: commands.append(request.post_data_json) if request.method == 'POST' and request.url.endswith('/api/command') else None)
                page = context.new_page()
                other = context.new_page()
                socket_errors = {'first': [], 'second': []}
                def watch_socket(socket, label):
                    def record(error):
                        socket_errors[label].append({'url': socket.url, 'error': error})
                        del socket_errors[label][:-80]
                    socket.on('socketerror', record)
                page.on('websocket', lambda socket: watch_socket(socket, 'first'))
                other.on('websocket', lambda socket: watch_socket(socket, 'second'))
                page.on('pageerror', lambda error: errors.append(str(error)))
                other.on('pageerror', lambda error: errors.append(str(error)))

                def select(target, space, tab, pane):
                    target.goto(fixture.url)
                    target.locator('[id=' + json.dumps('space-' + space) + ']').click()
                    target.locator('[id=' + json.dumps('tab-' + tab) + ']').click()
                    actual = target.evaluate('async()=>{const {model}=await import("/desktop/runtime/herdr.js");return {selected:model.selected,tabs:model.snapshot.tabs};}')
                    assert actual['selected']['tab'] == tab, {'requested': tab, 'actual': actual}
                    target.wait_for_function('pane=>graphicsSeen[pane]===true', arg=pane,timeout=15000)
                    target.wait_for_selector('.terminal-pane[data-pane=' + json.dumps(pane) + '][data-ready="true"]', timeout=20000)

                def current():
                    return next(pane for pane in fixture.rpc('session.snapshot')['snapshot']['panes'] if pane['terminal_id'] == terminal)

                try:
                    select(page, first['workspace']['workspace_id'], first['tab']['tab_id'], first['root_pane']['pane_id'])
                    default_before = fixture.rpc('session.snapshot')['snapshot'].get('focused_tab_id')
                    select(other, second['workspace']['workspace_id'], second['tab']['tab_id'], second['root_pane']['pane_id'])
                    default_after = fixture.rpc('session.snapshot')['snapshot'].get('focused_tab_id')
                    assert page.evaluate('async()=>(await import("/desktop/runtime/herdr.js")).model.selected.tab') == first['tab']['tab_id']
                    assert other.evaluate('async()=>(await import("/desktop/runtime/herdr.js")).model.selected.tab') == second['tab']['tab_id']
                    assert not any(command['method'] in {'workspace.focus', 'tab.focus', 'pane.focus', 'pane.focus_direction'} for command in commands), commands
                    fixture.post('/api/terminal-action', {'action': 'park', 'terminal': terminal})
                    fixture.post('/api/terminal-action', {'action': 'park', 'terminal': terminal})
                    wait_for_browser(page, 'async id=>(await import("/desktop/runtime/herdr.js")).model.selected.tab===id', arg=spare['tab']['tab_id'])
                    parked = current()
                    assert fixture.rpc('pane.process_info', {'pane_id': parked['pane_id']})['process_info']['shell_pid'] == original_pid
                    page.locator('#parked-button').click()
                    page.evaluate('async terminal=>await(await import("/desktop/runtime/herdr.js")).run("terminal.peek",{terminal})', terminal)
                    preview = '.web-dialog .terminal-pane[data-pane=' + json.dumps(parked['pane_id']) + '][data-ready="true"]'
                    page.wait_for_selector(preview, timeout=20000)
                    page.locator(preview + ' .xterm-helper-textarea').focus()
                    marker = uuid.uuid4().hex[:10]
                    page.keyboard.type("printf 'PREVIEW_%s\\n' '" + marker + "'")
                    page.keyboard.press('Enter')
                    wait_for(lambda: 'PREVIEW_' + marker in fixture.read(parked['pane_id']))
                    assert 'PREVIEW_' + marker not in fixture.read(spare['root_pane']['pane_id'])
                    page.screenshot(path=str(fixture.logs / f'{name}-preview.png'))
                    page.evaluate('async()=>{const api=await import("/desktop/runtime/herdr.js");api.closeOverlay();api.closeOverlay();}')
                    fixture.post('/api/terminal-action', {'action': 'keep', 'terminal': terminal, 'space': first['workspace']['workspace_id']})
                    kept = current()
                    assert fixture.rpc('pane.process_info', {'pane_id': kept['pane_id']})['process_info']['shell_pid'] == original_pid
                    assert fixture.http('/api/terminal-action', {'action': 'kill', 'terminal': terminal})[0] == 400
                    fixture.post('/api/terminal-action', {'action': 'kill', 'terminal': terminal, 'confirmed': True})
                    assert fixture.rpc('pane.process_info', {'pane_id': spare['root_pane']['pane_id']})['process_info']['shell_pid']
                    assert not errors, errors
                    results[name] = {'version': browser.version, 'independent_views_after_native_graphics_ready': True, 'no_public_focus_mutations': True, 'native_default_before': default_before, 'native_default_after': default_after, 'native_default_changed': default_before != default_after, 'park_preview_input_keep_and_confirmed_kill': True, 'preserved_shell_pid': original_pid}
                except Exception:
                    try:
                        snapshot=fixture.rpc('session.snapshot')['snapshot']
                        (fixture.logs/f'{name}-backend-failure.json').write_text(json.dumps({'terminal':terminal,'original_shell_pid':original_pid,'snapshot':snapshot},indent=2))
                        backend_log=pathlib.Path(fixture.socket).with_name('herdr-server.log')
                        if backend_log.is_file():
                            (fixture.logs/f'{name}-backend-server.log').write_bytes(backend_log.read_bytes())
                    except Exception as failure:print('Backend failure capture:',failure,flush=True)
                    page.screenshot(path=str(fixture.logs / f'{name}-failure.png'))
                    for label,target in [('first',page),('second',other)]:
                        try:
                            info=target.evaluate('''async()=>{const {model}=await import('/desktop/runtime/herdr.js');return {selected:model.selected,panes:model.snapshot.panes,seen:graphicsSeen,graphics:graphicsEvents,transport:terminalEvents,views:[...document.querySelectorAll('.terminal-pane')].map(element=>({pane:element.dataset.pane,ready:element.dataset.ready,status:element.querySelector('.terminal-status')?.textContent,rect:element.getBoundingClientRect().toJSON()})),body:document.body.innerText.slice(-2000)};}''')
                            info['socket_errors']=socket_errors[label]
                            (fixture.logs/f'{name}-{label}-failure.json').write_text(json.dumps(info,indent=2))
                            print(label,json.dumps(info),flush=True)
                        except Exception as failure:print('Failure capture:',failure,flush=True)
                    raise
                finally:
                    context.close()
                    browser.close()
                    for workspace in fixture.rpc('session.snapshot')['snapshot']['workspaces']:
                        fixture.rpc('workspace.close', {'workspace_id': workspace['workspace_id']})
        (fixture.logs / 'results.json').write_text(json.dumps(results, indent=2))
        print(json.dumps(results, indent=2), flush=True)


if __name__ == '__main__':
    main()
