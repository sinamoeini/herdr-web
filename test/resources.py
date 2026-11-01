#!/usr/bin/env python3
import json
import os
import pathlib
import shlex
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
            denied=WebSocket(fixture.url,'/terminal/'+panes[0]);assert denied.status==503;denied.close()
            attachment_pids=children(fixture.web.pid)
            assert len(attachment_pids)==32,attachment_pids
        finally:
            for stream in sockets:stream.close()
        wait_for(lambda:not children(fixture.web.pid),timeout=15)
        stream=WebSocket(fixture.url,'/terminal/'+panes[0]);stream.ready();stream.close()
        wait_for(lambda:not children(fixture.web.pid))
        first=WebSocket(fixture.url,'/terminal/'+panes[0]);first.ready()
        second=WebSocket(fixture.url,'/terminal/'+panes[0])
        busy=False
        try:
            while True:
                opcode,payload=second.receive()
                if opcode==1 and json.loads(payload).get('error'):
                    busy='busy' in json.loads(payload)['error'].lower();break
                if opcode==8:break
        finally:
            second.close();first.close()
        assert busy
        wait_for(lambda:not children(fixture.web.pid))
        fixture.web.terminate();fixture.web.wait(timeout=20)
        shim=fixture.root/'herdr-stream'
        python='import os,sys,tty;tty.setraw(0);os.write(1,b"\\x1b[?1016$p\\x1b[c");expected=b"\\x1b[?1016;0$y\\x1b[?1;2c";assert sys.stdin.buffer.read(len(expected))==expected;payload=b"x"*16384\nwhile True: os.write(1,payload)'
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
        result={'attachment_capacity_32_and_release':True,'busy_without_takeover':True,'stalled_reader_child_reaped':True,'rss_growth_kib':peak-before,'backpressure_uses_fault_injected_pty_producer':True}
        (fixture.logs/'results.json').write_text(json.dumps(result,indent=2))
        print(json.dumps(result,indent=2),flush=True)


if __name__=='__main__':main()
