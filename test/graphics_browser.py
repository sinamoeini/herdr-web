#!/usr/bin/env python3
"""Real Kitty crop/reload/resize/zoom and bitmap-retention browser regressions."""

import base64
import json

from harness import Fixture
from playwright.sync_api import sync_playwright


PIXELS = """()=>{let red=0,blue=0,opaque=0;for(const canvas of document.querySelectorAll('.web-image-canvas')){if(!canvas.width||!canvas.height)continue;const bytes=canvas.getContext('2d').getImageData(0,0,canvas.width,canvas.height).data;for(let offset=0;offset<bytes.length;offset+=4){if(bytes[offset+3])opaque++;if(bytes[offset]>240&&bytes[offset+1]<10&&bytes[offset+2]<10&&bytes[offset+3]>240)red++;if(bytes[offset]<10&&bytes[offset+1]<10&&bytes[offset+2]>240&&bytes[offset+3]>240)blue++;}}return {red,blue,opaque};}"""


def main():
    evidence = {}
    with Fixture('graphics-browser') as fixture:
        pane = fixture.rpc('workspace.create', {'label': 'Graphics regression', 'focus': False})['root_pane']['pane_id']
        with sync_playwright() as playwright:
            for name in ['chromium', 'firefox']:
                browser = getattr(playwright, name).launch()
                context = browser.new_context(viewport={'width': 1200, 'height': 850})
                page = context.new_page()
                errors = []
                page.on('pageerror', lambda error: errors.append(str(error)))
                page.add_init_script('''globalThis.webBitmapCounts={created:0,closed:0};const original=globalThis.createImageBitmap.bind(globalThis);globalThis.createImageBitmap=async(...args)=>{const image=await original(...args);globalThis.webBitmapCounts.created++;const close=image.close.bind(image);image.close=()=>{globalThis.webBitmapCounts.closed++;close();};return image;};''')

                def emit(control, pixels=b''):
                    sequence = '\x1b_G' + control + ';' + base64.b64encode(pixels).decode() + '\x1b\\'
                    fixture.post('/api/terminal-launch', {'pane': pane, 'argv': ['python3', '-c', 'import sys;sys.stdout.write(' + repr(sequence) + ');sys.stdout.flush()']})

                def red():
                    page.wait_for_function('() => (' + PIXELS + ')().red>0', timeout=15000)

                try:
                    page.goto(fixture.url)
                    page.wait_for_selector('.xterm-helper-textarea', state='attached')
                    emit('a=T,f=32,s=8,v=8,t=d,q=2,i=4242,c=2,r=2', bytes([255, 0, 0, 255]) * 64)
                    red()
                    page.reload()
                    page.wait_for_selector('.xterm-helper-textarea', state='attached')
                    red()
                    page.set_viewport_size({'width': 1050, 'height': 760})
                    red()
                    page.evaluate('async()=>await(await import("/desktop/runtime/herdr.js")).run("app.zoom_in")')
                    red()
                    page.evaluate('async()=>await(await import("/desktop/runtime/herdr.js")).run("app.zoom_reset")')
                    emit('a=d,d=I,q=2,i=4242')
                    page.wait_for_function('() => (' + PIXELS + ')().opaque===0')
                    mixed = (bytes([255, 0, 0, 255]) * 4 + bytes([0, 0, 255, 255]) * 4) * 8
                    emit('a=T,f=32,s=8,v=8,t=d,q=2,i=4243,x=4,y=0,w=4,h=8,c=2,r=2', mixed)
                    page.wait_for_function('() => {const pixels=(' + PIXELS + ')();return pixels.blue>0&&pixels.red===0;}', timeout=15000)
                    page.screenshot(path=str(fixture.logs / f'{name}-crop.png'))
                    emit('a=d,d=I,q=2,i=4243')
                    page.wait_for_function('() => (' + PIXELS + ')().opaque===0')
                    page.wait_for_function('()=>webBitmapCounts.created>0&&webBitmapCounts.created===webBitmapCounts.closed')
                    assert not errors, errors
                    assert not page.locator('#notifications').inner_text(), page.locator('#notifications').inner_text()
                    evidence[name] = {'version': browser.version, 'reload_resize_zoom': True, 'real_kitty_crop_pixels': True, 'deletion_closes_bitmaps': page.evaluate('webBitmapCounts'), 'errors': errors}
                except Exception:
                    page.screenshot(path=str(fixture.logs / f'{name}-failure.png'))
                    (fixture.logs / f'{name}-failure.json').write_text(json.dumps({'pixels': page.evaluate('(' + PIXELS + ')()'), 'errors': errors}, indent=2))
                    raise
                finally:
                    context.close()
                    browser.close()
        (fixture.logs / 'results.json').write_text(json.dumps(evidence, indent=2))
        print(json.dumps(evidence, indent=2), flush=True)


if __name__ == '__main__':
    main()
