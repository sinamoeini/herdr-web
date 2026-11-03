"""Exercise the real image, default port, backend, and process supervision."""

import json
import pathlib
import socket
import subprocess
import time
import urllib.error
import urllib.request
import urllib.parse
import uuid

from websocket_client import WebSocket

ROOT = pathlib.Path(__file__).resolve().parents[1]
ARTIFACTS = ROOT / 'test-results/docker'


def docker(*arguments):
    return subprocess.check_output(["docker", *arguments], text=True).strip()


def inspect(name):
    return json.loads(docker("inspect", name))[0]


def wait_for(callback, timeout=60):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if callback():
                return
        except (OSError, ValueError):
            pass
        time.sleep(0.2)
    raise AssertionError("Container did not reach the expected state")


def capture(name, label):
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    for command, suffix in [('logs', 'log'), ('inspect', 'json')]:
        result = subprocess.run(['docker', command, name], text=True, capture_output=True, check=False)
        (ARTIFACTS / f'{label}.{suffix}').write_text(result.stdout + result.stderr)


def state(url):
    with urllib.request.urlopen(url + '/api/state', timeout=3) as response:
        return json.load(response)


def post(url, path, value):
    request = urllib.request.Request(url + path, data=json.dumps(value).encode(), headers={
        'Origin': url, 'Content-Type': 'application/json',
    })
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.load(response)


def start(name, volume, port=8000):
    arguments = ['run', '--detach', '--name', name, '--stop-timeout', '20',
                 '--volume', f'{volume}:/home/herdr', '--publish', f'127.0.0.1::{port}']
    if port != 8000:
        arguments.extend(['--env', f'PORT={port}'])
    docker(*arguments, 'herdr-web:ci')
    return address(name, port)


def address(name, port=8000):
    binding = inspect(name)['NetworkSettings']['Ports'][f'{port}/tcp'][0]
    assert binding['HostIp'] == '127.0.0.1', binding
    return f'http://127.0.0.1:{binding["HostPort"]}'


def ready(name, url):
    status = inspect(name)['State']
    assert status['Running'], status
    snapshot = state(url)['snapshot']
    return snapshot['protocol'] == 22 and isinstance(snapshot['panes'], list)


def terminal_round_trip(url):
    created = post(url, '/api/command', {'method': 'workspace.create', 'params': {
        'cwd': '/home/herdr', 'label': 'Docker smoke', 'focus': False,
    }})
    pane = created['root_pane']['pane_id']
    terminal = WebSocket(url, '/terminal/' + urllib.parse.quote(pane, safe=''))
    try:
        assert terminal.status == 101, terminal.status
        terminal.ready()
        terminal.send({'type': 'input', 'data': "printf 'DOCKER_%s\\n' TERMINAL_OK\r"})
        output = bytearray()
        deadline = time.monotonic() + 15
        while b'DOCKER_TERMINAL_OK' not in output and time.monotonic() < deadline:
            try:
                opcode, payload = terminal.receive()
            except socket.timeout:
                continue
            if opcode == 1:
                message = json.loads(payload)
                assert 'error' not in message, message
            elif opcode == 2:
                output.extend(payload)
                assert len(output) <= 256 * 1024, 'Excessive terminal output'
            elif opcode == 8:
                raise AssertionError(f'Terminal closed before command output: {payload!r}')
        assert b'DOCKER_TERMINAL_OK' in output, output[-4096:]
    finally:
        terminal.close()


def run():
    name = "herdr-web-smoke-" + uuid.uuid4().hex[:10]
    volume = name + '-home'
    successful = False
    try:
        assert 'Default listener: http://127.0.0.1:8000' in docker('run', '--rm', 'herdr-web:ci', 'herdr-web', '--help')
        docker('volume', 'create', volume)
        url = start(name, volume)
        wait_for(lambda: ready(name, url))
        with urllib.request.urlopen(url + "/", timeout=2) as response:
            assert response.status == 200
            assert b"<html" in response.read().lower()
        for notice in ('desktop/LICENSE', 'vendor/licenses/rust/UPSTREAM.json'):
            with urllib.request.urlopen(url + '/' + notice, timeout=3) as response:
                assert response.read() == (ROOT / 'public' / notice).read_bytes(), notice
        for headers in [{"Host": "evil.example"}, {"Origin": "http://evil.example"}]:
            request = urllib.request.Request(url + "/api/state", headers=headers)
            try:
                urllib.request.urlopen(request, timeout=2)
            except urllib.error.HTTPError as error:
                assert error.code == 403, error
            else:
                raise AssertionError("Container bypassed Host/Origin validation")
        wait_for(lambda: inspect(name)["State"]["Health"]["Status"] == "healthy")
        assert docker("exec", name, "id", "-u") == "10001"
        terminal_round_trip(url)
        docker('exec', name, 'sh', '-c', "printf 'persisted-home' > /home/herdr/docker-smoke-persist")
        docker("stop", "--time", "20", name)
        assert inspect(name)["State"]["ExitCode"] == 0, inspect(name)["State"]
        docker("start", name)
        url = address(name)
        wait_for(lambda: ready(name, url))
        docker('exec', name, 'pkill', '-TERM', '-f', '^/usr/local/bin/herdr --session default server$')
        wait_for(lambda: not inspect(name)['State']['Running'])
        assert inspect(name)['State']['ExitCode'] != 0, inspect(name)['State']
        capture(name, 'expected-backend-exit')
        docker('rm', name)
        url = start(name, volume, port=9000)
        wait_for(lambda: ready(name, url))
        wait_for(lambda: inspect(name)['State']['Health']['Status'] == 'healthy')
        assert docker('exec', name, 'cat', '/home/herdr/docker-smoke-persist') == 'persisted-home'
        docker("exec", name, "pkill", "-TERM", "-x", "herdr-web")
        wait_for(lambda: not inspect(name)["State"]["Running"])
        assert inspect(name)["State"]["ExitCode"] != 0, inspect(name)["State"]
        successful = True
        print("Docker port 8000, real terminal input/output, localhost security, non-root runtime, health, clean restart, volume persistence, port override, and both child-exit cleanup paths passed.")
    finally:
        capture(name, 'expected-web-exit' if successful else 'failure')
        if not successful:
            print((ARTIFACTS / 'failure.log').read_text(), flush=True)
        subprocess.run(["docker", "rm", "--force", name], check=successful, stdout=subprocess.DEVNULL)
        subprocess.run(['docker', 'volume', 'rm', volume], check=successful, stdout=subprocess.DEVNULL)


if __name__ == "__main__":
    run()
