import json
import os
import pathlib
import socket
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
import uuid

ROOT = pathlib.Path(__file__).resolve().parents[1]
BINARY = pathlib.Path(os.environ.get('HERDR_WEB_BINARY', ROOT / 'target/release/herdr-web')).expanduser().resolve()


def wait_for(callback, timeout=15):
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        try:
            value = callback()
            if value:
                return value
        except (OSError, ValueError, KeyError, AssertionError) as error:
            last = error
        time.sleep(.1)
    raise AssertionError(f'Timed out waiting for fixture invariant: {last}')


def wait_for_browser(page, expression, arg=None, timeout=15):
    """Wait for the resolved value of a browser predicate, including Promises."""
    return wait_for(lambda: page.evaluate(expression, arg), timeout=timeout)


class Fixture:
    def __init__(self, label):
        self.directory = tempfile.TemporaryDirectory(prefix='hww-')
        self.root = pathlib.Path(self.directory.name)
        self.logs = ROOT / 'test-results' / label
        self.logs.mkdir(parents=True, exist_ok=True)
        self.environment = {key: value for key, value in os.environ.items() if not key.startswith('HERDR_')}
        for name, leaf in [('XDG_CONFIG_HOME', 'c'), ('XDG_STATE_HOME', 's'), ('XDG_DATA_HOME', 'd'), ('XDG_RUNTIME_DIR', 'r')]:
            target = self.root / leaf
            target.mkdir(mode=0o700)
            self.environment[name] = str(target)
        config = self.root / 'config.toml'
        config.write_text('')
        self.name = 'web-' + uuid.uuid4().hex[:10]
        self.herdr = pathlib.Path(os.environ.get('HERDR_BIN', pathlib.Path.home() / '.local/bin/herdr'))
        self.environment.update(HERDR_BIN=str(self.herdr), HERDR_SESSION=self.name, HERDR_CONFIG_PATH=str(config), PORT='0', SHELL=os.environ.get('SHELL', '/bin/bash'))
        self.processes = []
        self.handles = []

    def process(self, argv, name, environment=None, stdout=None):
        handle = open(self.logs / name, 'a')
        self.handles.append(handle)
        process = subprocess.Popen(argv, env=environment or self.environment, stdout=stdout or handle, stderr=handle, text=True, start_new_session=True)
        self.processes.append(process)
        return process

    def start(self):
        from browser import wait_url
        self.backend = self.process([str(self.herdr), '--session', self.name, 'server'], 'backend.log')
        def discover():
            result = subprocess.check_output([str(self.herdr), 'session', 'list', '--json'], env=self.environment)
            return next((entry['socket_path'] for entry in json.loads(result)['sessions'] if entry['name'] == self.name and entry['running']), None)
        self.socket = wait_for(discover)
        self.web = self.process([str(BINARY)], 'web.log', stdout=subprocess.PIPE)
        self.url = wait_url(self.web)
        return self

    def rpc(self, method, params=None):
        with socket.socket(socket.AF_UNIX) as connection:
            connection.settimeout(5)
            connection.connect(self.socket)
            connection.sendall((json.dumps({'id': 'test', 'method': method, 'params': params or {}}) + '\n').encode())
            result = connection.makefile('rb').readline(8 * 1024 * 1024 + 1)
        assert result.endswith(b'\n') and len(result) <= 8 * 1024 * 1024
        envelope = json.loads(result)
        assert 'error' not in envelope, envelope
        return envelope['result']

    def http(self, path, data=None, headers=None, raw=None, method=None):
        body = raw if raw is not None else json.dumps(data).encode() if data is not None else None
        request_headers = {'Origin': self.url, **({'Content-Type': 'application/json'} if data is not None else {}), **(headers or {})}
        request = urllib.request.Request(self.url + path, data=body, headers=request_headers, method=method)
        try:
            response = urllib.request.urlopen(request, timeout=25)
        except urllib.error.HTTPError as error:
            response = error
        content = response.read()
        return response.status, json.loads(content) if 'application/json' in response.headers.get('Content-Type', '') else content

    def post(self, path, data):
        status, value = self.http(path, data)
        assert 200 <= status < 300, (status, value)
        return value

    def read(self, pane):
        return self.rpc('pane.read', {'pane_id': pane, 'source': 'visible', 'format': 'text', 'strip_ansi': True})['read']['text']

    def close(self):
        for process in reversed(self.processes):
            if process.poll() is None:
                process.terminate()
            try:
                process.wait(timeout=20)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        for handle in self.handles:
            handle.close()
        self.directory.cleanup()

    def __enter__(self):
        try:
            return self.start()
        except BaseException:
            self.close()
            raise

    def __exit__(self, *_error):
        self.close()
