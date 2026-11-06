import contextlib
import importlib.util
import io
import json
import pathlib
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("run_acceptance", ROOT / "tools/run_acceptance.py")
acceptance = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(acceptance)


class AcceptanceRunnerTests(unittest.TestCase):
    def setUp(self):
        self.output = io.StringIO()
        self.enterContext(contextlib.redirect_stdout(self.output))

    def test_failed_suite_is_reported_and_later_suites_still_run(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = pathlib.Path(temporary)
            commands = [
                ("failure", [sys.executable, "-c", "print('failure evidence'); raise SystemExit(7)"]),
                ("success", [sys.executable, "-c", "print('later suite executed')"]),
            ]
            self.assertEqual(acceptance.run_suites(commands, output), 1)
            report = json.loads((output / "summary.json").read_text())
            self.assertEqual([suite["status"] for suite in report["suites"]], ["failed", "passed"])
            self.assertEqual([suite["exit_code"] for suite in report["suites"]], [7, 0])
            self.assertIn("failure evidence", (output / "failure.log").read_text())
            self.assertIn("later suite executed", (output / "success.log").read_text())
            self.assertIn("Failed suites: failure", self.output.getvalue())

    def test_missing_command_fails_instead_of_skipping(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = pathlib.Path(temporary)
            self.assertEqual(acceptance.run_suites([("missing", [str(output / "nonexistent")])], output), 1)
            suite = json.loads((output / "summary.json").read_text())["suites"][0]
            self.assertEqual(suite["status"], "failed")
            self.assertIsNone(suite["exit_code"])
            self.assertIn("error", suite)
            self.assertIn("Failed suites: missing", self.output.getvalue())

    def test_all_successful_suites_return_success(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = pathlib.Path(temporary)
            self.assertEqual(acceptance.run_suites([("success", [sys.executable, "-c", "pass"])], output), 0)

    def test_inventory_includes_native_lifecycle_and_headed_permissions(self):
        self.assertEqual(len(acceptance.SUITES), 14)
        self.assertEqual(len(set(acceptance.SUITES)), len(acceptance.SUITES))
        self.assertTrue({"integration", "ui", "terminal_selection", "terminal_scroll", "graphics_browser", "view_edges", "plugin", "ssh", "permissions"}.issubset(acceptance.SUITES))
        for name in acceptance.SUITES:
            self.assertTrue((ROOT / "test" / f"{name}.py").is_file())
        self.assertEqual(acceptance.command_for("permissions")[:3], ["xvfb-run", "-a", sys.executable])
        self.assertEqual(acceptance.command_for("plugin"), [sys.executable, str(ROOT / "test/plugin.py")])


if __name__ == "__main__":
    unittest.main()
