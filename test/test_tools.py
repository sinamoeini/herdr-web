import importlib.util
import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("asset_vendor", ROOT / "tools/vendor.py")
vendor = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(vendor)


class VendorTests(unittest.TestCase):
    def test_digest_is_computed_over_original_bytes(self):
        self.assertEqual(vendor.digest(b"abc"), "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad")

    def test_imports_are_local_without_changing_font_or_binary_files(self):
        source = b'import { View } from "gpui";\nimport \'base\';\nconst api = import("herdr");\n'
        adapted = vendor.adapt_imports(source)
        self.assertIn(b'from "/desktop/runtime/gpui.js"', adapted)
        self.assertIn(b"import '/desktop/runtime/base.js'", adapted)
        self.assertIn(b'import("/desktop/runtime/herdr.js")', adapted)
        self.assertNotEqual(vendor.digest(source), vendor.digest(adapted))
        self.assertEqual(vendor.adapt_imports(adapted), adapted)

    def test_destinations_cannot_overwrite_adapter_or_escape_root(self):
        for path in ["../escape.js", "/tmp/escape.js", "runtime/gpui.js", "mods/../../escape", ""]:
            with self.subTest(path=path), self.assertRaises(ValueError):
                vendor.safe_destination(path)
        self.assertEqual(str(vendor.safe_destination("mods/notes/main.js")), "mods/notes/main.js")

    def test_manifest_identifies_frozen_sources_and_every_planned_asset(self):
        import json
        manifest = json.loads((ROOT / "public/desktop/UPSTREAM.json").read_text())
        plan = json.loads((ROOT / "tools/asset-plan.json").read_text())
        self.assertEqual(manifest["desktop_commit"], vendor.DESKTOP)
        self.assertEqual(manifest["herdr_commit"], vendor.HERDR)
        self.assertEqual(plan["desktop_commit"], vendor.DESKTOP)
        destinations = [entry["destination"] for entry in manifest["files"]]
        self.assertEqual(len(destinations), len(set(destinations)))
        self.assertTrue(destinations)
        self.assertTrue({entry["destination"] for entry in plan["files"]}.issubset(destinations))
        self.assertIn("LICENSE", destinations)
        self.assertIn("themes/palettes.json", destinations)

    def test_copied_assets_have_real_hashes_and_unchanged_font_bytes(self):
        import json
        manifest=json.loads((ROOT/'public/desktop/UPSTREAM.json').read_text())
        for entry in manifest['files']:
            with self.subTest(asset=entry['destination']):
                self.assertEqual(entry['commit'],vendor.DESKTOP)
                self.assertRegex(entry['original_sha256'],r'^[0-9a-f]{64}$')
                content=(ROOT/'public/desktop'/entry['destination']).read_bytes()
                self.assertEqual(vendor.digest(content),entry['adapted_sha256'])
                if pathlib.Path(entry['destination']).suffix in {'.ttf','.otf','.woff','.woff2'}:
                    self.assertEqual(entry['original_sha256'],entry['adapted_sha256'])

    def test_locked_rust_dependencies_retain_original_license_files(self):
        import json
        import tomllib
        manifest = json.loads((ROOT / 'public/vendor/licenses/rust/UPSTREAM.json').read_text())
        locked = tomllib.loads((ROOT / 'Cargo.lock').read_text())
        checksums = {(package['name'], package['version']): package.get('checksum') for package in locked['package']}
        self.assertEqual(manifest['missing_supplied_license_files'], [])
        self.assertTrue(manifest['packages'])
        for package in manifest['packages']:
            with self.subTest(package=package['name'], version=package['version']):
                self.assertRegex(package['archive_checksum'], r'^[0-9a-f]{64}$')
                self.assertEqual(package['archive_checksum'], checksums.get((package['name'], package['version'])), 'Refresh the Linux license inventory after changing Cargo dependencies')
                self.assertTrue(package['files'])
                for notice in package['files']:
                    path = ROOT / 'public/vendor/licenses/rust' / notice['file']
                    self.assertEqual(vendor.digest(path.read_bytes()), notice['sha256'])


if __name__ == "__main__":
    unittest.main()
