"""Whole terminal rows remain visible after browser and pane resizing."""

import base64
import json
import shlex

from frontend_regressions import create_space, terminal, type_command
from harness import wait_for
from playwright.sync_api import expect


PROGRAM = r"""
import json, os, pathlib, signal, sys, termios, tty
dimensions = pathlib.Path(sys.argv[1])
previous = termios.tcgetattr(0)
def draw(*_):
    cols, rows = os.get_terminal_size()
    sys.stdout.write(f'\033[2J\033[{rows};1H\033[48;2;255;0;255m\033[2KBOTTOM_EDGE_{rows}_{cols}\033[0m')
    sys.stdout.flush()
    dimensions.write_text(json.dumps({'rows': rows, 'cols': cols}))
try:
    tty.setraw(0)
    sys.stdout.write('\033[?1049h\033[?25l')
    signal.signal(signal.SIGWINCH, draw)
    draw()
    while os.read(0, 1) != b'q':
        pass
finally:
    termios.tcsetattr(0, termios.TCSANOW, previous)
    sys.stdout.write('\033[0m\033[?1049l\033[?25h\r\nGEOMETRY_STOPPED\r\n')
    sys.stdout.flush()
"""


def measure(view):
    return view.evaluate("""pane => {
      const viewport=pane.querySelector(".terminal-viewport"),screen=pane.querySelector(".xterm-screen");
      const outer=viewport.getBoundingClientRect(),style=getComputedStyle(viewport);
      const rows=screen.querySelector(".xterm-rows");
      return {screen:screen.getBoundingClientRect().toJSON(),row:rows.lastElementChild.getBoundingClientRect().toJSON(),
        rows:rows.children.length,content:{
          top:outer.top+parseFloat(style.paddingTop),bottom:outer.bottom-parseFloat(style.paddingBottom),
          left:outer.left+parseFloat(style.paddingLeft),right:outer.right-parseFloat(style.paddingRight)
        }};
    }""")


def run_terminal_geometry(page, fixture):
    original_size = page.viewport_size
    expect(page.locator('#notifications .web-toast')).to_have_count(0, timeout=10000)
    first = create_space(page)
    pane = first['pane']
    view = terminal(page, pane)
    program = fixture.root / 'terminal-geometry.py'
    dimensions = fixture.root / 'terminal-geometry.json'
    program.write_text(PROGRAM)
    dimensions.unlink(missing_ok=True)
    type_command(page, pane, f'python3 {shlex.quote(str(program))} {shlex.quote(str(dimensions))}')
    wait_for(dimensions.is_file)
    evidence = []
    name = page.context.browser.browser_type.name

    def fits(label):
        page.evaluate('()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))')
        def ready():
            geometry = measure(view)
            size = json.loads(dimensions.read_text())
            row = view.locator('.xterm-rows > div').last
            return (geometry, size) if size['rows'] == geometry['rows'] and f'BOTTOM_EDGE_{size["rows"]}_{size["cols"]}' in row.inner_text() else None
        geometry, size = wait_for(ready)
        evidence.append({'case': label, 'geometry': geometry, 'pty': size})
        image = page.screenshot(path=str(fixture.logs / f'{name}-terminal-{label}.png'))
        screen, content, row = geometry['screen'], geometry['content'], geometry['row']
        assert screen['top'] >= content['top'] - .5, geometry
        assert screen['left'] >= content['left'] - .5, geometry
        assert screen['bottom'] <= content['bottom'] + .5, geometry
        assert row['bottom'] <= content['bottom'] + .5, geometry
        assert screen['right'] <= content['right'] + .5, geometry
        pixels = page.evaluate("""async ({png,x,ys}) => {
          const image=new Image();image.src="data:image/png;base64,"+png;await image.decode();
          const canvas=document.createElement("canvas");canvas.width=image.width;canvas.height=image.height;
          const context=canvas.getContext("2d");context.drawImage(image,0,0);
          return ys.map(y=>Array.from(context.getImageData(Math.floor(x*image.width/innerWidth),Math.floor(y*image.height/innerHeight),1,1).data));
        }""", {'png': base64.b64encode(image).decode(), 'x': screen['left'] + screen['width'] * .7,
                'ys': [row['top'] + 1, (row['top'] + row['bottom']) / 2, row['bottom'] - 1]})
        assert pixels == [[255, 0, 255, 255]] * 3, (geometry, pixels)
        evidence[-1]['last_row_pixels'] = pixels

    try:
        for width, height in [(1200, 800), (900, 600), (900, 601), (1440, 960)]:
            page.set_viewport_size({'width': width, 'height': height})
            fits(f'{width}x{height}')
        page.keyboard.press('Control+,')
        page.locator('#st-font_size-plus').click()
        page.keyboard.press('Escape')
        fits('larger-font')
        view.locator('.xterm-helper-textarea').focus()
        with page.expect_response(lambda response: response.url.endswith('/api/command') and response.request.method == 'POST' and response.request.post_data_json.get('method') == 'pane.split') as response:
            page.keyboard.press('Control+Alt+e')
        assert response.value.ok
        new_pane = response.value.json()['pane']['pane_id']
        terminal(page, new_pane)
        expect(page.locator('.terminal-content .terminal-pane')).to_have_count(2)
        fits('horizontal-split')
        divider = page.get_by_role('separator', name='Resize panes')
        expect(divider).to_have_attribute('aria-orientation', 'horizontal')
        divider.focus()
        page.keyboard.press('ArrowUp')
        expect(divider).to_have_attribute('aria-valuenow', '45')
        fits('resized-split')
        return {'whole_rows_and_padding': True, 'bottom_row_pixels': True, 'cases': len(evidence)}
    finally:
        (fixture.logs / f'{name}-terminal-geometry.json').write_text(json.dumps(evidence, indent=2) + '\n')
        view.locator('.xterm-helper-textarea').focus()
        page.keyboard.type('q')
        wait_for(lambda: 'GEOMETRY_STOPPED' in fixture.read(pane))
        page.set_viewport_size(original_size)
