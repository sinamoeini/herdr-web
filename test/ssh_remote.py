#!/usr/bin/env python3
"""Opt-in SSH acceptance against an explicitly supplied, isolated remote instance."""

import argparse
import hashlib
import json
import os
import pathlib
import re
import shutil
import socket
import subprocess
import time
import urllib.request
import uuid

from browser import wait_url
from harness import BINARY, ROOT, Fixture, wait_for
from websocket_client import WebSocket


def http(url, path, data=None):
    request = urllib.request.Request(url + path, data=None if data is None else json.dumps(data).encode(),
                                    headers={'Origin': url, 'Content-Type': 'application/json'})
    with urllib.request.urlopen(request, timeout=25) as response:
        body = response.read(8 * 1024 * 1024 + 1)
        assert len(body) <= 8 * 1024 * 1024, 'Remote HTTP response exceeds the test limit'
        return json.loads(body) if 'application/json' in response.headers.get('Content-Type', '') else body


class Bridge:
    """Own a local web process without starting or selecting a local backend."""
    def __init__(self, label, path=None):
        self.fixture = Fixture(label)
        if path:
            self.fixture.environment['PATH'] = path

    def __enter__(self):
        try:
            self.fixture.web = self.fixture.process([str(BINARY), '--diagnostics'], 'web.log', stdout=subprocess.PIPE)
            self.fixture.url = wait_url(self.fixture.web)
            return self.fixture
        except BaseException:
            self.fixture.close()
            raise

    def __exit__(self, *_):
        self.fixture.close()


def reaped(connection):
    def gone():
        try:
            os.kill(connection['pid'], 0)
            return False
        except ProcessLookupError:
            pass
        try:
            with socket.create_connection(('127.0.0.1', connection['local_port']), timeout=.2):
                return False
        except OSError:
            return True
    wait_for(gone)


def roundtrip(url, pane, value=None):
    token = uuid.uuid4().hex[:12]
    stream = None
    output = bytearray()
    def attach():
        nonlocal stream
        if stream:
            stream.close()
        stream = WebSocket(url, '/terminal/' + pane)
        stream.ready()
        return True
    try:
        wait_for(attach)
        prefix = '' if value is None else 'HERDR_SSH_TEST_VALUE=' + value + '; '
        stream.send({'type': 'input', 'data': prefix + "printf '\\nSSH_" + token + ":%s:%s_END\\n' \"$$\" \"$HERDR_SSH_TEST_VALUE\"\r"})
        deadline = time.monotonic() + 15
        pattern = re.compile(('SSH_' + token + r':([0-9]+):([a-f0-9]+)_END').encode())
        while time.monotonic() < deadline:
            opcode, data = stream.receive()
            if opcode == 2:
                output.extend(data)
                assert len(output) <= 2 * 1024 * 1024, 'Terminal output exceeds the test limit'
                # Native terminal redraws can insert cursor/color escapes between cells.
                plain = re.sub(rb'\x1b\[[0-?]*[ -/]*[@-~]', b'', output)
                match = pattern.search(plain)
                if match:
                    return match.groups()
            elif opcode == 8:
                raise AssertionError('Remote terminal closed before its response')
        raise AssertionError('Remote terminal did not return the test marker')
    except Exception:
        # Disposable test terminal only. Raw evidence stays outside the public report.
        logs = ROOT / 'test-results/ssh-remote'
        logs.mkdir(parents=True, exist_ok=True)
        (logs / ('terminal-failure-' + token + '.bin')).write_bytes(output)
        raise
    finally:
        if stream:
            stream.close()


