#!/usr/bin/env python3
import json
import socket
import urllib.parse

from harness import Fixture, ROOT
from websocket_client import WebSocket


def main():
    with Fixture('security') as fixture:
        for path,mime in [('/','text/html'),('/vendor/xterm.js','text/javascript'),('/desktop/fonts/Geist.ttf','font/ttf'),('/desktop/icons/shell.svg','image/svg+xml')]:
            import urllib.request
            with urllib.request.urlopen(fixture.url+path) as response:
                assert response.status==200
                assert response.headers['Content-Type'].startswith(mime)
                assert response.headers['Cache-Control']=='no-store'
                assert response.headers['X-Content-Type-Options']=='nosniff'
                policy=response.headers['Content-Security-Policy']
                assert "frame-ancestors 'none'" in policy and "object-src 'none'" in policy
        notices = [ROOT / 'public/desktop/LICENSE', ROOT / 'public/desktop/icons/LICENSES.md', ROOT / 'public/vendor/SHA256SUMS']
        notices.extend(path for path in (ROOT / 'public/vendor/licenses').rglob('*') if path.is_file())
        for path in notices:
            with urllib.request.urlopen(fixture.url + '/' + path.relative_to(ROOT / 'public').as_posix()) as response:
                assert response.status == 200
                assert response.headers['Content-Type'].startswith('application/json' if path.suffix == '.json' else 'text/plain')
                assert response.read() == path.read_bytes(), path
        for path in ['/Cargo.toml','/.git/config','/server/src/main.rs','/no-such-asset.js']:
            assert fixture.http(path)[0]==404
        assert fixture.http('/',headers={'Host':'evil.localhost'})[0]==403
        assert fixture.http('/',headers={'Host':'user@localhost'})[0]==400
        assert fixture.http('/',headers={'Origin':'http://foreign.example'})[0]==403
        assert fixture.http('/api/files',{'action':'read','path':'/dev/zero'},headers={'Origin':'http://foreign.example'})[0]==403
        assert fixture.http('/api/files',{'action':'read','path':'/dev/zero'},headers={'Content-Type':'text/plain'})[0]==415
        parsed=urllib.parse.urlsplit(fixture.url)
        with socket.create_connection((parsed.hostname,parsed.port),timeout=3) as connection:
            body=b'{"action":"read","path":"/dev/zero"}'
            connection.sendall((f'POST /api/files HTTP/1.1\r\nHost: {parsed.netloc}\r\nContent-Type: application/json\r\nContent-Length: {len(body)}\r\nConnection: close\r\n\r\n').encode()+body)
            assert connection.recv(8192).startswith(b'HTTP/1.1 403')
        with socket.create_connection((parsed.hostname,parsed.port),timeout=3) as connection:
            connection.sendall((f'GET / HTTP/1.1\r\nHost: {parsed.netloc}\r\nHost: localhost\r\nConnection: close\r\n\r\n').encode())
            assert connection.recv(8192).startswith(b'HTTP/1.1 400')
        assert fixture.http('/api/command',raw=b'x'*16385,headers={'Content-Type':'application/json'},method='POST')[0]==413
        assert fixture.http('/api/command',raw=b'{broken',headers={'Content-Type':'application/json'},method='POST')[0]==400
        assert fixture.http('/api/clipboard-image',raw=b'\x89PNG\r\n\x1a\n'+b'x'*(10*1024*1024),headers={'Content-Type':'image/png'},method='POST')[0]==413
        denied=WebSocket(fixture.url,'/api/events',origin=False)
        assert denied.status==403
        denied.close()
        assert fixture.rpc('session.snapshot')['snapshot']['workspaces']==[]
        results={'static_mime_embedding_and_source_404':True,'embedded_license_and_provenance_files':len(notices),'host_origin_and_upgrade_boundary':True,'body_and_json_limits':True,'rejection_before_backend_mutation':True}
        (fixture.logs/'results.json').write_text(json.dumps(results,indent=2))
        print(json.dumps(results,indent=2),flush=True)


if __name__=='__main__':main()
