import contextlib
import importlib.util
import io
import json
import pathlib
import subprocess
import unittest
from unittest.mock import Mock, patch

ROOT = pathlib.Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("docker_entrypoint", ROOT / "tools/docker-entrypoint.py")
entrypoint = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(entrypoint)


class DockerEntrypointTests(unittest.TestCase):
    def setUp(self):
        self.error_output = io.StringIO()
        self.enterContext(contextlib.redirect_stderr(self.error_output))

    def backend(self):
        backend = Mock()
        backend.poll.return_value = None
        backend.args = ["herdr"]
        backend.returncode = 1
        return backend

    def test_backend_readiness_requires_exact_running_session(self):
        catalogs = [
            {"sessions": [{"name": "other", "running": True}]},
            {"sessions": [{"name": "default", "running": False}]},
            {"sessions": [{"name": "default", "running": True}]},
        ]
        results = [Mock(stdout=json.dumps(catalog)) for catalog in catalogs]
        with patch.object(entrypoint.subprocess, "run", side_effect=results) as run, patch.object(entrypoint.time, "sleep"):
            entrypoint.wait_for_backend(self.backend(), "herdr", "default")
        self.assertEqual(run.call_count, 3)
        self.assertEqual(run.call_args.args[0], ["herdr", "session", "list", "--json"])

    def test_dead_backend_fails_without_launching_web(self):
        backend = self.backend()
        backend.poll.return_value = 1
        with self.assertRaisesRegex(RuntimeError, "backend exited"):
            entrypoint.wait_for_backend(backend, "herdr", "default")

    def test_backend_readiness_has_a_deadline(self):
        with patch.object(entrypoint.time, "monotonic", side_effect=[0, 31]):
            with self.assertRaisesRegex(RuntimeError, "did not become ready"):
                entrypoint.wait_for_backend(self.backend(), "herdr", "default")

    def test_transient_discovery_failures_are_retried(self):
        result = Mock(stdout='{"sessions":[{"name":"default","running":true}]}')
        failures = [subprocess.TimeoutExpired("herdr", 2), Mock(stdout="not JSON"), result]
        with patch.object(entrypoint.subprocess, "run", side_effect=failures), patch.object(entrypoint.time, "sleep"):
            entrypoint.wait_for_backend(self.backend(), "herdr", "default")

    def test_cleanup_escalates_only_after_grace_period(self):
        process = self.backend()
        process.wait.side_effect = [subprocess.TimeoutExpired("herdr", 7), 0]
        entrypoint.stop(process)
        process.terminate.assert_called_once()
        process.kill.assert_called_once()
        self.assertEqual(process.wait.call_count, 2)

    def test_already_exited_child_is_reaped_without_signaling(self):
        process = self.backend()
        process.poll.return_value = 1
        entrypoint.stop(process)
        process.terminate.assert_not_called()
        process.kill.assert_not_called()
        process.wait.assert_called_once_with(timeout=7)

    def test_child_failure_cleans_up_web_before_backend(self):
        backend, web = self.backend(), self.backend()
        web.poll.return_value = 1
        with patch.object(entrypoint.sys, "argv", ["entrypoint"]), \
                patch.object(entrypoint.signal, "signal"), \
                patch.object(entrypoint.subprocess, "Popen", side_effect=[backend, web]), \
                patch.object(entrypoint, "wait_for_backend"), \
                patch.object(entrypoint, "stop") as stop:
            self.assertEqual(entrypoint.main(), 1)
        self.assertEqual([call.args[0] for call in stop.call_args_list], [web, backend])
        self.assertIn('Container child herdr exited with status 1', self.error_output.getvalue())

    def test_custom_command_does_not_start_backend(self):
        with patch.object(entrypoint.sys, "argv", ["entrypoint", "herdr-web", "--help"]), \
                patch.object(entrypoint.os, "execvp", side_effect=SystemExit(0)) as execute, \
                patch.object(entrypoint.subprocess, "Popen") as start:
            with self.assertRaises(SystemExit):
                entrypoint.main()
        execute.assert_called_once_with("herdr-web", ["herdr-web", "--help"])
        start.assert_not_called()

    def test_shutdown_during_readiness_cleans_up_backend(self):
        backend = self.backend()
        with patch.object(entrypoint.sys, "argv", ["entrypoint"]), \
                patch.object(entrypoint.signal, "signal"), \
                patch.object(entrypoint.subprocess, "Popen", return_value=backend) as start, \
                patch.object(entrypoint, "wait_for_backend", side_effect=SystemExit(0)), \
                patch.object(entrypoint, "stop") as stop:
            with self.assertRaises(SystemExit) as stopped:
                entrypoint.main()
        self.assertEqual(stopped.exception.code, 0)
        start.assert_called_once()
        stop.assert_called_once_with(backend)

    def test_failed_readiness_reaps_owned_backend(self):
        backend = self.backend()
        with patch.object(entrypoint.sys, 'argv', ['entrypoint']), \
                patch.object(entrypoint.signal, 'signal'), \
                patch.object(entrypoint.subprocess, 'Popen', return_value=backend), \
                patch.object(entrypoint, 'wait_for_backend', side_effect=RuntimeError('startup failed')), \
                patch.object(entrypoint, 'stop') as stop:
            self.assertEqual(entrypoint.main(), 1)
        stop.assert_called_once_with(backend)
        self.assertIn('startup failed', self.error_output.getvalue())

    def test_missing_web_executable_reaps_backend(self):
        backend = self.backend()
        with patch.object(entrypoint.sys, 'argv', ['entrypoint']), \
                patch.object(entrypoint.signal, 'signal'), \
                patch.object(entrypoint.subprocess, 'Popen', side_effect=[backend, FileNotFoundError('missing web binary')]), \
                patch.object(entrypoint, 'wait_for_backend'), \
                patch.object(entrypoint, 'stop') as stop:
            self.assertEqual(entrypoint.main(), 1)
        stop.assert_called_once_with(backend)
        self.assertIn('missing web binary', self.error_output.getvalue())


if __name__ == "__main__":
    unittest.main()