def exercise(bridge, observer, host, port, result):
    """Use a second owned forward to inspect and clean up after bridge shutdown."""
    destination = {'host': host, 'port': port}
    probe = connection = None
    workspace = owned_label = None
    try:
        result['stage'] = 'connect'
        probe = observer.post('/api/machines', {'action': 'connect', **destination})
        remote = probe['url']
        before = http(remote, '/api/state')['snapshot']
        assert before['protocol'] == 22
        version = before['version']
        result['backend'] = version if re.fullmatch(r'[0-9.]+', version) else 'unreported'
        result['protocol'] = before['protocol']
        result['remote_homepage_sha256'] = hashlib.sha256(http(remote, '/')).hexdigest()
        baseline = {pane['terminal_id'] for pane in before['panes']}
        result['stage'] = 'create_disposable_terminal'
        owned_label = 'SSH acceptance ' + uuid.uuid4().hex
        created = http(remote, '/api/command', {'method': 'workspace.create', 'params': {'label': owned_label, 'focus': False}})
        workspace = created['workspace']['workspace_id']
        pane = created['root_pane']['pane_id']
        terminal = created['root_pane']['terminal_id']
        assert terminal not in baseline
        result['stage'] = 'forward_http_and_input'
        connection = bridge.post('/api/machines', {'action': 'connect', **destination})
        assert connection['port'] == port
        assert hashlib.sha256(http(connection['url'], '/')).hexdigest() == result['remote_homepage_sha256']
        assert http(connection['url'], '/api/state')['snapshot']['protocol'] == 22
        marker = uuid.uuid4().hex
        identity = roundtrip(connection['url'], pane, marker)
        assert identity[1].decode() == marker
        result['stage'] = 'disconnect'
        bridge.post('/api/machines', {'action': 'disconnect', **destination})
        reaped(connection)
        assert bridge.http('/api/machines')[1] == []
        assert roundtrip(remote, pane) == identity
        result['stage'] = 'reconnect'
        connection = bridge.post('/api/machines', {'action': 'connect', **destination})
        assert roundtrip(connection['url'], pane) == identity
        result['stage'] = 'local_web_shutdown'
        bridge.web.terminate()
        bridge.web.wait(timeout=20)
        reaped(connection)
        assert roundtrip(remote, pane) == identity
        after = http(remote, '/api/state')['snapshot']
        assert baseline <= {entry['terminal_id'] for entry in after['panes']}
        assert any(entry['terminal_id'] == terminal for entry in after['panes'])
        assert hashlib.sha256(http(remote, '/')).hexdigest() == result['remote_homepage_sha256']
        result.update(forwarded_http_and_terminal_input=True, reconnect=True,
                      local_shutdown_reaps_forward=True, remote_terminal_pid_and_variable_preserved=True,
                      remote_service_responds_after_local_shutdown=True, preexisting_terminal_ids_preserved=True)
    finally:
        # Cleanup uses the independent probe even after the tested web process stops.
        result['cleanup'] = {'remote_workspace': 'pending' if owned_label else 'not created', 'owned_forwards': False}
        try:
            if owned_label and not workspace:
                # A lost create response can hide the ID of our own workspace.
                candidates = [entry for entry in http(probe['url'], '/api/state')['snapshot']['workspaces'] if entry['label'] == owned_label]
                assert len(candidates) <= 1, 'Ambiguous disposable workspace identity'
                workspace = candidates[0]['workspace_id'] if candidates else None
                if not workspace:
                    result['cleanup']['remote_workspace'] = 'create outcome uncertain; inspect isolated remote instance'
            if workspace:
                http(probe['url'], '/api/command', {'method': 'workspace.close', 'params': {'workspace_id': workspace}})
                remaining = http(probe['url'], '/api/state')['snapshot']
                assert all(entry['workspace_id'] != workspace for entry in remaining['workspaces'])
                assert baseline <= {entry['terminal_id'] for entry in remaining['panes']}
                result['cleanup']['remote_workspace'] = 'removed'
        finally:
            if bridge.web.poll() is None:
                bridge.web.terminate()
                bridge.web.wait(timeout=20)
            if connection:
                reaped(connection)
            if probe:
                observer.post('/api/machines', {'action': 'disconnect', **destination})
                reaped(probe)
            result['cleanup']['owned_forwards'] = True
    result.update(status='passed', stage='complete')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host', help='Existing trusted SSH alias or user@host for an isolated test instance')
    parser.add_argument('--port', type=int, default=8004, help='Remote Herdr Web listener port, not the SSH port')
    parser.add_argument('--remote-source', help='Optional remote Herdr Web commit SHA, supplied by its operator')
    parser.add_argument('--output', type=pathlib.Path, default=ROOT / 'test-results/ssh-remote/results.json')
    args = parser.parse_args()
    if args.host and (not re.fullmatch(r'[A-Za-z0-9._][A-Za-z0-9._@-]{0,254}', args.host) or args.host.count('@') > 1 or args.host.endswith('@')):
        parser.error('Use an SSH alias or user@host')
    if not 1 <= args.port <= 65535:
        parser.error('The remote web port must be from 1 to 65535')
    if args.remote_source and not re.fullmatch(r'[a-fA-F0-9]{40}', args.remote_source):
        parser.error('--remote-source must be a full commit SHA')
    result = {'scope': 'separate host supplied by operator', 'status': 'untested',
              'reason': 'No remote test host supplied', 'remote_source_operator_supplied': args.remote_source}
    code = 0
    if args.host:
        if not BINARY.is_file() or not shutil.which('ssh'):
            result.update(status='prerequisite_missing', reason='Build Herdr Web and install OpenSSH before this test')
            code = 2
        else:
            result.pop('reason')
            result['local_binary_sha256'] = hashlib.sha256(BINARY.read_bytes()).hexdigest()
            result['local_source_sha'] = subprocess.check_output(['git','rev-parse','HEAD'], cwd=ROOT, text=True).strip()
            result['local_source_dirty'] = bool(subprocess.check_output(['git','status','--porcelain'], cwd=ROOT))
            try:
                with Bridge('ssh-remote/bridge') as bridge, Bridge('ssh-remote/observer') as observer:
                    exercise(bridge, observer, args.host, args.port, result)
            except Exception as error:
                # Do not publish SSH stderr, host names, credentials, paths, or terminal output.
                result.update(status='failed', error_type=type(error).__name__,
                              reason='The supplied target failed acceptance. Inspect private local logs.')
                code = 1
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2), flush=True)
    return code


if __name__ == '__main__':
    raise SystemExit(main())
