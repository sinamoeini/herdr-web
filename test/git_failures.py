#!/usr/bin/env python3
"""Fault-injected Git child supervision; real Git semantics live in services.py."""

import concurrent.futures
import json
import os
import pathlib
import socket
import subprocess
import time
import urllib.parse

from browser import wait_url
from harness import BINARY, Fixture, wait_for


def main():
    with Fixture('git-failures') as fixture:
        fixture.web.terminate()
        fixture.web.wait(timeout=20)
        directory = fixture.root / 'bin'
        directory.mkdir()
        executable = directory / 'git'
        executable.write_text('''#!/usr/bin/python3
import os,pathlib,sys,time
pathlib.Path("owned.pid").write_text(str(os.getpid()))
phase="identity" if "--get-regexp" in sys.argv else "command"
pathlib.Path("phase").write_text(phase)
mode=pathlib.Path("mode").read_text()
if mode=="commit-timeout" and phase=="identity":
    time.sleep(8)
    os.write(1,b"user.name\\nFixture\\0user.email\\nfixture@example.invalid\\0")
    sys.exit(0)
if mode.startswith("identity-"):
    assert phase=="identity"
    mode=mode.removeprefix("identity-")
if mode in ("stdout","stderr"):
    for index in range(32):os.write(1 if mode=="stdout" else 2,b"x"*65536)
else:time.sleep(120)
''')
        executable.chmod(0o700)
        fixture.environment['PATH'] = str(directory) + ':' + os.environ['PATH']
        fixture.web = fixture.process([str(BINARY)], 'fault-web.log', stdout=subprocess.PIPE)
        fixture.url = wait_url(fixture.web)

        def request(label, mode, commit=False):
            path = fixture.root / label
            path.mkdir()
            (path / 'mode').write_text(mode)
            return path, {'cwd': str(path), 'args': ['commit', '-m', 'fixture'] if commit else ['status', '--porcelain=v1', '-z']}

        def reaped(path):
            pid = int((path / 'owned.pid').read_text())
            wait_for(lambda: not pathlib.Path(f'/proc/{pid}').exists())

        for mode in ['stdout', 'stderr', 'timeout', 'identity-stdout', 'identity-stderr', 'identity-timeout', 'commit-timeout']:
            path, data = request(mode, mode, commit='-' in mode)
            started = time.monotonic()
            status, result = fixture.http('/api/git', data)
            assert status >= 400, (status, result)
            assert ('15 seconds' if mode.endswith('timeout') else 'one MiB') in result['error'], result
            assert time.monotonic() - started < 20
            reaped(path)

        for mode in ['timeout', 'identity-timeout', 'commit-timeout']:
            path, data = request('cancel-' + mode, mode, commit='-' in mode)
            parsed = urllib.parse.urlsplit(fixture.url)
            connection = socket.create_connection((parsed.hostname, parsed.port), timeout=3)
            body = json.dumps(data).encode()
            connection.sendall((f'POST /api/git HTTP/1.1\r\nHost: {parsed.netloc}\r\nOrigin: {fixture.url}\r\nContent-Type: application/json\r\nContent-Length: {len(body)}\r\n\r\n').encode() + body)
            wait_for(lambda: (path / 'phase').read_text() == ('identity' if mode.startswith('identity-') else 'command'))
            connection.close()
            reaped(path)

        jobs = [request('parallel-' + str(index), 'timeout', commit=index % 2 == 0) for index in range(4)]
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            futures = [pool.submit(fixture.http, '/api/git', data) for _, data in jobs]
            for path, _ in jobs:
                wait_for(lambda: (path / 'owned.pid').exists())
            rejected, data = request('fifth', 'timeout')
            assert fixture.http('/api/git', data)[0] == 429
            assert not (rejected / 'owned.pid').exists()
            fixture.web.terminate()
            fixture.web.wait(timeout=20)
            for path, _ in jobs:
                reaped(path)
            for future in futures:
                try:
                    status, result = future.result(timeout=5)
                    assert status >= 400, (status, result)
                except (ConnectionError, OSError):
                    pass

        result = {'fault_injected_git': True, 'stdout_and_stderr_overflow_reaped': True, 'timeout_15s_reaped': True, 'request_cancellation_reaped': True, 'capacity_four_and_shutdown_reaped': True,
                  'identity_probe_overflow_timeout_and_cancellation_reaped': True,
                  'commit_after_identity_timeout_and_cancellation_reaped': True,
                  'identity_and_commit_share_the_15_second_deadline': True}
        (fixture.logs / 'results.json').write_text(json.dumps(result, indent=2))
        print(json.dumps(result, indent=2), flush=True)


if __name__ == '__main__':
    main()
