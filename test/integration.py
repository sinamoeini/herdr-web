#!/usr/bin/env python3
import json
import os
import pathlib
import re
import socket
import subprocess
import tempfile
import time
import uuid
import traceback
import base64
import urllib.parse
from browser import wait_url
from harness import BINARY, wait_for_browser
from playwright.sync_api import sync_playwright

ROOT = pathlib.Path(__file__).resolve().parents[1]
ARTIFACTS = ROOT / 'test-results/integration'
ARTIFACTS.mkdir(parents=True, exist_ok=True)


def until(callback, timeout=20):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            result = callback()
            if result:
                return result
        except (OSError, KeyError, AssertionError):
            pass
        time.sleep(.15)
    raise AssertionError('Timed out waiting for real backend state')


def main():
    executable = pathlib.Path(os.environ.get('HERDR_BIN', pathlib.Path.home() / '.local/bin/herdr'))
    processes = []
    with tempfile.TemporaryDirectory(prefix='herdr-web-integration-') as temporary:
        directory = pathlib.Path(temporary)
        environment = {key: value for key, value in os.environ.items() if not key.startswith('HERDR_')}
        for key in ['XDG_CONFIG_HOME', 'XDG_STATE_HOME', 'XDG_DATA_HOME', 'XDG_RUNTIME_DIR']:
            path = directory / key
            path.mkdir(mode=0o700)
            environment[key] = str(path)
        config = directory / 'config.toml'
        config.write_text('')
        name = 'web-test-' + uuid.uuid4().hex[:10]
        environment.update(HERDR_CONFIG_PATH=str(config), HERDR_SESSION=name, HERDR_BIN=str(executable), SHELL='/bin/bash', PORT='0')
        project=directory/'project';project.mkdir()
        subprocess.run(['git','init','-q',str(project)],check=True)
        subprocess.run(['git','-C',str(project),'config','user.email','web-test@example.invalid'],check=True)
        subprocess.run(['git','-C',str(project),'config','user.name','Herdr Web Test'],check=True)
        (project/'hello.py').write_text('print("hello")\n')
        subprocess.run(['git','-C',str(project),'add','-A'],check=True)
        subprocess.run(['git','-C',str(project),'commit','-qm','initial fixture'],check=True)
        server_log = open(ARTIFACTS / 'herdr.log', 'w')
        server = subprocess.Popen([str(executable), '--session', name, 'server'], env=environment, stdout=server_log, stderr=subprocess.STDOUT,start_new_session=True)
        processes.append(server)
        try:
            def discover():
                value = json.loads(subprocess.check_output([str(executable), 'session', 'list', '--json'], env=environment))
                return next((session['socket_path'] for session in value['sessions'] if session['name'] == name and session['running']), None)
            selected_socket = until(discover)

            def rpc(method, params):
                with socket.socket(socket.AF_UNIX) as connection:
                    connection.settimeout(4)
                    connection.connect(selected_socket)
                    connection.sendall((json.dumps({'id':'test','method':method,'params':params})+'\n').encode())
                    result = bytearray()
                    while not result.endswith(b'\n'):
                        chunk = connection.recv(65536)
                        if not chunk or len(result) > 8*1024*1024:
                            raise AssertionError('Invalid backend response')
                        result.extend(chunk)
                    envelope = json.loads(result)
                    assert 'error' not in envelope, envelope
                    return envelope['result']

            first = rpc('workspace.create', {'label':'Browser one', 'focus':False, 'cwd':str(project)})
            second = rpc('workspace.create', {'label':'Browser two', 'focus':False, 'cwd':str(project)})
            pane = first['root_pane']['pane_id']
            def read(): return rpc('pane.read',{'pane_id':pane,'source':'visible','format':'text','strip_ansi':True})['read']['text']
            first_tab = first['tab']['tab_id']
            second_tab = second['tab']['tab_id']
            original_focus={key:rpc('session.snapshot',{})['snapshot'].get(key) for key in ['focused_workspace_id','focused_tab_id','focused_pane_id']}

            def start_web():
                process = subprocess.Popen([str(BINARY)],env=environment,stdout=subprocess.PIPE,stderr=open(ARTIFACTS/'web.log','a'),text=True,start_new_session=True)
                processes.append(process)
                return process, wait_url(process)
            web, url = start_web()
            record = {'backend':subprocess.check_output([str(executable),'--version'],text=True).strip(),'browsers':{}}
            with sync_playwright() as playwright:
                for browser_name in ['chromium','firefox']:
                    browser = getattr(playwright,browser_name).launch()
                    context = browser.new_context(viewport={'width':1400,'height':900})
                    browser_commands=[]
                    context.on('request',lambda request:browser_commands.append(request.post_data_json) if request.url.endswith('/api/command') and request.method=='POST' else None)
                    context.add_init_script("""const send=WebSocket.prototype.send; WebSocket.prototype.send=function(data){ if(typeof data==='string' && data.includes('"type":"input"'))setTimeout(()=>{if(this.readyState===1)send.call(this,data);},750); else send.call(this,data); };""")
                    context.add_init_script("globalThis.addEventListener('herdr-error',event=>console.error(event.detail?.stack||String(event.detail)))")
                    context.add_init_script("""globalThis.webGraphics=[];globalThis.WebSocket=class extends WebSocket{constructor(...args){super(...args);if(String(args[0]).includes('/graphics/'))this.addEventListener('message',event=>{const scene=JSON.parse(event.data);globalThis.webGraphics.push({assets:scene.assets?.length,placements:scene.placements,retained:scene.retained,error:scene.error});if(globalThis.webGraphics.length>20)globalThis.webGraphics.shift();});}}""")
                    context.add_init_script('sessionStorage.setItem("herdr-web.selection.v1",'+json.dumps(json.dumps({'space':first['workspace']['workspace_id'],'tab':first_tab,'pane':pane}))+')')
                    page = context.new_page()
                    errors=[]
                    cancelled_watches=[]
                    page.on('pageerror',lambda error:errors.append(str(error)))
                    def failed(request):
                        record=request.url+': '+str(request.failure)
                        if urllib.parse.urlsplit(request.url).path=='/api/watch' and str(request.failure) in ['net::ERR_ABORTED','NS_BINDING_ABORTED','NS_ERROR_ABORT']:
                            cancelled_watches.append(record)
                        else:
                            errors.append(record)
                    page.on('requestfailed',failed)
                    page.on('console',lambda message:print(browser_name,message.type,message.text,flush=True) if message.type=='error' else None)
                    debug=context.new_cdp_session(page) if browser_name=='chromium' else None
                    if debug:
                        debug.send('Debugger.enable')
                        debug.on('Debugger.paused',lambda event:print('PAUSED STACK',json.dumps([{'function':frame['functionName'],'location':frame['location']} for frame in event['callFrames'][:10]]),flush=True))
                    try:
                        page.goto(url)
                        page.wait_for_selector('.xterm-helper-textarea',state='attached',timeout=12000)
                        page.wait_for_timeout(1500)
                        notifications=page.locator('#notifications').inner_text()
                        assert not notifications, notifications
                        assert page.locator('#space-new').count(), 'Official spaces panel did not render'
                        assert page.locator('#tabs').count() or page.locator('#tab-scroll').count(), 'Official tabs did not render'
                        def post(route,data):
                            response=context.request.post(url+route,data=data,headers={'Origin':url,'Content-Type':'application/json'})
                            assert response.ok,response.text()
                            return response.json()
                        another=context.new_page();another.goto(url)
                        another.locator('[id=' + json.dumps('space-' + second['workspace']['workspace_id']) + ']').click()
                        another.locator('[id=' + json.dumps('tab-' + second_tab) + ']').click()
                        wait_for_browser(another, "async id => (await import('/desktop/runtime/herdr.js')).model.selected.tab===id",arg=second_tab)
                        another.wait_for_selector('.terminal-pane[data-pane=' + json.dumps(second['root_pane']['pane_id']) + '][data-ready="true"]',timeout=15000)
                        assert page.evaluate("async()=> (await import('/desktop/runtime/herdr.js')).model.selected.tab")==first_tab
                        assert not any(command['method'] in {'workspace.focus','tab.focus','pane.focus','pane.focus_direction'} for command in browser_commands),browser_commands
                        another.close()
                        page.locator('.xterm-helper-textarea').first.focus()
                        marker='WEB_'+uuid.uuid4().hex[:8]
                        page.keyboard.type(f"export WEB_PERSIST={marker}; printf 'PID=%s VALUE=%s\\n' \"$$\" \"$WEB_PERSIST\"")
                        page.keyboard.press('Enter')
                        text=until(lambda: read() if f'VALUE={marker}' in read() else None)
                        assert '1016;2$y1;2c' not in text, text
                        pid=re.findall(r'PID=(\d+) VALUE='+marker,text)[-1]
                        page.reload()
                        page.wait_for_selector('.xterm-helper-textarea',state='attached')
                        page.wait_for_timeout(1800)
                        page.locator('.xterm-helper-textarea').first.focus()
                        page.keyboard.type("printf 'AFTER=%s:%s\\n' \"$$\" \"$WEB_PERSIST\"")
                        page.keyboard.press('Enter')
                        until(lambda:f'AFTER={pid}:{marker}' in read())
                        page.keyboard.type("read -r WEB_PENDING; printf 'READ=%s\\n' \"$WEB_PENDING\"")
                        page.keyboard.press('Enter');page.wait_for_timeout(1000)
                        pending='unsubmitted-'+uuid.uuid4().hex[:8]
                        page.keyboard.type(pending);page.wait_for_timeout(1000)
                        page.reload();page.wait_for_selector('.xterm-helper-textarea',state='attached');page.wait_for_timeout(1800)
                        assert f'READ={pending}' not in read()
                        page.locator('.xterm-helper-textarea').first.focus();page.keyboard.type('-rest');page.keyboard.press('Enter')
                        until(lambda:f'READ={pending}-rest' in read())
                        page.locator(f'[id="tab-{first_tab}"]').click(button='right')
                        assert page.locator('.web-popup').count()==1
                        page.get_by_text('Rename',exact=True).click()
                        page.wait_for_selector('#prompt input')
                        page.locator('#prompt input').fill('cancel this rename')
                        page.get_by_text('Cancel',exact=True).click()
                        assert rpc('session.snapshot',{})['snapshot']['tabs'][0]['label']!='cancel this rename'
                        pixels=base64.b64encode(bytes([255,0,0,255])*64).decode()
                        image_script="import sys;sys.stdout.write('\\x1b_Gf=32,s=8,v=8,a=T,t=d,q=2,i=42;"+pixels+"\\x1b\\\\');sys.stdout.flush()"
                        post('/api/terminal-launch',{'pane':pane,'argv':['python3','-c',image_script]})
                        page.wait_for_function("""()=>Array.from(document.querySelectorAll('.web-image-canvas')).some(canvas=>{if(!canvas.width||!canvas.height)return false;const bytes=canvas.getContext('2d').getImageData(0,0,canvas.width,canvas.height).data;for(let offset=0;offset<bytes.length;offset+=4)if(bytes[offset]>240&&bytes[offset+1]<10&&bytes[offset+2]<10&&bytes[offset+3]>240)return true;return false;})""",timeout=15000)
                        page.screenshot(path=str(ARTIFACTS/f'{browser_name}-kitty.png'))
                        post('/api/terminal-launch',{'pane':pane,'argv':['python3','-c',"import sys;sys.stdout.write('\\x1b_Ga=d,d=I,q=2,i=42;\\x1b\\\\');sys.stdout.flush()"]})
                        page.wait_for_function("""()=>Array.from(document.querySelectorAll('.web-image-canvas')).every(canvas=>{if(!canvas.width||!canvas.height)return true;const bytes=canvas.getContext('2d').getImageData(0,0,canvas.width,canvas.height).data;for(let offset=3;offset<bytes.length;offset+=4)if(bytes[offset])return false;return true;})""")
                        if browser_name=='chromium':
                            environment['PORT']=str(urllib.parse.urlsplit(url).port)
                            web.terminate();web.wait(timeout=15);web,url=start_web()
                            page.reload();page.wait_for_selector('.xterm-helper-textarea',state='attached');page.wait_for_timeout(1800)
                            page.locator('.xterm-helper-textarea').first.focus();page.keyboard.type("printf 'RESTART=%s:%s\\n' \"$$\" \"$WEB_PERSIST\"");page.keyboard.press('Enter')
                            until(lambda:f'RESTART={pid}:{marker}' in read())
                        page.evaluate("async()=> (await import('/desktop/runtime/herdr.js')).run('layout.switch',{name:'terminals'})")
                        page.wait_for_timeout(400);page.screenshot(path=str(ARTIFACTS/f'{browser_name}-terminals.png'))
                        page.evaluate("async()=> (await import('/desktop/runtime/herdr.js')).run('layout.switch',{name:'main'})")
                        page.wait_for_timeout(400)
                        page.screenshot(path=str(ARTIFACTS/f'{browser_name}-main.png'))
                        assert not errors, errors
                        record['browsers'][browser_name]={'version':browser.version,'shell_pid':pid,'persistent_value':marker,'independent_views':True,'blocked_read_survived':True,'kitty_pixels_and_delete':True,'expected_watch_cancellations':cancelled_watches,'errors':errors}
                    except Exception:
                        failure=traceback.format_exc()
                        print(failure,flush=True)
                        if debug:
                            try: debug.send('Debugger.pause');page.wait_for_timeout(300);debug.send('Debugger.resume')
                            except Exception: pass
                        (ARTIFACTS/f'{browser_name}-failure.txt').write_text(failure+'\n'+json.dumps(errors))
                        try:
                            (ARTIFACTS/f'{browser_name}-backend-failure.json').write_text(json.dumps({'visible':read(),'process':rpc('pane.process_info',{'pane_id':pane}),'graphics':page.evaluate('globalThis.webGraphics')},indent=2))
                        except Exception as capture: print('Backend capture failed:',capture,flush=True)
                        try: page.screenshot(path=str(ARTIFACTS/f'{browser_name}-failure.png'),full_page=True,timeout=5000,animations='disabled')
                        except Exception as capture: print('Screenshot failed:',capture,flush=True)
                        try: (ARTIFACTS/f'{browser_name}-body.txt').write_text(page.locator('body').inner_text(timeout=3000))
                        except Exception: pass
                        raise
                    finally:
                        context.close()
                        browser.close()
            (ARTIFACTS/'results.json').write_text(json.dumps(record,indent=2))
            print(json.dumps(record,indent=2))
        finally:
            for process in reversed(processes):
                if process.poll() is None:
                    process.terminate()
                try: process.wait(timeout=25)
                except subprocess.TimeoutExpired:
                    process.kill(); process.wait()
            server_log.close()


if __name__ == '__main__':
    main()
