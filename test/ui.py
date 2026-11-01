#!/usr/bin/env python3
import json
import os
import pathlib
import time

from harness import BINARY, Fixture, wait_for
from playwright.sync_api import sync_playwright


def main():
    results={}
    with Fixture('ui') as fixture:
        mods=fixture.root/'mods';notes=mods/'notes';notes.mkdir(parents=True)
        (notes/'main.js').write_text('import {View,div} from "/desktop/runtime/gpui.js"; export default class Notes extends View { init(){this.tick=0;this.interval(()=>{this.tick++;this.notify();},100);}render(){return div().id("trusted-notes").child("Trusted notes "+this.tick);} }')
        fixture.web.terminate();fixture.web.wait(timeout=20)
        fixture.environment['HERDR_WEB_MODS']=str(mods)
        from browser import wait_url
        import subprocess
        fixture.web=fixture.process([str(BINARY)],'web.log',stdout=subprocess.PIPE)
        fixture.url=wait_url(fixture.web)
        with sync_playwright() as playwright:
            for name in ['chromium','firefox']:
                browser=getattr(playwright,name).launch()
                context=browser.new_context(viewport={'width':1440,'height':960},accept_downloads=True)
                page=context.new_page()
                errors=[]
                commands=[]
                page.on('request',lambda request:commands.append(request.post_data_json) if request.url.endswith('/api/command') and request.method=='POST' else None)
                page.on('pageerror',lambda error:errors.append(str(error)))
                page.on('console',lambda message:print(name,message.type,message.text,flush=True) if message.type=='error' else None)
                page.add_init_script('globalThis.webPanelErrors=[];globalThis.addEventListener("herdr-error",event=>{const error=event.detail?.stack||String(event.detail);webPanelErrors.push(error);console.error(error);})')
                try:
                    page.goto(fixture.url)
                    page.get_by_role('button',name='Create space',exact=True).wait_for()
                    page.get_by_role('button',name='Create space',exact=True).click()
                    page.wait_for_selector('.xterm-helper-textarea',state='attached')
                    snapshot=wait_for(lambda:fixture.rpc('session.snapshot')['snapshot'] if fixture.rpc('session.snapshot')['snapshot']['panes'] else None)
                    workspace=snapshot['workspaces'][0]['workspace_id'];tab=snapshot['tabs'][0]['tab_id'];pane=snapshot['panes'][0]['pane_id']
                    second=fixture.post('/api/command',{'method':'workspace.create','params':{'label':'Other space','focus':False}})
                    second_tab=fixture.post('/api/command',{'method':'tab.create','params':{'workspace_id':workspace,'label':'Other tab','focus':False}})['tab']['tab_id']
                    page.wait_for_selector(f'[id="tab-{second_tab}"]')
                    original_name=next(value['label'] for value in fixture.rpc('session.snapshot')['snapshot']['tabs'] if value['tab_id']==tab)
                    for target in [f'tab-{tab}',f'space-{second["workspace"]["workspace_id"]}',f'tab-{second_tab}']:
                        bounds=page.locator(f'[id="{target}"]').bounding_box()
                        page.mouse.click(bounds['x']+bounds['width']/2,bounds['y']+bounds['height']/2,button='right')
                        assert page.locator('.web-popup').count()==1
                        assert page.locator('.web-popup #menu').count()==1
                    page.get_by_text('Rename',exact=True).click()
                    page.locator('#prompt input').fill('Rename specific tab')
                    page.get_by_text('Save',exact=True).click()
                    wait_for(lambda:next(value['label'] for value in fixture.rpc('session.snapshot')['snapshot']['tabs'] if value['tab_id']==second_tab)=='Rename specific tab')
                    assert next(value['label'] for value in fixture.rpc('session.snapshot')['snapshot']['tabs'] if value['tab_id']==tab)==original_name
                    target=page.locator(f'[id="tab-{tab}"]')
                    bounds=target.bounding_box()
                    target.dispatch_event('contextmenu',{'button':2,'clientX':0,'clientY':0,'bubbles':True,'cancelable':True})
                    popup=page.locator('.web-popup').bounding_box()
                    assert popup['y']>=bounds['y'] and popup['x']>=bounds['x']-5
                    page.keyboard.press('Escape')
                    target.dblclick()
                    page.locator('#prompt input').fill('Canceled name')
                    page.keyboard.press('Escape')
                    assert next(value['label'] for value in fixture.rpc('session.snapshot')['snapshot']['tabs'] if value['tab_id']==tab)==original_name

                    page.keyboard.press('Control+Shift+P')
                    page.wait_for_selector('#palette input')
                    page.locator('#palette input').fill('theme moss')
                    page.wait_for_timeout(100)
                    page.keyboard.press('Enter')
                    page.wait_for_function('()=>getComputedStyle(document.documentElement).getPropertyValue("--background").trim()==="#151c19"')
                    page.locator('#settings').click()
                    page.wait_for_selector('#st-s-privacy')
                    page.locator('#st-s-privacy').click()
                    page.get_by_text('Telemetry disabled',exact=True).wait_for()
                    assert page.get_by_text('Send feedback',exact=True).count()==0
                    page.keyboard.press('Escape')

                    fixture.post('/api/terminal-launch',{'pane':pane,'argv':['cd',str(fixture.root)]})
                    wait_for(lambda:any(value['pane_id']==pane and value.get('foreground_cwd',value.get('cwd'))==str(fixture.root) for value in fixture.rpc('session.snapshot')['snapshot']['panes']))
                    file=fixture.root/'example-ui.py'
                    try:
                        file.write_text('def example():\n    return "<script>text</script>"\n')
                        page.locator('#panel-files').click()
                        page.get_by_text(file.name,exact=True).wait_for(timeout=20000)
                        page.get_by_text(file.name,exact=True).click()
                        page.wait_for_selector('.web-preview')
                        assert 'def example()' in page.locator('.web-preview').inner_text()
                        assert page.locator('.web-preview script').count()==0
                        assert page.locator('.web-preview .hljs-keyword').count()>0
                        field='must survive refresh'
                        page.keyboard.press('Control+Shift+P')
                        page.locator('#palette input').fill(field)
                        fixture.rpc('tab.rename',{'tab_id':tab,'label':'External rename'})
                        page.wait_for_timeout(500)
                        assert page.locator('#palette input').input_value()==field
                        page.keyboard.press('Escape')
                        page.locator('#panel-files').click()
                        page.wait_for_function('()=>document.querySelector(".web-preview")===null')
                    finally:
                        file.unlink(missing_ok=True)

                    page.locator('#layout-editor-button').click()
                    page.locator('.layout-editor input').fill('with-notes')
                    layout=page.evaluate('async()=>structuredClone((await import("/desktop/runtime/herdr.js")).model.layout)')
                    layout['children'].append({'id':'trusted-panel','type':'block','content':{'mod':'notes'},'size':{'px':180}})
                    page.locator('.layout-editor textarea').fill(json.dumps(layout))
                    page.get_by_role('button',name='Save copy / switch',exact=True).click()
                    page.wait_for_selector('#trusted-notes')
                    page.reload()
                    page.wait_for_selector('#trusted-notes')
                    page.locator('#layout-editor-button').click()
                    with page.expect_download() as pending_download:
                        page.get_by_role('button',name='Download',exact=True).click()
                    downloaded=json.loads(pathlib.Path(pending_download.value.path()).read_text())
                    assert any(child.get('id')=='trusted-panel' for child in downloaded['children'])
                    before=page.evaluate('localStorage.getItem("herdr-web.personal.v1")')
                    page.locator('.layout-editor textarea').fill('{broken')
                    page.get_by_role('button',name='Save copy / switch',exact=True).click()
                    assert page.locator('.layout-editor').is_visible()
                    assert page.evaluate('localStorage.getItem("herdr-web.personal.v1")')==before
                    page.get_by_role('button',name='Cancel',exact=True).click()
                    page.evaluate('async()=> (await import("/desktop/runtime/herdr.js")).run("layout.switch",{name:"main"})')
                    page.wait_for_function('()=>document.querySelector("#trusted-notes")===null')
                    page.locator('#layout-editor-button').click()
                    page.locator('.layout-editor input').fill('with-notes')
                    page.get_by_role('button',name='Delete personal layout',exact=True).click()
                    assert 'with-notes' not in json.loads(page.evaluate('localStorage.getItem("herdr-web.personal.v1")'))

                    page.evaluate('async()=> {const api=await import("/desktop/runtime/herdr.js");api.setShared("win98.boot",Date.now());api.setShared("win98.init",Date.now());api.setShared("win98.mute",true);await api.run("layout.switch",{name:"win98"});}')
                    page.wait_for_selector('[id="start~btn"]')
                    page.locator('[id="start~btn"]').click()
                    page.wait_for_selector('.web-popup #menu')
                    page.locator('.web-popup').get_by_text('Programs',exact=True).hover()
                    page.wait_for_function('()=>document.querySelectorAll(".web-popup").length===2')
                    page.keyboard.press('Escape')
                    page.wait_for_function('()=>document.querySelectorAll(".web-popup").length===1')
                    page.keyboard.press('Escape')
                    page.evaluate('async()=>{const api=await import("/desktop/runtime/herdr.js");api.setShared("win98.apps",{mines:"open",sol:"open"});}')
                    page.wait_for_selector('#mines-menu-0')
                    page.wait_for_selector('#sol-menu-Game')
                    title=page.locator('#sol-title').bounding_box()
                    page.mouse.move(title['x']+80,title['y']+8)
                    page.mouse.down()
                    page.mouse.move(title['x']+260,title['y']+50,steps=5)
                    page.mouse.up()
                    moved=page.locator('#sol-title').bounding_box()
                    assert moved['x']>title['x']+100,(title,moved)
                    page.locator('#sol-stock').click()
                    page.locator('[id="sol-close~btn"]').click()
                    page.locator('#mine-0').click()
                    page.locator('[id="mines-close~btn"]').click()
                    page.locator('#clippy').wait_for()
                    previous={item['pane_id'] for item in fixture.rpc('session.snapshot')['snapshot']['panes']}
                    page.locator('[id="start~btn"]').click()
                    page.locator('.web-popup').get_by_text('Run...',exact=True).click()
                    page.locator('#dlg-title').wait_for()
                    page.keyboard.type("printf 'HERDR98_%s\\n' RUN")
                    page.keyboard.press('Enter')
                    new_panes=wait_for(lambda:[item for item in fixture.rpc('session.snapshot')['snapshot']['panes'] if item['pane_id'] not in previous])
                    wait_for(lambda:any('HERDR98_RUN' in fixture.read(item['pane_id']) for item in new_panes))
                    page.wait_for_function('()=>document.querySelector("dialog[open]")===null')
                    page.screenshot(path=str(fixture.logs/f'{name}-herdr98.png'))
                    page.evaluate('async()=>{const api=await import("/desktop/runtime/herdr.js");api.setShared("win98.apps",{});await api.run("layout.switch",{name:"main"});}')
                    page.wait_for_selector('#space-new')
                    selected=page.evaluate('async()=>structuredClone((await import("/desktop/runtime/herdr.js")).model.selected)')
                    page.evaluate('''async()=>{const api=await import("/desktop/runtime/herdr.js");globalThis.webFocusTrace=[];const hook=api.model.hooks.focus;api.model.hooks.focus=id=>{webFocusTrace.push({requested:id,selected:{...api.model.selected},panes:[...document.querySelectorAll(".terminal-pane")].map(el=>({...el.dataset}))});return hook(id);};const focus=Terminal.prototype.focus;Terminal.prototype.focus=function(...args){focus.apply(this,args);webFocusTrace.push({terminal:this.element?.closest(".terminal-pane")?.dataset.pane,active:document.activeElement?.closest(".terminal-pane")?.dataset.pane});};}''')
                    backend_focus=fixture.rpc('session.snapshot')['snapshot'].get('focused_pane_id')
                    previous_panes={item['pane_id'] for item in fixture.rpc('session.snapshot')['snapshot']['panes']}
                    page.keyboard.press('Control+Alt+d')
                    page.wait_for_function('async previous=>(await import("/desktop/runtime/herdr.js")).model.selected.pane!==previous',arg=selected['pane'])
                    selected_pane=page.evaluate('async()=>(await import("/desktop/runtime/herdr.js")).model.selected.pane')
                    page.wait_for_function('pane=>document.activeElement?.closest(".terminal-pane")?.dataset.pane===pane',arg=selected_pane,timeout=8000)
                    page.keyboard.type("printf 'LOCAL_%s\\n' FOCUS")
                    page.keyboard.press('Enter')
                    wait_for(lambda:'LOCAL_FOCUS' in fixture.read(selected_pane))
                    backend_focus_after=fixture.rpc('session.snapshot')['snapshot'].get('focused_pane_id')
                    assert not any(command['method'] in {'workspace.focus','tab.focus','pane.focus','pane.focus_direction'} for command in commands),commands
                    assert all(command['params'].get('focus') is False for command in commands if command['method'] in {'workspace.create','tab.create','pane.split'}),commands
                    current_panes={item['pane_id'] for item in fixture.rpc('session.snapshot')['snapshot']['panes']}
                    assert previous_panes < current_panes and len(current_panes)==len(previous_panes)+1
                    page.screenshot(path=str(fixture.logs/f'{name}-desktop.png'))
                    errors.extend(page.evaluate('webPanelErrors'))
                    assert not errors,errors
                    results[name]={'version':browser.version,'empty_create':True,'context_target_and_coordinates':True,'rename_cancel':True,'palette_theme':True,'privacy':True,'file_highlight':True,'personal_layout_and_trusted_panel_reload':True,'layout_download_and_delete':True,'herdr98_games_drag_and_clippy':True,'herdr98_run_real_terminal':True,'split_selects_and_focuses_locally':True,'no_public_focus_mutations':True,'native_default_focus_observed':[backend_focus,backend_focus_after],'errors':errors}
                except Exception:
                    try:(fixture.logs/f'{name}-state.json').write_text(json.dumps(page.evaluate('''async()=>{const api=await import("/desktop/runtime/herdr.js");return {selected:api.model.selected,trace:globalThis.webFocusTrace,active:document.activeElement?.outerHTML.slice(0,200),panes:[...document.querySelectorAll(".terminal-pane")].map(el=>({...el.dataset,rect:el.getBoundingClientRect().toJSON()})),overlays:[...document.querySelectorAll(".web-dialog,.web-popup,dialog[open]")].map(el=>el.outerHTML.slice(0,150)),notices:document.querySelector("#notifications").innerText};}'''),indent=2))
                    except Exception as capture:print('Failure state capture:',capture,flush=True)
                    try:page.screenshot(path=str(fixture.logs/f'{name}-failure.png'),timeout=5000)
                    except Exception:pass
                    try:(fixture.logs/f'{name}-failure.txt').write_text(page.locator('body').inner_text(timeout=3000)+'\n'+json.dumps(errors))
                    except Exception:pass
                    raise
                finally:
                    context.close();browser.close()
                    for space in fixture.rpc('session.snapshot')['snapshot']['workspaces']:
                        fixture.rpc('workspace.close',{'workspace_id':space['workspace_id']})
        (fixture.logs/'results.json').write_text(json.dumps(results,indent=2))
        print(json.dumps(results,indent=2),flush=True)


if __name__=='__main__':
    main()
