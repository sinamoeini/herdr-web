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

    def test_native_fluent_transforms_pressed_styles_and_printable_keys(self):
        self.javascript.module("public/desktop/runtime/gpui.js")
        result = self.javascript.evaluate("""
            const control=div().p(2).map(element=>element.h(18)).active(element=>element.pt(3).border_color("#000080"));
            JSON.stringify({height:control.styles.height,padding:control.styles.padding,pressed:control.activeStyle,
                keys:["a","猫","😀","Enter"].map(key=>normalized({key}).key_char)});
        """)
        self.assertEqual(json.loads(result), {"height": "18px", "padding": "2px", "pressed": {"paddingTop": "3px", "borderColor": "#000080"}, "keys": ["a", "猫", "😀", None]})


if __name__ == "__main__":
    unittest.main()
