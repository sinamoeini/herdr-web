#!/usr/bin/env python3
"""Headed-browser clipboard and media checks on a private X11 display."""

import base64
import ctypes
import json
import os
import pathlib
import shutil
import stat
import struct
import subprocess
import uuid
import zlib

from harness import Fixture, wait_for
from playwright.sync_api import sync_playwright

BEFORE_AUDIO = 'HERDR_AUDIO_BEFORE_GESTURE:'


def png():
    def chunk(kind, data):
        return struct.pack('!I', len(data)) + kind + data + struct.pack('!I', zlib.crc32(kind + data))
    pixels = (b'\0' + bytes([255, 0, 0, 255]) * 8) * 8
    return b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('!IIBBBBB', 8, 8, 8, 6, 0, 0, 0)) + chunk(b'IDAT', zlib.compress(pixels)) + chunk(b'IEND', b'')


def clipboard_owner():
    library = ctypes.CDLL('libX11.so.6')
    library.XOpenDisplay.argtypes = [ctypes.c_char_p]
    library.XOpenDisplay.restype = ctypes.c_void_p
    library.XInternAtom.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int]
    library.XInternAtom.restype = ctypes.c_ulong
    library.XGetSelectionOwner.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
    library.XGetSelectionOwner.restype = ctypes.c_ulong
    library.XCloseDisplay.argtypes = [ctypes.c_void_p]
    display = library.XOpenDisplay(None)
    if not display:
        raise RuntimeError('Cannot open the isolated X11 display; run with xvfb-run -a')
    try:
        return library.XGetSelectionOwner(display, library.XInternAtom(display, b'CLIPBOARD', 0))
    finally:
        library.XCloseDisplay(display)


INIT = '''
globalThis.permissionEvidence={audio:[],pastes:[],clipboard:{},beforeComplete:false};
globalThis.testAudioElements=[];
const NativeAudio=globalThis.Audio;
globalThis.Audio=function(...args){
  const audio=new NativeAudio(...args),record={source:args[0],activation:navigator.userActivation.hasBeenActive,playing:false,ended:false};
  permissionEvidence.audio.push(record);testAudioElements.push(audio);
  audio.addEventListener('playing',event=>{record.playing=true;record.trusted=event.isTrusted;});
  audio.addEventListener('ended',()=>record.ended=true);
  audio.addEventListener('error',()=>{record.error=audio.error?.code;record.errorMessage=audio.error?.message;});
  const play=audio.play.bind(audio);
  audio.play=()=>play().then(value=>{record.outcome='allowed';return value;},error=>{record.outcome=error.name;throw error;});
  return audio;
};
document.addEventListener('paste',event=>permissionEvidence.pastes.push({trusted:event.isTrusted,types:[...(event.clipboardData?.types||[])]}),true);
addEventListener('load',()=>setTimeout(async()=>{
  const api=await import('/desktop/runtime/herdr.js');
  await api.playSound('blocked');permissionEvidence.beforeComplete=true;
  console.debug('HERDR_AUDIO_BEFORE_GESTURE:'+JSON.stringify(permissionEvidence.audio[0]));
},100));
'''


