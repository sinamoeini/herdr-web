#!/usr/bin/env python3
import json
import os
import pathlib
import shlex
import socket
import subprocess
import pwd
import urllib.request
import urllib.parse
from unittest.mock import patch

from harness import Fixture,wait_for
from websocket_client import WebSocket
from harness import BINARY
from browser import wait_url
from machine_browser import run as machine_browser
from ssh_remote import Bridge, exercise, main as optional_remote


def available_port():
    with socket.socket() as listener:
        listener.bind(('127.0.0.1',0))
        return listener.getsockname()[1]


def main():
    fixture=Fixture('ssh')
    host_key=fixture.root/'host-key';identity=fixture.root/'client-key'
    for key in [host_key,identity]:
        subprocess.run(['/usr/bin/ssh-keygen','-q','-t','ed25519','-N','','-f',str(key)],check=True)
    authorized=fixture.root/'authorized_keys';authorized.write_text(identity.with_suffix('.pub').read_text());authorized.chmod(0o600)
    port=available_port();sshd=fixture.root/'sshd_config'
    user=pwd.getpwuid(os.geteuid()).pw_name
    sshd.write_text(f'Port {port}\nListenAddress 127.0.0.1\nHostKey {host_key}\nPidFile {fixture.root}/sshd.pid\nAuthorizedKeysFile {authorized}\nStrictModes no\nPasswordAuthentication no\nKbdInteractiveAuthentication no\nUsePAM yes\nPermitRootLogin no\nAllowUsers {user}\nAllowTcpForwarding yes\nLogLevel VERBOSE\n')
    known=fixture.root/'known_hosts';known.write_text('')
    config=fixture.root/'ssh_config';alias='herdr-web-test'
    control=fixture.root/'shared-ssh'
    config.write_text(f'Host {alias}\n  HostName 127.0.0.1\n  Port {port}\n  User {user}\n  IdentityFile {identity}\n  IdentitiesOnly yes\n  UserKnownHostsFile {known}\n  ControlMaster auto\n  ControlPath {control}\n  ControlPersist yes\n  ForkAfterAuthentication yes\n')
    executable=fixture.root/'bin';executable.mkdir()
    starts=fixture.root/'ssh-starts'
    wrapper=executable/'ssh';wrapper.write_text('#!/bin/sh\nprintf "start\\n" >> '+shlex.quote(str(starts))+'\nexec /usr/bin/ssh -F '+shlex.quote(str(config))+' "$@"\n');wrapper.chmod(0o700)
    fixture.environment['PATH']=str(executable)+':'+os.environ['PATH']
    with fixture:
        server=fixture.process(['sudo','-n','/usr/sbin/sshd','-D','-e','-f',str(sshd)],'sshd.log')
        def ready():
            assert server.poll() is None,'Isolated sshd exited; inspect sshd.log'
            with socket.create_connection(('127.0.0.1',port),timeout=.5):return True
        wait_for(ready)
        target_port=urllib.parse.urlsplit(fixture.url).port
        status,error=fixture.http('/api/machines',{'action':'connect','host':alias,'port':target_port})
        assert status>=400 and ('host key' in error['error'].lower() or 'host-key' in error['error'].lower()),(status,error)
        assert fixture.http('/api/machines')[1]==[]
        host_public=host_key.with_suffix('.pub').read_text().split()
        known.write_text(f'[127.0.0.1]:{port} {host_public[0]} {host_public[1]}\n')
        unrelated=fixture.process(['/usr/bin/ssh','-F',str(config),'-M','-N','-T','-o','BatchMode=yes','-o','StrictHostKeyChecking=yes','-o','ForkAfterAuthentication=no','-o','ControlPersist=no','-S',str(control),'--',alias],'unrelated-master.log')
        wait_for(lambda:control.exists())
        unavailable=available_port()
        status,error=fixture.http('/api/machines',{'action':'connect','host':alias,'port':unavailable})
        assert status>=400 and fixture.http('/api/machines')[1]==[],(status,error)
        with Fixture('ssh-target') as target:
            preexisting=target.rpc('workspace.create',{'label':'Unrelated remote terminal','focus':False})['root_pane']
            preexisting_pid=target.rpc('pane.process_info',{'pane_id':preexisting['pane_id']})['process_info']['shell_pid']
            second_web=target.process([str(BINARY)],'second-web.log',stdout=subprocess.PIPE)
            second_url=wait_url(second_web)
            browser_results=machine_browser(fixture,alias,[urllib.parse.urlsplit(target.url).port,urllib.parse.urlsplit(second_url).port],starts)
            # Legacy host-only disconnect removes every owned port for that host.
            both=[fixture.post('/api/machines',{'action':'connect','host':alias,'port':urllib.parse.urlsplit(url).port}) for url in [target.url,second_url]]
            fixture.post('/api/machines',{'action':'disconnect','host':alias})
            from ssh_remote import reaped
            for connection in both:reaped(connection)
            assert fixture.http('/api/machines')[1]==[]
            harness_result={'scope':'isolated loopback fixture, not separate-host acceptance'}
            with Bridge('ssh-harness/bridge',fixture.environment['PATH']) as bridge, Bridge('ssh-harness/observer',fixture.environment['PATH']) as observer:
                exercise(bridge,observer,alias,urllib.parse.urlsplit(target.url).port,harness_result)
            assert target.web.poll() is None and target.backend.poll() is None
            baseline={pane['terminal_id'] for pane in target.rpc('session.snapshot')['snapshot']['panes']}
            failed={}
            with Bridge('ssh-harness/failure-bridge',fixture.environment['PATH']) as bridge, Bridge('ssh-harness/failure-observer',fixture.environment['PATH']) as observer:
                with patch('ssh_remote.roundtrip',side_effect=AssertionError('injected test assertion')):
                    try:
                        exercise(bridge,observer,alias,urllib.parse.urlsplit(target.url).port,failed)
                    except AssertionError as error:
                        assert str(error)=='injected test assertion'
                    else:
                        raise AssertionError('The harness did not report the injected failure')
            assert failed['cleanup']=={'remote_workspace':'removed','owned_forwards':True}
            assert baseline=={pane['terminal_id'] for pane in target.rpc('session.snapshot')['snapshot']['panes']}
            assert target.rpc('pane.process_info',{'pane_id':preexisting['pane_id']})['process_info']['shell_pid']==preexisting_pid
            harness_result['assertion_failure_removes_only_test_workspace_and_owned_forwards']=True
        connected=fixture.post('/api/machines',{'action':'connect','host':alias,'port':target_port})
        with urllib.request.urlopen(connected['url']+'/api/state') as response:
            assert json.load(response)['snapshot']['protocol']==22
        pane=fixture.rpc('workspace.create',{'label':'SSH terminal','focus':False})['root_pane']['pane_id']
        stream=WebSocket(connected['url'],'/terminal/'+pane);stream.ready()
        stream.send({'type':'input','data':"printf 'SSH_%s\\n' 'FORWARD_INPUT'\r"})
        wait_for(lambda:'SSH_FORWARD_INPUT' in fixture.read(pane))
        stream.close()
        owned=connected['pid']
        fixture.post('/api/machines',{'action':'disconnect','host':alias})
        wait_for(lambda:not pathlib.Path(f'/proc/{owned}').exists())
        assert fixture.http('/api/machines')[1]==[]
        again=fixture.post('/api/machines',{'action':'connect','host':alias,'port':target_port})
        fixture.web.terminate();fixture.web.wait(timeout=20)
        wait_for(lambda:not pathlib.Path(f'/proc/{again["pid"]}').exists())
        assert 'SSH_FORWARD_INPUT' in fixture.read(pane)
        assert unrelated.poll() is None and control.exists()
        result={'strict_unknown_host_key_rejection':True,'refused_remote_listener_not_registered':True,'real_ssh_loopback_forward_http_and_terminal_input':True,'disconnect_and_shutdown_reap_owned_tunnels':True,'unrelated_ssh_master_survives':True,'machine_dialog':browser_results,'remote_harness_local_exercise':harness_result,'external_remote_host':'not supplied; external topology remains untested'}
        (fixture.logs/'results.json').write_text(json.dumps(result,indent=2))
        print(json.dumps(result,indent=2),flush=True)
    assert optional_remote()==0


if __name__=='__main__':main()
