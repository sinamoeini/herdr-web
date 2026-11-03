import ctypes
import json
import pathlib
import re
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]


class JavaScriptCore:
    def __init__(self):
        self.library = ctypes.CDLL("/System/Library/Frameworks/JavaScriptCore.framework/JavaScriptCore")
        signatures = {
            "JSGlobalContextCreate": (ctypes.c_void_p, [ctypes.c_void_p]),
            "JSGlobalContextRelease": (None, [ctypes.c_void_p]),
            "JSStringCreateWithUTF8CString": (ctypes.c_void_p, [ctypes.c_char_p]),
            "JSStringRelease": (None, [ctypes.c_void_p]),
            "JSEvaluateScript": (ctypes.c_void_p, [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int, ctypes.POINTER(ctypes.c_void_p)]),
            "JSValueToStringCopy": (ctypes.c_void_p, [ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]),
            "JSStringGetMaximumUTF8CStringSize": (ctypes.c_size_t, [ctypes.c_void_p]),
            "JSStringGetUTF8CString": (ctypes.c_size_t, [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t]),
        }
        for name, (result, arguments) in signatures.items():
            function = getattr(self.library, name)
            function.restype = result
            function.argtypes = arguments
        self.context = self.library.JSGlobalContextCreate(None)

    def close(self):
        self.library.JSGlobalContextRelease(self.context)

    def evaluate(self, source):
        script = self.library.JSStringCreateWithUTF8CString(source.encode())
        exception = ctypes.c_void_p()
        try:
            value = self.library.JSEvaluateScript(self.context, script, None, None, 1, ctypes.byref(exception))
            text = self.library.JSValueToStringCopy(self.context, exception.value or value, None)
            try:
                length = self.library.JSStringGetMaximumUTF8CStringSize(text)
                buffer = ctypes.create_string_buffer(length)
                self.library.JSStringGetUTF8CString(text, buffer, length)
                result = buffer.value.decode()
            finally:
                self.library.JSStringRelease(text)
            if exception.value:
                raise AssertionError(result)
            return result
        finally:
            self.library.JSStringRelease(script)

    def module(self, relative):
        source = (ROOT / relative).read_text()
        source = re.sub(r"^import .*?;\s*$", "", source, flags=re.MULTILINE)
        source = re.sub(r"\bexport\s+(?=(?:class|function|const|async)\b)", "", source)
        self.evaluate(source)


