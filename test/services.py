#!/usr/bin/env python3
import base64
import concurrent.futures
import json
import os
import pathlib
import stat
import subprocess
import threading
import time

from harness import BINARY, Fixture, wait_for
from git_identity import run_git_identity


def main():
    evidence = {}
    with Fixture('services') as fixture:
        snapshot = fixture.rpc('session.snapshot')['snapshot']
        assert not snapshot['workspaces']
        first = fixture.post('/api/command', {'method': 'workspace.create', 'params': {'label': 'One', 'cwd': str(fixture.root), 'focus': False}})
        second = fixture.post('/api/command', {'method': 'workspace.create', 'params': {'label': 'Two', 'cwd': str(fixture.root), 'focus': False}})
        pane = first['root_pane']['pane_id']
        terminal = first['root_pane']['terminal_id']
        workspace = first['workspace']['workspace_id']
        tab = first['tab']['tab_id']
        state = fixture.post('/api/command', {'method': 'workspace.rename', 'params': {'workspace_id': workspace, 'label': 'Renamed space'}})
        fixture.post('/api/command', {'method': 'workspace.move', 'params': {'workspace_id': workspace, 'insert_index': 1}})
        fixture.post('/api/command', {'method': 'tab.rename', 'params': {'tab_id': tab, 'label': 'Renamed tab'}})
        added = fixture.post('/api/command', {'method': 'tab.create', 'params': {'workspace_id': workspace, 'label': 'Other tab', 'focus': False}})
        fixture.post('/api/command', {'method': 'tab.move', 'params': {'tab_id': added['tab']['tab_id'], 'insert_index': 0}})
        split = fixture.post('/api/command', {'method': 'pane.split', 'params': {'target_pane_id': pane, 'direction': 'right', 'focus': False}})
        fixture.post('/api/command', {'method': 'layout.set_split_ratio', 'params': {'tab_id': tab, 'path': [], 'ratio': .65}})
        fixture.post('/api/command', {'method': 'pane.rename', 'params': {'pane_id': pane, 'label': 'Named pane'}})
        neighbor = fixture.post('/api/command', {'method': 'pane.neighbor', 'params': {'pane_id': pane, 'direction': 'right'}})
        fixture.post('/api/command', {'method': 'pane.swap', 'params': {'pane_id': pane, 'direction': 'right'}})
        before = fixture.rpc('session.snapshot')['snapshot']
        for request in [
            {'method': 'server.stop', 'params': {}},
            {'method': 'tab.close', 'params': {'tab_id': tab, 'force': True}},
            {'method': 'pane.close', 'params': {'pane_id': 17}},
            {'method': 'pane.split', 'params': {'target_pane_id': pane, 'direction': 'left'}},
            {'method': 'layout.set_split_ratio', 'params': {'tab_id': tab, 'path': [1], 'ratio': .5}},
        ]:
            assert fixture.http('/api/command', request)[0] == 400
        assert len(fixture.rpc('session.snapshot')['snapshot']['panes']) == len(before['panes'])
        evidence['real_workspace_tab_pane_mutations'] = True

        script = 'import json,pathlib,sys;pathlib.Path(sys.argv[1]).write_text(json.dumps(sys.argv[2:]));print("ARGV_DONE",flush=True)'
        output = fixture.root / 'arguments.json'
        arguments = ['space here', "both ' and \" quotes", '', '$HOME', 'semi;colon', '猫']
        fixture.post('/api/terminal-launch', {'pane': pane, 'argv': ['python3', '-c', script, str(output), *arguments]})
        wait_for(output.exists)
        assert json.loads(output.read_text()) == arguments
        assert fixture.http('/api/terminal-launch', {'pane': pane, 'argv': ['a\0b']})[0] == 400
        assert fixture.http('/api/terminal-launch', {'pane': pane, 'argv': ['x'] * 129})[0] == 400
        assert fixture.http('/api/agent-start', {'pane': pane, 'program': 'not-an-installed-agent'})[0] == 400
        evidence['real_shell_argv_quoting'] = True

        worker_pid = fixture.root / 'worker.pid'
        worker_script = 'import os,pathlib,time;pathlib.Path(' + repr(str(worker_pid)) + ').write_text(str(os.getpid()));time.sleep(120)'
        worker_name = 'Worker review ' + 'x' * 100
        child = fixture.post('/api/child-terminal', {'parent': terminal, 'name': worker_name, 'argv': ['python3', '-c', worker_script]})
        grandchild = fixture.post('/api/child-terminal', {'parent': child['terminal'], 'name': '', 'argv': []})
        worker_process = int(wait_for(lambda: worker_pid.read_text()))
        panes = fixture.rpc('session.snapshot')['snapshot']['panes']
        assert next(item for item in panes if item['terminal_id'] == child['terminal'])['tokens']['web_parent'] == terminal
        assert next(item for item in panes if item['terminal_id'] == grandchild['terminal'])['tokens']['web_parent'] == child['terminal']
        tokens = next(item for item in panes if item['terminal_id'] == child['terminal'])['tokens']
        assert tokens['web_name'] + tokens.get('web_name_tail', '') == worker_name
        parked = fixture.post('/api/terminal-action', {'action': 'park', 'terminal': terminal})
        fixture.post('/api/terminal-action', {'action': 'park', 'terminal': terminal})
        panes = fixture.rpc('session.snapshot')['snapshot']['panes']
        current = next(item for item in panes if item['terminal_id'] == terminal)
        fixture.post('/api/terminal-action', {'action': 'keep', 'terminal': terminal, 'space': second['workspace']['workspace_id']})
        fixture.web.terminate()
        fixture.web.wait(timeout=20)
        from browser import wait_url
        fixture.web = fixture.process([str(BINARY)], 'restarted-web.log', stdout=subprocess.PIPE)
        fixture.url = wait_url(fixture.web)
        state = fixture.http('/api/state')[1]['snapshot']['panes']
        assert next(item for item in state if item['terminal_id'] == child['terminal'])['tokens']['web_parent'] == terminal
        assert next(item for item in state if item['terminal_id'] == grandchild['terminal'])['tokens']['web_parent'] == child['terminal']
        os.kill(worker_process, 0)
        assert fixture.http('/api/terminal-action', {'action': 'kill', 'terminal': terminal})[0] == 400
        fixture.post('/api/terminal-action', {'action': 'kill', 'terminal': terminal, 'confirmed': True})
        remaining = fixture.rpc('session.snapshot')['snapshot']['panes']
        assert any(item['terminal_id'] == child['terminal'] for item in remaining)
        assert any(item['terminal_id'] == grandchild['terminal'] for item in remaining)
        os.kill(worker_process, 0)
        evidence['parking_identity_children_and_parent_close'] = True
        evidence['child_process_metadata_and_long_name_survive_web_restart'] = True

        folder = fixture.root / 'watched'
        folder.mkdir()
        (folder / 'hello.txt').write_text('猫 <script>text only</script>')
        assert fixture.post('/api/files', {'action': 'read', 'path': str(folder / 'hello.txt')})['text'].startswith('猫')
        assert fixture.http('/api/files', {'action': 'read', 'path': '/dev/null'})[0] == 400
        fifo = fixture.root / 'fifo'
        os.mkfifo(fifo)
        assert fixture.http('/api/files', {'action': 'read', 'path': str(fifo)})[0] == 400
        (folder / 'binary').write_bytes(b'\0\xff')
        assert fixture.http('/api/files', {'action': 'read', 'path': str(folder / 'binary')})[0] == 400
        def watch(): return fixture.post('/api/watch', {'path': str(folder)})
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            changed = pool.submit(watch)
            time.sleep(.5)
            (folder / 'external').write_text('change')
            assert changed.result(timeout=5)['changed'] is True
        for path in folder.iterdir(): path.unlink()
        folder.rmdir()
        assert fixture.post('/api/watch', {'path': str(folder)}) == {'changed': True, 'missing': True}
        evidence['files_pipes_binary_and_linux_inotify'] = True

        png = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVQIHWP4z8DwHwAFgAI/ScLttAAAAABJRU5ErkJggg==')
        status, uploaded = fixture.http('/api/clipboard-image', raw=png, headers={'Content-Type': 'image/png'}, method='POST')
        assert status == 200, uploaded
        image = pathlib.Path(uploaded['path'])
        assert image.read_bytes() == png
        assert stat.S_IMODE(image.stat().st_mode) == 0o600
        assert stat.S_IMODE(image.parent.stat().st_mode) == 0o700
        assert fixture.http('/api/clipboard-image', raw=png, headers={'Content-Type': 'image/jpeg'}, method='POST')[0] == 400
        image.unlink()
        image.parent.rmdir()
        evidence['image_signatures_and_private_storage'] = True

        repository = fixture.root / 'repo'
        subprocess.run(['git', 'init', '-q', str(repository)], check=True)
        subprocess.run(['git', '-C', str(repository), 'config', 'user.name', 'Herdr Web Test'], check=True)
        subprocess.run(['git', '-C', str(repository), 'config', 'user.email', 'test@example.invalid'], check=True)
        (repository / 'file.txt').write_text('first\n')
        def git(*arguments): return fixture.post('/api/git', {'cwd': str(repository), 'args': list(arguments)})
        git('add', '-A')
        git('commit', '-m', 'diff')
        (repository / 'file.txt').write_text('changed\n')
        assert '+changed' in git('diff', '--no-ext-diff', '--no-textconv', '--no-color', '--', 'file.txt')['stdout']
        assert 'file.txt' in git('status', '--porcelain=v1', '-z')['stdout']
        worktree = fixture.root / 'worktree'
        git('worktree', 'add', '-b', 'web-test', str(worktree), 'HEAD')
        assert worktree.is_dir()
        git('worktree', 'remove', '--', str(worktree))
        assert not worktree.exists()
        assert fixture.http('/api/git', {'cwd': str(repository), 'args': ['diff', '--output=/tmp/rejected']})[0] == 400
        evidence['git_diff_staging_commit_and_worktree'] = True
        evidence['git_identity'] = run_git_identity()

        for host in ['-oProxyCommand=evil', 'bad host', 'user@host;command']:
            assert fixture.http('/api/machines', {'action': 'connect', 'host': host})[0] == 400
        assert fixture.http('/api/machines', {'action': 'connect', 'host': '127.0.0.1', 'port': 0})[0] == 400
        assert fixture.http('/api/machines')[1] == []
        evidence['ssh_argument_rejection'] = True
        (fixture.logs / 'results.json').write_text(json.dumps(evidence, indent=2))
        print(json.dumps(evidence, indent=2), flush=True)


if __name__ == '__main__':
    main()
