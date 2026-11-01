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

from harness import Fixture,wait_for
from websocket_client import WebSocket


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
    config.write_text(f'Host {alias}\n  HostName 127.0.0.1\n  Port {port}\n  User {user}\n  IdentityFile {identity}\n  IdentitiesOnly yes\n  UserKnownHostsFile {known}\n')
    executable=fixture.root/'bin';executable.mkdir()
    wrapper=executable/'ssh';wrapper.write_text('#!/bin/sh\nexec /usr/bin/ssh -F '+shlex.quote(str(config))+' "$@"\n');wrapper.chmod(0o700)
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
        unavailable=available_port()
        status,error=fixture.http('/api/machines',{'action':'connect','host':alias,'port':unavailable})
        assert status>=400 and fixture.http('/api/machines')[1]==[],(status,error)
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
        result={'strict_unknown_host_key_rejection':True,'refused_remote_listener_not_registered':True,'real_ssh_loopback_forward_http_and_terminal_input':True,'disconnect_and_shutdown_reap_owned_tunnels':True,'external_remote_host':'not supplied; external topology remains untested'}
        (fixture.logs/'results.json').write_text(json.dumps(result,indent=2))
        print(json.dumps(result,indent=2),flush=True)


if __name__=='__main__':main()
