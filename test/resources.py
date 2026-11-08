#!/usr/bin/env python3
import json
import os
import pathlib
import shlex
import socket
import threading
import time

from harness import BINARY,Fixture,wait_for
from websocket_client import WebSocket


def children(parent):
    result=[]
    for process in pathlib.Path('/proc').iterdir():
        if not process.name.isdigit():continue
        try:
            status=(process/'stat').read_text().rsplit(')',1)[1].split()
            if int(status[1])==parent:result.append(int(process.name))
        except (FileNotFoundError,ProcessLookupError,PermissionError):pass
    return result


def main():
    with Fixture('resources') as fixture:
        created=fixture.rpc('workspace.create',{'label':'Attachment capacity','focus':False})
        panes=[created['root_pane']['pane_id']]
        workspace=created['workspace']['workspace_id']
        for index in range(31):panes.append(fixture.rpc('tab.create',{'workspace_id':workspace,'focus':False})['root_pane']['pane_id'])
        sockets=[]
        try:
            for pane in panes:
                stream=WebSocket(fixture.url,'/terminal/'+pane)
                assert stream.status==101
                stream.ready();sockets.append(stream)
            shared=WebSocket(fixture.url,'/terminal/'+panes[0]);assert shared.status==101;shared.ready();shared.close()
            extra=fixture.rpc('tab.create',{'workspace_id':workspace,'focus':False})['root_pane']['pane_id']
            denied=WebSocket(fixture.url,'/terminal/'+extra);assert denied.status==503;denied.close()
            attachment_pids=children(fixture.web.pid)
            assert len(attachment_pids)==32,attachment_pids
        finally:
            for stream in sockets:stream.close()
        wait_for(lambda:not children(fixture.web.pid),timeout=15)
        stream=WebSocket(fixture.url,'/terminal/'+panes[0]);stream.ready();stream.close()
        wait_for(lambda:not children(fixture.web.pid))
        first=WebSocket(fixture.url,'/terminal/'+panes[0]);first.ready()
        # Another web-server process still competes for Herdr's attach slot.
        from browser import wait_url
        import subprocess
        peer=fixture.process([str(BINARY)],'peer-web.log',stdout=subprocess.PIPE)
        peer_url=wait_url(peer)
        second=WebSocket(peer_url,'/terminal/'+panes[0])
        busy=False
        try:
            while True:
                opcode,payload=second.receive()
                if opcode==1:
                    message=json.loads(payload)
                    assert not message.get('ready'),'Busy attachment advertised input readiness before backend acceptance'
                    if message.get('error'):
                        busy='busy' in message['error'].lower();break
                if opcode==8:break
            assert busy
            first.send({'type':'input','data':"printf 'OWNER_%s\\n' PRESERVED\r"})
            wait_for(lambda:'OWNER_PRESERVED' in fixture.read(panes[0]))
        finally:
            second.close();first.close();peer.terminate();peer.wait(timeout=20)
        wait_for(lambda:not children(fixture.web.pid))
        fixture.web.terminate();fixture.web.wait(timeout=20)
        shim=fixture.root/'herdr-stream'
        python='import os,sys,time,tty;tty.setraw(0);os.write(1,b"\\x1b[?1016$p\\x1b[c");expected=b"\\x1b[?1016;0$y\\x1b[?1;2c";assert sys.stdin.buffer.read(len(expected))==expected;os.write(1,b"\\x1b[?2026h\\x1b[?25lready\\x1b[?2026l");payload=b"\\x1b[?2026h\\x1b[1;1H"+b"x"*16384+b"\\x1b[?2026l"\nwhile True: os.write(1,payload);time.sleep(.002)'
        shim.write_text('#!/bin/sh\nif [ "$1" = terminal ]; then exec /usr/bin/python3 -c '+shlex.quote(python)+'; fi\nexec '+shlex.quote(str(fixture.herdr))+' "$@"\n')
        shim.chmod(0o700)
        fixture.environment['HERDR_BIN']=str(shim)
        from browser import wait_url
        import subprocess
        fixture.web=fixture.process([str(BINARY)],'stalled-web.log',stdout=subprocess.PIPE)
        fixture.url=wait_url(fixture.web)
        def rss():return int(next(line.split()[1] for line in pathlib.Path(f'/proc/{fixture.web.pid}/status').read_text().splitlines() if line.startswith('VmRSS:')))
        before=rss()
        stalled=WebSocket(fixture.url,'/terminal/'+panes[0],receive_buffer=1024);stalled.ready()
        wait_for(lambda:len(children(fixture.web.pid))==1)
        peak=before
        deadline=time.monotonic()+15
        while time.monotonic()<deadline and children(fixture.web.pid):
            peak=max(peak,rss());time.sleep(.1)
        stalled.close()
        assert not children(fixture.web.pid),'Stalled attachment child was not reaped'
        assert peak-before<64*1024,(before,peak)
        assert fixture.rpc('session.snapshot')['snapshot']['panes']
        healthy=WebSocket(fixture.url,'/terminal/'+panes[0]);healthy.ready()
        received=[0];failures=[];finished=threading.Event()
        def drain():
            try:
                while not finished.is_set():
                    opcode,payload=healthy.receive()
                    if opcode==2:received[0]+=len(payload)
                    elif opcode==1:assert not json.loads(payload).get('error'),payload
                    elif opcode==8:raise AssertionError(payload)
            except (OSError,EOFError,AssertionError) as error:
                if not finished.is_set():failures.append(str(error))
        reader=threading.Thread(target=drain);reader.start()
        slow=WebSocket(fixture.url,'/terminal/'+panes[0],receive_buffer=1024);slow.ready()
        owner=children(fixture.web.pid);assert len(owner)==1
        try:
            start=received[0]
            until=time.monotonic()+8
            while time.monotonic()<until:
                peak=max(peak,rss());time.sleep(.1)
            assert not failures,failures
            assert received[0]>start+1024*1024
            # Drain the closed peer after it stalls. The healthy peer continues
            # throughout, so a dead shared child cannot satisfy this assertion.
            slow.socket.setsockopt(socket.SOL_SOCKET,socket.SO_RCVBUF,1024*1024)
            slow.socket.settimeout(5)
            while slow.socket.recv(1024*1024):pass
            assert children(fixture.web.pid)==owner
            after=received[0]
            wait_for(lambda:received[0]>after+65536)
            assert not failures,failures
        finally:
            finished.set();slow.close();healthy.close();reader.join(timeout=6)
        wait_for(lambda:not children(fixture.web.pid))
        assert peak-before<64*1024,(before,peak)
        result={'attachment_capacity_32_and_release':True,'existing_terminal_can_share_at_capacity':True,'busy_without_takeover':True,'busy_never_advertises_ready':True,'existing_owner_accepts_input_after_busy_rejection':True,'stalled_reader_child_reaped':True,'slow_peer_does_not_interrupt_healthy_peer':True,'rss_growth_kib':peak-before,'backpressure_uses_fault_injected_pty_producer':True}
        (fixture.logs/'results.json').write_text(json.dumps(result,indent=2))
        print(json.dumps(result,indent=2),flush=True)


if __name__=='__main__':main()
