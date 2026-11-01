import hashlib
import importlib.util
import pathlib
import tarfile
import tempfile
import tomllib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("package_release", ROOT / "tools/package_release.py")
release = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(release)
VERSION = tomllib.loads((ROOT / "server/Cargo.toml").read_text())["package"]["version"]
TAG = f"v{VERSION}"


class ReleaseTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = pathlib.Path(self.temporary.name)
        self.binary = self.directory / "herdr-web"
        header = bytearray(64)
        header[:6] = b"\x7fELF\x02\x01"
        header[18:20] = b"\x3e\x00"
        self.binary.write_bytes(header)
        self.binary.chmod(0o755)

    def test_binary_manifest_is_linkable_without_a_build_hook(self):
        manifest = tomllib.loads(release.plugin_manifest(ROOT, TAG).decode())
        self.assertNotIn("build", manifest)
        self.assertEqual(manifest["version"], VERSION)
        for action in manifest["actions"]:
            self.assertEqual(action["command"], ["./herdr-web", "plugin", action["id"]])

    def test_mismatched_and_unsafe_tags_are_rejected(self):
        for tag in ("v999.999.999", "../escape", "main", f"{TAG}/escape"):
            with self.subTest(tag=tag), self.assertRaises(ValueError):
                release.plugin_manifest(ROOT, tag)

    def test_archive_is_deterministic_and_contains_notices_not_private_files(self):
        first = release.package(ROOT, self.binary, self.directory / "first", TAG)
        second = release.package(ROOT, self.binary, self.directory / "second", TAG)
        self.assertEqual(first.read_bytes(), second.read_bytes())
        digest, name = (first.parent / "SHA256SUMS").read_text().split()
        self.assertEqual(name, first.name)
        self.assertEqual(digest, hashlib.sha256(first.read_bytes()).hexdigest())
        prefix = f"herdr-web-{TAG}-{release.TARGET}/"
        with tarfile.open(first) as bundle:
            entries = bundle.getmembers()
            names = {entry.name.removeprefix(prefix) for entry in entries}
            self.assertIn("herdr-web", names)
            self.assertIn("herdr-plugin.toml", names)
            self.assertTrue(set(release.DOCUMENTS + release.PROVENANCE).issubset(names))
            self.assertIn("public/desktop/fonts/OFL.txt", names)
            self.assertIn("public/desktop/icons/LICENSES.md", names)
            self.assertIn("public/desktop/licenses/CC-BY-SA-3.0.txt", names)
            for notice in (ROOT / "public/vendor/licenses").rglob("*"):
                if notice.is_file():
                    relative = notice.relative_to(ROOT).as_posix()
                    self.assertEqual(bundle.extractfile(prefix + relative).read(), notice.read_bytes())
            self.assertEqual(bundle.getmember(prefix + "herdr-web").mode, 0o755)
            for entry in entries:
                self.assertTrue(entry.name.startswith(prefix))
                self.assertNotIn("..", pathlib.PurePosixPath(entry.name).parts)
                self.assertEqual(entry.uid, 0)
                self.assertEqual(entry.gid, 0)
                self.assertEqual(entry.uname, "")
            for name in names:
                self.assertFalse(name.startswith((".git", ".agents", "test-results", "tools", "server")))
            self.assertNotIn("docs/DEVBOX.md", names)
            packaged = tomllib.loads(bundle.extractfile(prefix + "herdr-plugin.toml").read().decode())
            self.assertNotIn("build", packaged)

    def test_non_linux_wrong_architecture_and_symlink_binaries_are_rejected(self):
        for header in (b"#!/bin/sh\n", b"\x7fELF\x02\x01" + bytes(12) + b"\xb7\x00"):
            self.binary.write_bytes(header)
            with self.assertRaises(ValueError):
                release.package(ROOT, self.binary, self.directory / "invalid", TAG)
        linked = self.directory / "linked"
        linked.symlink_to(self.binary)
        with self.assertRaises(ValueError):
            release.package(ROOT, linked, self.directory / "invalid", TAG)

    def test_existing_release_outputs_are_not_overwritten(self):
        output = self.directory / "existing"
        archive = release.package(ROOT, self.binary, output, TAG)
        original = archive.read_bytes()
        with self.assertRaises(ValueError):
            release.package(ROOT, self.binary, output, TAG)
        self.assertEqual(archive.read_bytes(), original)


if __name__ == "__main__":
    unittest.main()
