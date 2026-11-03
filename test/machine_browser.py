"""Exercise the real machine dialog against two isolated SSH destinations."""

import json
import pathlib
from playwright.sync_api import expect, sync_playwright
from harness import wait_for
from ssh_remote import reaped


def run(fixture, alias, ports, starts):
    results = {}
    fixture.rpc('workspace.create', {'label': 'Machine dialog', 'focus': False})
    with sync_playwright() as playwright:
        for name in ['chromium', 'firefox']:
            browser = getattr(playwright, name).launch()
            context = browser.new_context(viewport={'width': 1300, 'height': 1000})
            page = context.new_page()
            errors, requests = [], []
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.on('request', lambda request: requests.append(request.post_data_json)
                    if request.method == 'POST' and request.url.endswith('/api/machines') else None)
            try:
                page.goto(fixture.url)
                page.locator('#machine').click()
                port = page.get_by_label('Remote web port', exact=True)
                host = page.get_by_label('SSH destination', exact=True)
                expect(port).to_have_value('8000')
                host.fill(alias)
                launched = starts.read_text() if starts.exists() else ''
                for invalid in ['', '0', '65536', '-1', '1.5', 'abc', '1e3']:
                    port.fill(invalid)
                    page.locator('#ma-add-typed').click()
                    expect(page.locator('#ma-port-error')).to_be_visible()
                    expect(port).to_have_attribute('aria-invalid', 'true')
                    assert requests == []
                    assert len(context.pages) == 1
                    assert (starts.read_text() if starts.exists() else '') == launched
                connections = []
                for remote_port in ports:
                    port.fill(str(remote_port))
                    host.fill(alias)
                    with page.expect_popup() as opening:
                        page.locator('#ma-add-typed').click()
                    popup = opening.value
                    wait_for(lambda: len(fixture.http('/api/machines')[1]) == len(connections) + 1)
                    connection = next(value for value in fixture.http('/api/machines')[1] if value['port'] == remote_port)
                    popup.wait_for_url(connection['url'] + '/')
                    expect(page.locator('#ma-connections')).to_contain_text(f'{alias} · remote web port {remote_port}')
                    assert requests[-1] == {'action': 'connect', 'host': alias, 'port': remote_port}
                    assert fixture.post('/api/machines', requests[-1])['pid'] == connection['pid']
                    connections.append(connection)
                    popup.close()
                assert connections[0]['local_port'] != connections[1]['local_port']
                assert connections[0]['pid'] != connections[1]['pid']
                page.screenshot(path=str(fixture.logs / f'{name}-machine-ports.png'))
                for index, connection in enumerate(connections):
                    page.locator('[id='+json.dumps(f'ma-disconnect-{alias}-{connection["port"]}')+']').click()
                    reaped(connection)
                    wait_for(lambda: len(fixture.http('/api/machines')[1]) == len(connections) - index - 1)
                    if index == 0:
                        assert pathlib.Path(f'/proc/{connections[1]["pid"]}').exists()
                assert not errors, errors
                results[name] = {'version': browser.version, 'default_port': 8000,
                                 'invalid_ports_start_no_ssh_or_http_request': True,
                                 'two_remote_ports_through_visible_dialog': True,
                                 'exact_request_ports_and_connection_status': True,
                                 'disconnect_preserves_other_port': True}
            except BaseException:
                page.screenshot(path=str(fixture.logs / f'{name}-machine-failure.png'))
                raise
            finally:
                context.close()
                browser.close()
                fixture.post('/api/machines', {'action': 'disconnect', 'host': alias})
    return results
