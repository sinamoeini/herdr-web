import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]


class RemoteAcceptanceArguments(unittest.TestCase):
    def invoke(self, *args):
        with tempfile.TemporaryDirectory() as directory:
            result = pathlib.Path(directory) / 'result.json'
            environment = {**os.environ, 'HERDR_WEB_BINARY': str(pathlib.Path(directory) / 'missing')}
            process = subprocess.run([sys.executable, str(ROOT/'test/ssh_remote.py'), '--output', str(result), *args],
                                     env=environment, capture_output=True, text=True, timeout=10)
            return process, json.loads(result.read_text()) if result.exists() else None

    def test_absent_target_is_explicitly_untested_without_local_prerequisites(self):
        process, result = self.invoke()
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertEqual(result['status'], 'untested')

    def test_supplied_target_with_missing_binary_is_not_a_pass_or_untested(self):
        process, result = self.invoke('--host', 'private-test-alias')
        self.assertEqual(process.returncode, 2)
        self.assertEqual(result['status'], 'prerequisite_missing')
        self.assertNotIn('private-test-alias', process.stdout)

    def test_invalid_destinations_ports_and_source_are_rejected(self):
        for arguments in [('--host','host;command'), ('--host','a@b@c'), ('--host','user@'),
                          ('--port','0'), ('--port','65536'), ('--remote-source','not-a-sha')]:
            with self.subTest(arguments=arguments):
                process, result = self.invoke(*arguments)
                self.assertEqual(process.returncode, 2)
                self.assertIsNone(result)