@unittest.skipUnless(sys.platform == "darwin", "Logic-only fallback uses system JavaScriptCore; use Playwright on Linux")
class LogicTests(unittest.TestCase):
    def setUp(self):
        self.javascript = JavaScriptCore()

    def tearDown(self):
        self.javascript.close()

    def test_all_authored_modules_parse(self):
        for relative in ["public/desktop/runtime/gpui.js", "public/desktop/runtime/base.js", "public/desktop/runtime/herdr.js", "public/desktop/runtime/layouts.js", "public/native-images.js"]:
            with self.subTest(module=relative):
                source = (ROOT / relative).read_text()
                source = re.sub(r"^import .*?;\s*$", "", source, flags=re.MULTILINE)
                source = re.sub(r"\bexport\s+(?=(?:class|function|const|async)\b)", "", source)
                self.javascript.evaluate(f"new Function({json.dumps(source)}); 'parsed'")

    def test_delayed_terminal_readiness_does_not_steal_selected_pane_focus(self):
        source = (ROOT / "public/app.js").read_text()
        terminal_class = "class PaneTerminal{" + source.split("class PaneTerminal{", 1)[1].split("\nfunction terminalTheme()", 1)[0]
        result = self.javascript.evaluate(terminal_class + """
            const model = {selected: {pane: 'original'}};
            const document = {querySelectorAll: () => [], querySelector: () => null};
            let active = null;
            function view(pane, ready, preview = false) {
                return Object.assign(Object.create(PaneTerminal.prototype), {
                    pane: {pane}, preview, ready, disposed: false,
                    element: {dataset: {}},
                    terminal: {options: {}, focus: () => { active = pane; }},
                });
            }
            const original = view('original', false);
            original.focus();
            const queued = original.wantsFocus;
            const split = view('split', false);
            model.selected.pane = 'split';
            split.focus();
            split.inputReady(true);
            original.inputReady(true);
            const afterLateReady = active;
            original.focus();
            const afterStaleRequest = active;
            const preview = view('preview', true, true);
            preview.focus();
            JSON.stringify({queued, afterLateReady, afterStaleRequest,
                stalePending: original.wantsFocus, originalReady: original.ready,
                originalInputEnabled: !original.terminal.options.disableStdin,
                previewFocus: active});
        """)
        self.assertEqual(json.loads(result), {
            "queued": True, "afterLateReady": "split", "afterStaleRequest": "split",
            "stalePending": False, "originalReady": True,
            "originalInputEnabled": True, "previewFocus": "preview",
        })

    def test_all_adapted_official_panels_and_application_parse(self):
        sources=list((ROOT/'public/desktop').rglob('*.js'))+[ROOT/'public/app.js']
        for path in sources:
            with self.subTest(module=str(path.relative_to(ROOT))):
                source=re.sub(r'^import\b.*?;\s*','',path.read_text(),flags=re.MULTILINE|re.DOTALL)
                source=re.sub(r'\bexport\s*\{.*?\}\s*;?','',source,flags=re.DOTALL)
                source=re.sub(r'\bexport\s+(?:default\s+)?','',source)
                self.javascript.evaluate(f"new Function({json.dumps('return (async()=>{'+source+'})()')}); 'parsed'")

    def test_layout_validation_preserves_last_saved_layout(self):
        self.javascript.module("public/desktop/runtime/layouts.js")
        result = self.javascript.evaluate("""
            const storage = { value: '{}', getItem() { return this.value; }, setItem(key, value) { this.value = value; } };
            const personal = new PersonalLayouts(storage);
            const valid = { type: 'row', children: [{ type: 'block', id: 'terminal', content: { tab: true } }] };
            personal.save('mine', valid);
            const before = storage.value;
            let failures = 0;
            for (const invalid of [
              { type: 'row', children: [] },
              { type: 'script', children: [] },
              { type: 'block', id: 'terminal', content: { tab: true }, width: Infinity },
              { type: 'row', children: [valid.children[0], valid.children[0]] },
              { type: 'block', id: 'terminal', content: { tab: true }, visible: false },
              { type: 'row', visible: false, children: valid.children },
            ]) { try { personal.save('mine', invalid); } catch { failures++; } }
            JSON.stringify({ failures, preserved: before === storage.value, clone: validateLayout(valid) !== valid });
        """)
        self.assertEqual(json.loads(result), {"failures": 6, "preserved": True, "clone": True})

    def test_layout_count_depth_and_names(self):
        self.javascript.module("public/desktop/runtime/layouts.js")
        result = self.javascript.evaluate("""
            const terminal = { type: 'block', id: 'terminal', content: { tab: true } };
            let deep = terminal;
            for (let level = 0; level < 20; level++) deep = { type: 'row', children: [deep] };
            let rejected = 0;
            try { validateLayout(deep); } catch { rejected++; }
            const many = { type: 'row', children: [terminal, ...Array.from({ length: 200 }, (_, index) => ({ type: 'block', id: `mod-${index}`, content: { mod: 'notes' } }))] };
            try { validateLayout(many); } catch { rejected++; }
            const layouts = new PersonalLayouts({ getItem: () => '{}', setItem: () => {} });
            try { layouts.save('../bad', terminal); } catch { rejected++; }
            String(rejected);
        """)
        self.assertEqual(result, "3")

    def test_image_scene_keys_sizes_and_retention(self):
        self.javascript.module("public/native-images.js")
        result = self.javascript.evaluate("""
            const validScene = { assets: [{key: 'full:opaque:identity:fingerprint', width: 1, height: 1, format: 1, data: '/wAA/w=='}], placements: [], retained: ['full:opaque:identity:fingerprint'] };
            validateScene(validScene);
            let rejected = 0;
            for (const invalid of [
              {...validScene, retained: []},
              {...validScene, assets: [{...validScene.assets[0], width: 9000000}]},
              {...validScene, assets: [{...validScene.assets[0], data: '/wAA'}]},
              {...validScene, assets: [{...validScene.assets[0], data: '!!!!'}]},
            ]) { try { validateScene(invalid); } catch { rejected++; } }
            try { validateViewport({cols: 501, rows: 24, cw: 8, ch: 16}); } catch { rejected++; }
            JSON.stringify({rejected, key: validScene.assets[0].key});
        """)
        self.assertEqual(json.loads(result), {"rejected": 5, "key": "full:opaque:identity:fingerprint"})

    def test_context_coordinates_use_event_then_target(self):
        self.javascript.module("public/desktop/runtime/gpui.js")
        result = self.javascript.evaluate("""
            const target = { getBoundingClientRect: () => ({left: 30, bottom: 80, width: 100}) };
            JSON.stringify([contextAnchor({clientX: 222, clientY: 111}, target), contextAnchor({clientX: 0, clientY: 0}, target)]);
        """)
        self.assertEqual(json.loads(result), [{"x": 222, "y": 111}, {"x": 54, "y": 80}])

    def test_preview_resolves_moved_terminal_without_changing_browser_selection(self):
        self.javascript.evaluate('globalThis.localStorage=globalThis.sessionStorage={getItem:()=>null};')
        self.javascript.module("public/desktop/runtime/herdr.js")
        self.javascript.evaluate('''
            model.snapshot.panes=[{pane_id:"w1:p1",terminal_id:"persistent-terminal"}];
            model.selected={space:"w1",tab:"w1:t2",pane:"w1:p2"};
            globalThis.lookupResult=null;
            globalThis.requested=[];
            globalThis.fetch=(url,options)=>{
                requested.push({url,method:options.method});
                return new Promise(resolve=>{globalThis.finishBackend=resolve;});
            };
            resolveTerminalPane("persistent-terminal").then(pane=>{globalThis.lookupResult=pane;});
        ''')
        self.assertEqual(self.javascript.evaluate('JSON.stringify(lookupResult)'), "null")
        self.javascript.evaluate('''
            finishBackend({ok:true,json:async()=>({snapshot:{panes:[
                {pane_id:"w1:p1",terminal_id:"unrelated-terminal"},
                {pane_id:"w3:p1",terminal_id:"persistent-terminal"}
            ]}})});
        ''')
        result=json.loads(self.javascript.evaluate('JSON.stringify({resolved:lookupResult,cached:model.snapshot.panes,selected:model.selected,requested})'))
        self.assertEqual(result["resolved"], {"pane_id":"w3:p1","terminal_id":"persistent-terminal"})
        self.assertEqual(result["cached"], [{"pane_id":"w1:p1","terminal_id":"persistent-terminal"}])
        self.assertEqual(result["selected"], {"space":"w1","tab":"w1:t2","pane":"w1:p2"})
        self.assertEqual(result["requested"], [{"url":"/api/state","method":"GET"}])

    def test_preview_rejects_deleted_terminal_instead_of_using_stale_mapping(self):
        self.javascript.evaluate('globalThis.localStorage=globalThis.sessionStorage={getItem:()=>null};')
        self.javascript.module("public/desktop/runtime/herdr.js")
        self.javascript.evaluate('''
            model.snapshot.panes=[{pane_id:"w1:p1",terminal_id:"deleted-terminal"}];
            globalThis.lookupResult=null;
            globalThis.fetch=async()=>({ok:true,json:async()=>({snapshot:{panes:[]}})});
            resolveTerminalPane("deleted-terminal").then(
                pane=>{lookupResult={pane};},error=>{lookupResult={error:error.message};});
        ''')
        self.assertEqual(json.loads(self.javascript.evaluate('JSON.stringify(lookupResult)')), {"error":"Terminal no longer exists"})

    def test_preview_propagates_backend_failure_instead_of_using_stale_mapping(self):
        self.javascript.evaluate('globalThis.localStorage=globalThis.sessionStorage={getItem:()=>null};')
        self.javascript.module("public/desktop/runtime/herdr.js")
        self.javascript.evaluate('''
            model.snapshot.panes=[{pane_id:"w1:p1",terminal_id:"persistent-terminal"}];
            globalThis.lookupResult=null;
            globalThis.fetch=async()=>({ok:false,status:503,json:async()=>({error:"Backend unavailable"})});
            resolveTerminalPane("persistent-terminal").then(
                pane=>{lookupResult={pane};},error=>{lookupResult={error:error.message};});
        ''')
        self.assertEqual(json.loads(self.javascript.evaluate('JSON.stringify(lookupResult)')), {"error":"Backend unavailable"})

    def test_native_fluent_transforms_pressed_styles_and_printable_keys(self):
        self.javascript.module("public/desktop/runtime/gpui.js")
        result = self.javascript.evaluate("""
            const control=div().p(2).map(element=>element.h(18)).active(element=>element.pt(3).border_color("#000080"));
            JSON.stringify({height:control.styles.height,padding:control.styles.padding,pressed:control.activeStyle,
                keys:["a","猫","😀","Enter"].map(key=>normalized({key}).key_char)});
        """)
        self.assertEqual(json.loads(result), {"height": "18px", "padding": "2px", "pressed": {"paddingTop": "3px", "borderColor": "#000080"}, "keys": ["a", "猫", "😀", None]})

    def test_browser_wheel_deltas_match_native_content_direction(self):
        self.javascript.module("public/desktop/runtime/gpui.js")
        result = self.javascript.evaluate("""
            JSON.stringify([
                normalized({deltaX: 12, deltaY: 100}).delta,
                normalized({deltaX: -8, deltaY: -3.5}).delta,
            ]);
        """)
        self.assertEqual(json.loads(result), [{"x": -12, "y": -100}, {"x": 8, "y": 3.5}])

    def test_upgraded_picker_keeps_selection_inside_positioned_list(self):
        self.javascript.module("public/desktop/runtime/gpui.js")
        source = (ROOT / "public/desktop/mods/picker/main.js").read_text()
        source = re.sub(r"^import .*?;\s*$", "", source, flags=re.MULTILINE)
        source = source.replace("export default class Picker", "return class Picker")
        self.javascript.evaluate("""
            const pickerAPI = {
                View: class {}, div, svg, v_flex: () => div().flex_col(), h_flex: () => div().flex_row(),
                Input: {new: () => div()}, theme: () => ({}), settings: () => ({zoom: 1}),
                spaces: () => [], agents: () => [], terminals: () => [],
            };
        """)
        self.javascript.evaluate(f"const Picker = new Function(...Object.keys(pickerAPI), {json.dumps(source)})(...Object.values(pickerAPI));")
        result = self.javascript.evaluate("""
            const picker = new Picker();
            Object.assign(picker, {filter: '', input: {}, at: 0, scroll: 0,
                listHeight: 144, machines: Array.from({length: 20}, (_, index) => `machine-${index}`)});
            function find(element, id) {
                if (element.key === id) return element;
                for (const child of element.items || []) {
                    if (child && typeof child === 'object') {
                        const found = find(child, id);
                        if (found) return found;
                    }
                }
            }
            const context = {notify() {}, stop_propagation() {}};
            picker.render().events.keydown({key: 'up'}, context);
            const tree = picker.render();
            const selected = find(tree, 'pk-space-machine-19');
            const list = find(tree, 'pk-list');
            const last = {top: selected.styles.top, bottom: selected.styles.bottom};
            tree.events.keydown({key: 'down'}, context);
            const firstTree = picker.render();
            const first = find(firstTree, 'pk-new');
            find(firstTree, 'pk-list').events.wheel(normalized({deltaX: 0, deltaY: 3.5}), context);
            JSON.stringify({position: list.styles.position, last, first: first.styles.top, scroll: picker.scroll});
        """)
        self.assertEqual(json.loads(result), {"position": "relative", "last": {"top": "112px", "bottom": "0px"}, "first": "0px", "scroll": 3.5})


if __name__ == "__main__":
    unittest.main()
