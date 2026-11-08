#!/usr/bin/env python3
"""Linux long-poll cancellation, shared watch capacity, and descriptor cleanup."""

import http.client
import json
import pathlib
import select
import socket
import urllib.parse

from harness import Fixture, wait_for


def main():
    with Fixture('watch-resources') as fixture:
        directory = fixture.root / 'watched'
        directory.mkdir()
        parsed = urllib.parse.urlsplit(fixture.url)

        def inotify_count():
            count = 0
            for descriptor in pathlib.Path(f'/proc/{fixture.web.pid}/fd').iterdir():
                try:
                    count += str(descriptor.readlink()) == 'anon_inode:inotify'
                except FileNotFoundError:
                    pass
            return count

        def watch():
            connection = socket.create_connection((parsed.hostname, parsed.port), timeout=3)
            body = json.dumps({'path': str(directory)}).encode()
            connection.sendall((f'POST /api/watch HTTP/1.1\r\nHost: {parsed.netloc}\r\nOrigin: {fixture.url}\r\nContent-Type: application/json\r\nContent-Length: {len(body)}\r\n\r\n').encode() + body)
            return connection

        baseline = inotify_count()
        for cycle in range(3):
            pending = []
            try:
                for _ in range(64):
                    pending.append(watch())
                def full_capacity():
                    # Descriptor cleanup can precede permit release. Retry only
                    # temporary capacity responses; a leaked permit still fails
                    # the requirement to reach all 64 watches within 15 seconds.
                    for connection in select.select(pending, [], [], 0)[0]:
                        response = http.client.HTTPResponse(connection)
                        response.begin()
                        payload = response.read()
                        assert response.status == 503, (response.status, payload)
                        assert json.loads(payload) == {'error': 'watch capacity reached'}, payload
                        index = pending.index(connection)
                        connection.close()
                        pending[index] = watch()
                    return inotify_count() == baseline + 64
                wait_for(full_capacity)
                assert fixture.http('/api/watch', {'path': str(directory)})[0] == 503
            finally:
                for connection in pending:
                    connection.close()
            wait_for(lambda: inotify_count() == baseline)
        pending = watch()
        wait_for(lambda: inotify_count() == baseline + 1)
        fixture.web.terminate()
        fixture.web.wait(timeout=20)
        pending.close()
        assert fixture.rpc('session.snapshot')['snapshot']['protocol'] == 22
        result = {'watch_capacity_64': True, 'cancelled_long_polls_release_inotify_and_permits': True, 'repeated_cycles': 3, 'shutdown_preserves_backend': True}
        (fixture.logs / 'results.json').write_text(json.dumps(result, indent=2))
        print(json.dumps(result, indent=2), flush=True)


if __name__ == '__main__':
    main()
