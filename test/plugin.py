#!/usr/bin/env python3
"""Native Herdr plugin lifecycle tests using isolated XDG/configuration paths."""

import argparse
import pathlib
import tomllib
import concurrent.futures
import json
import os
import socket
import subprocess
import shutil
import sys
import tempfile
import time
import urllib.request
import uuid

ROOT = pathlib.Path(__file__).resolve().parents[1]

def wait(callback, seconds=15):
    deadline=time.monotonic()+seconds
    while time.monotonic()<deadline:
        try:
            result=callback()
            if result:return result
        except (OSError,ValueError,KeyError):pass
        time.sleep(.15)
    raise AssertionError('Timed out waiting for plugin lifecycle invariant')

def integration():
    artifacts=ROOT/'test-results/plugin';artifacts.mkdir(parents=True,exist_ok=True)
    herdr=pathlib.Path(os.environ.get('HERDR_BIN',pathlib.Path.home()/'.local/bin/herdr'))
    with tempfile.TemporaryDirectory(prefix='hwp-') as temporary:
        base=pathlib.Path(temporary)
        environment={key:value for key,value in os.environ.items() if not key.startswith('HERDR_')}
        for key,leaf in [('XDG_CONFIG_HOME','c'),('XDG_STATE_HOME','s'),('XDG_DATA_HOME','d'),('XDG_RUNTIME_DIR','r')]:
            path=base/leaf;path.mkdir(mode=0o700);environment[key]=str(path)
        config=base/'config.toml';config.write_text('')
        name='plugin-'+uuid.uuid4().hex[:8]
        environment.update(HERDR_CONFIG_PATH=str(config),HERDR_SESSION=name,SHELL='/bin/bash')
        backend=subprocess.Popen([str(herdr),'--session',name,'server'],env=environment,stdout=open(artifacts/'backend.log','w'),stderr=subprocess.STDOUT,start_new_session=True)
        owned=[]
        orphan_pids=[]
        try:
            def discover():
                catalog=json.loads(subprocess.check_output([str(herdr),'session','list','--json'],env=environment))
                return next((value['socket_path'] for value in catalog['sessions'] if value['name']==name and value['running']),None)
            selected=wait(discover)
            def rpc(method,params):
                with socket.socket(socket.AF_UNIX) as connection:
                    connection.settimeout(5);connection.connect(selected);connection.sendall((json.dumps({'id':'plugin-test','method':method,'params':params})+'\n').encode())
                    data=bytearray()
                    while not data.endswith(b'\n'):
                        chunk=connection.recv(65536)
                        if not chunk:raise AssertionError('Backend response ended early')
                        data.extend(chunk)
                    value=json.loads(data);assert 'error' not in value,value;return value['result']
            rpc('plugin.link',{'path':str(ROOT),'enabled':True})
            config_dir=pathlib.Path(subprocess.check_output([str(herdr),'plugin','config-dir','local.herdr-web'],env=environment,text=True).strip())
            (config_dir/'port').write_text('0\n')
            state=pathlib.Path(environment['XDG_STATE_HOME'])/'herdr/plugins/local.herdr-web'
            def control(action='status'):
                paths=list(state.glob('*.sock'))
                if len(paths)!=1:return None
                with socket.socket(socket.AF_UNIX) as connection:
                    connection.settimeout(3);connection.connect(str(paths[0]));connection.sendall((action+'\n').encode());return json.loads(connection.makefile('rb').readline(4097))
            def invoke(action):return rpc('plugin.action.invoke',{'plugin_id':'local.herdr-web','action_id':action})
            with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:list(pool.map(lambda _:invoke('start'),range(4)))
            initial=wait(control);assert initial['running'] is True
            first_pid=initial['pid']
            time.sleep(2)
            assert control()['pid']==first_pid
            space=rpc('workspace.create',{'label':'Plugin persistence','focus':False})
            pane=space['root_pane']['pane_id'];terminal=space['root_pane']['terminal_id']
            rpc('pane.send_text',{'pane_id':pane,'text':'export PLUGIN_KEEP=still_here; printf "KEEP=%s:%s\\n" "$$" "$PLUGIN_KEEP"'})
            rpc('pane.send_keys',{'pane_id':pane,'keys':['enter']})
            def read():return rpc('pane.read',{'pane_id':pane,'source':'visible','format':'text','strip_ansi':True})['read']['text']
            import re
            before=wait(lambda:re.findall(r'KEEP=(\d+):still_here',read()))[-1]
            invoke('restart')
            restarted=wait(lambda:(value if (value:=control()) and value['pid']!=first_pid else None))
            rpc('pane.send_text',{'pane_id':pane,'text':'printf "AGAIN=%s:%s\\n" "$$" "$PLUGIN_KEEP"'})
            rpc('pane.send_keys',{'pane_id':pane,'keys':['enter']})
            wait(lambda:f'AGAIN={before}:still_here' in read())
            invoke('stop');wait(lambda:not list(state.glob('*.sock')))
            plugin_env={**environment,'HERDR_PLUGIN_ID':'local.herdr-web','HERDR_SOCKET_PATH':selected,'HERDR_BIN_PATH':str(herdr),'HERDR_PLUGIN_ROOT':str(ROOT),'HERDR_PLUGIN_CONFIG_DIR':str(config_dir),'HERDR_PLUGIN_STATE_DIR':str(state),'HERDR_SESSION':'wrong-and-nonexistent'}
            direct=subprocess.Popen([str(ROOT/'target/release/herdr-web'),'plugin','start'],env=plugin_env,stdout=open(artifacts/'context.log','w'),stderr=subprocess.STDOUT,start_new_session=True);owned.append(direct)
            status=wait(control)
            with urllib.request.urlopen(status['url']+'/api/state') as response:snapshot=json.load(response)['snapshot']
            assert any(value['terminal_id']==terminal for value in snapshot['panes'])
            rpc('plugin.disable',{'plugin_id':'local.herdr-web'});direct.wait(timeout=10);wait(lambda:not list(state.glob('*.sock')))
            rpc('plugin.enable',{'plugin_id':'local.herdr-web'})
            invoke('start');wait(control);rpc('plugin.unlink',{'plugin_id':'local.herdr-web'});wait(lambda:not list(state.glob('*.sock')))
            rpc('plugin.link',{'path':str(ROOT),'enabled':True})
            with socket.socket() as occupied:
                occupied.bind(('127.0.0.1',0));occupied.listen(1)
                (config_dir/'port').write_text(str(occupied.getsockname()[1]))
                failed=subprocess.Popen([str(ROOT/'target/release/herdr-web'),'plugin','start'],env=plugin_env,stdout=open(artifacts/'occupied.log','w'),stderr=subprocess.STDOUT,start_new_session=True);owned.append(failed)
                assert failed.wait(timeout=10)!=0
                assert not list(state.glob('*.sock'))
                with socket.create_connection(occupied.getsockname(),timeout=2):pass
            (config_dir/'port').write_text('0\n')
            key=14695981039346656037
            for byte in os.fsencode(pathlib.Path(selected).resolve()):key=((key^byte)*1099511628211)&((1<<64)-1)
            stale=state/f'{key:016x}.sock'
            with socket.socket(socket.AF_UNIX) as abandoned:abandoned.bind(str(stale))
            invoke('start');recovered=wait(control)
            assert recovered['running'] is True
            invoke('stop');wait(lambda:not list(state.glob('*.sock')))
            child_file=base/'parent-child.pid'
            parent_code='import pathlib,subprocess,sys,time; child=subprocess.Popen(sys.argv[1:]);pathlib.Path('+repr(str(child_file))+').write_text(str(child.pid));time.sleep(120)'
            parent=subprocess.Popen([sys.executable,'-c',parent_code,str(ROOT/'target/release/herdr-web'),'plugin','start'],env=plugin_env,stdout=open(artifacts/'parent.log','w'),stderr=subprocess.STDOUT,start_new_session=True);owned.append(parent)
            parented=wait(control);orphan_pids.append(parented['pid'])
            assert int(child_file.read_text())==parented['pid']
            parent.terminate();parent.wait(timeout=5)
            wait(lambda:not pathlib.Path(f'/proc/{parented["pid"]}').exists(),seconds=10)
            wait(lambda:not list(state.glob('*.sock')))
            invoke('start');old_root=wait(control);orphan_pids.append(old_root['pid'])
            replacement=base/'replacement';(replacement/'target/release').mkdir(parents=True)
            shutil.copy2(ROOT/'herdr-plugin.toml',replacement/'herdr-plugin.toml')
            shutil.copy2(ROOT/'build.sh',replacement/'build.sh')
            (replacement/'target/release/herdr-web').symlink_to(ROOT/'target/release/herdr-web')
            rpc('plugin.link',{'path':str(replacement),'enabled':True})
            wait(lambda:not pathlib.Path(f'/proc/{old_root["pid"]}').exists(),seconds=10)
            wait(lambda:not list(state.glob('*.sock')))
            assert any(value['terminal_id']==terminal for value in rpc('session.snapshot',{})['snapshot']['panes'])
            rpc('plugin.link',{'path':str(ROOT),'enabled':True})
            invoke('start');lost_backend=wait(control);orphan_pids.append(lost_backend['pid'])
            backend.terminate();backend.wait(timeout=10)
            wait(lambda:not pathlib.Path(f'/proc/{lost_backend["pid"]}').exists(),seconds=10)
            wait(lambda:not list(state.glob('*.sock')))
            result={'backend':subprocess.check_output([str(herdr),'--version'],text=True).strip(),'concurrent_start_pid':first_pid,'restart_pid':restarted['pid'],'preserved_shell_pid':before,'context_socket_override':True,'disable':True,'unlink':True,'stop':True,'occupied_listener_preserved':True,'stale_control_socket_recovered':True,'parent_loss':True,'backend_loss':True,'registration_root_change':True}
            (artifacts/'results.json').write_text(json.dumps(result,indent=2));print(json.dumps(result,indent=2))
        finally:
            for pid in orphan_pids:
                try:os.kill(pid,15)
                except ProcessLookupError:pass
            for process in reversed(owned+[backend]):
                if process.poll() is None:process.terminate()
                try:process.wait(timeout=10)
                except subprocess.TimeoutExpired:process.kill();process.wait()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest-only", action="store_true")
    arguments = parser.parse_args()
    if not arguments.manifest_only:
        integration()
        return
    manifest = tomllib.loads((ROOT / "herdr-plugin.toml").read_text())
    assert manifest["id"] == "local.herdr-web"
    assert manifest["platforms"] == ["linux"]
    assert manifest["build"] == [{"command": ["./build.sh"]}]
    assert {action["id"] for action in manifest["actions"]} == {"start", "stop", "restart", "status"}
    for action in manifest["actions"]:
        assert action["command"] == ["./target/release/herdr-web", "plugin", action["id"]]
    print("Plugin manifest shape passed. No Herdr plugin lifecycle behavior was tested.")


if __name__ == "__main__":
    main()