def main():
    if not os.environ.get('DISPLAY'):
        raise SystemExit('Use a private display: xvfb-run -a python test/permissions.py')
    if not shutil.which('pulseaudio'):
        raise SystemExit('Install the optional PulseAudio test prerequisite; this test uses a private null sink, not the system audio service')
    evidence = {'scope': 'Headed browsers on isolated X11 and a private PulseAudio null sink; no synthetic clipboard events or permission grants. Physical speaker audibility and other desktop environments are not tested.', 'pulseaudio': subprocess.check_output(['pulseaudio','--version'],text=True).strip(), 'browsers': {}}
    with Fixture('permissions') as fixture:
        audio_socket=fixture.root/'pulse.sock'
        audio_config=fixture.root/'pulse.pa'
        audio_config.write_text(f'load-module module-native-protocol-unix socket={audio_socket} auth-anonymous=1\nload-module module-null-sink sink_name=herdr_web_test\nset-default-sink herdr_web_test\n')
        pulse=fixture.process(['pulseaudio','--daemonize=no','--use-pid-file=no','--exit-idle-time=-1','-n','--file='+str(audio_config)],'pulseaudio.log')
        def audio_ready():
            assert pulse.poll() is None,'Private PulseAudio exited; inspect pulseaudio.log'
            return audio_socket.exists()
        wait_for(audio_ready)
        pane = fixture.rpc('workspace.create', {'label': 'Permission checks', 'focus': False})['root_pane']['pane_id']
        uploads = []
        with sync_playwright() as playwright:
            for name in ['chromium', 'firefox']:
                browser = getattr(playwright, name).launch(headless=False,env={**os.environ,'PULSE_SERVER':'unix:'+str(audio_socket)})
                context = browser.new_context(viewport={'width': 1200, 'height': 850})
                page = context.new_page()
                errors = []
                page.on('pageerror', lambda error: errors.append(str(error)))
                page.add_init_script(INIT)
                try:
                    with page.expect_console_message(lambda message: message.type == 'debug' and message.text.startswith(BEFORE_AUDIO), timeout=15000) as probe:
                        page.goto(fixture.url)
                    before = json.loads(probe.value.text[len(BEFORE_AUDIO):])
                    assert before['activation'] is False, before
                    assert before['outcome'] == 'NotAllowedError', before
                    page.bring_to_front()
                    page.wait_for_selector('.terminal-pane[data-ready="true"]', timeout=20000)
                    page.evaluate('''encoded=>{
                      const button=document.createElement('button');button.id='permission-clipboard';button.textContent='Write test image';button.style.cssText='position:fixed;top:5px;right:5px;z-index:30000';
                      button.onclick=async()=>{try{permissionEvidence.clipboard.activation=navigator.userActivation.isActive;const bytes=Uint8Array.from(atob(encoded),letter=>letter.charCodeAt(0));await navigator.clipboard.write([new ClipboardItem({'image/png':new Blob([bytes],{type:'image/png'})})]);permissionEvidence.clipboard.status='written';}catch(error){permissionEvidence.clipboard.error=error.name+': '+error.message;}};
                      document.body.append(button);
                    }''', base64.b64encode(png()).decode())
                    page.locator('#permission-clipboard').click()
                    page.wait_for_function('()=>permissionEvidence.clipboard.status||permissionEvidence.clipboard.error')
                    clipboard = page.evaluate('permissionEvidence.clipboard')
                    assert clipboard.get('status') == 'written' and clipboard['activation'], clipboard
                    owner = clipboard_owner()
                    assert owner != 0, 'Browser did not publish an X11 CLIPBOARD selection'
                    tag=name+'_'+uuid.uuid4().hex[:8]
                    marker='CLIPBOARD_'+tag+'='
                    fixture.rpc('pane.send_input', {'pane_id': pane, 'text': f'read -r WEB_PASTED; printf "CLIPBOARD_%s=%s\\n" {tag} "$WEB_PASTED"', 'keys': ['enter']})
                    page.wait_for_timeout(300)
                    page.locator('.xterm-helper-textarea').focus()
                    with page.expect_response(lambda response: response.url.endswith('/api/clipboard-image')) as pending:
                        page.keyboard.press('Control+v')
                    response = pending.value
                    assert response.ok, response.text()
                    path = pathlib.Path(response.json()['path'])
                    assert path.parent.name.startswith(f'herdr-web-images-{fixture.web.pid}-'), path
                    uploads.append(path)
                    assert stat.S_IMODE(path.stat().st_mode) == 0o600
                    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
                    assert path.read_bytes()[:8] == b'\x89PNG\r\n\x1a\n'
                    assert struct.unpack('!II', path.read_bytes()[16:24]) == (8, 8)
                    page.wait_for_timeout(300)
                    assert marker not in fixture.read(pane)
                    page.keyboard.press('Enter')
                    wait_for(lambda: f"{marker}'{path}'" in fixture.read(pane))
                    pastes = page.evaluate('permissionEvidence.pastes')
                    assert any(event['trusted'] and 'Files' in event['types'] for event in pastes), pastes
                    page.locator('#settings').click()
                    page.locator('#st-s-alerts').click()
                    page.locator('#st-sounds').click()
                    page.locator('#st-play-blocked').click()
                    page.wait_for_function('()=>permissionEvidence.audio.length>1&&(permissionEvidence.audio.at(-1).ended||permissionEvidence.audio.at(-1).error)', timeout=15000)
                    after = page.evaluate('permissionEvidence.audio.at(-1)')
                    assert after.get('outcome') == 'allowed' and after['activation'] and after['playing'] and after['ended'] and after['trusted'] and not after.get('error'), after
                    assert not errors, errors
                    evidence['browsers'][name] = {'version': browser.version, 'clipboard': clipboard, 'x11_clipboard_owner': owner, 'native_paste_events': pastes, 'private_upload_and_no_enter': True, 'audio_before_gesture': before, 'audio_after_gesture': after, 'errors': errors}
                    (fixture.logs / f'{name}-result.json').write_text(json.dumps(evidence['browsers'][name],indent=2))
                    page.screenshot(path=str(fixture.logs / f'{name}-permissions.png'))
                except Exception:
                    (fixture.logs / f'{name}-failure.json').write_text(json.dumps({'state': page.evaluate('permissionEvidence'), 'errors': errors}, indent=2))
                    page.screenshot(path=str(fixture.logs / f'{name}-failure.png'))
                    raise
                finally:
                    context.close()
                    browser.close()
                    for path in uploads:
                        path.unlink(missing_ok=True)
                    uploads.clear()
        (fixture.logs / 'results.json').write_text(json.dumps(evidence, indent=2))
        print(json.dumps(evidence, indent=2), flush=True)


if __name__ == '__main__':
    main()
