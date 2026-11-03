import copy
import importlib.util
import pathlib
import unittest
from unittest.mock import Mock, patch

ROOT = pathlib.Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("browser_evidence", ROOT / "test/browser_evidence.py")
evidence = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(evidence)
ORIGIN = "http://127.0.0.1:8000"
SOCKET = "ws://127.0.0.1:8000/terminal/p1"


def non_repository():
    return {"status": 422, "method": "POST", "url": ORIGIN + "/api/git",
            "request": {"cwd": "/tmp/fixture", "args": list(evidence.GIT_PROBES[0])},
            "response": {"code": 128, "stdout": "", "stderr": evidence.NON_REPOSITORY + "\n",
                         "error": "Git exited unsuccessfully; see stderr"}}


def socket_event(kind, closed=False, unloading=False):
    return {"kind": kind, "url": SOCKET, "clientClosed": closed, "unloading": unloading, "time": 10}


class BrowserEvidenceTests(unittest.TestCase):
    def test_only_exact_read_only_non_repository_probes_are_expected(self):
        failure = non_repository()
        for probe in evidence.GIT_PROBES:
            failure["request"]["args"] = probe
            self.assertTrue(evidence.expected_git_probe(failure, ORIGIN))
        mutations = [
            ("status", 500), ("method", "GET"), ("url", ORIGIN + "/api/command"),
            ("url", "http://127.0.0.1:9000/api/git"),
            ("request", {"cwd": "/tmp/fixture", "args": ["commit", "-m", "test"]}),
            ("request", {"cwd": "/tmp/fixture", "args": ["status"]}),
            ("response", {"code": 128, "stderr": "fatal: permission denied"}),
            ("response", None), ("capture_error", "invalid JSON"),
        ]
        for key, value in mutations:
            with self.subTest(key=key, value=value):
                changed = copy.deepcopy(failure)
                changed[key] = value
                self.assertFalse(evidence.expected_git_probe(changed, ORIGIN))

    def test_http_failures_and_unhandled_console_errors_fail_the_gate(self):
        collector = evidence.BrowserEvidence(ORIGIN)
        collector.http_failures.append(non_repository())
        collector.assert_clean()
        collector.http_failures.append({"status": 503, "url": ORIGIN + "/api/state", "method": "GET"})
        with self.assertRaises(AssertionError):
            collector.assert_clean()
        collector.http_failures.pop()
        collector.console_errors.append({"text": "application crashed", "location": {}, "time": 10})
        with self.assertRaises(AssertionError):
            collector.assert_clean()

    def test_resource_console_error_needs_matching_expected_http_response(self):
        collector = evidence.BrowserEvidence(ORIGIN)
        error = {"text": evidence.RESOURCE_ERROR, "location": {"url": ORIGIN + "/api/git"}, "time": 10}
        self.assertFalse(collector.expected_console(error))
        collector.http_failures.append(non_repository())
        self.assertTrue(collector.expected_console(error))
        error["text"] = evidence.RESOURCE_ERROR.replace("Unprocessable Entity", "Unprocessable Content")
        self.assertTrue(collector.expected_console(error))
        collector.http_failures.append({"status": 500, "method": "POST", "url": ORIGIN + "/api/git"})
        self.assertFalse(collector.expected_console(error))

    def test_native_socket_diagnostics_need_recent_matching_intentional_close(self):
        collector = evidence.BrowserEvidence(ORIGIN)
        error = {"text": f'[JavaScript Error: "Firefox can’t establish a connection to the server at {SOCKET}." {{file: "{ORIGIN}/app.js" line: 65}}]',
                 "location": {}, "time": 10}
        self.assertFalse(collector.expected_console(error))
        collector.socket_events.append(socket_event("client-close", closed=True))
        self.assertTrue(collector.expected_console(error))
        collector.socket_events[0]["time"] = 1
        self.assertFalse(collector.expected_console(error))
        collector.socket_events[0]["time"] = 10
        collector.socket_events[0]["url"] = SOCKET + "-different"
        self.assertFalse(collector.expected_console(error))

    def test_reload_interruptions_are_scoped_to_exact_socket(self):
        collector = evidence.BrowserEvidence(ORIGIN)
        error = {"text": f"The connection to {SOCKET} was interrupted while the page was loading.",
                 "location": {}, "time": 10}
        collector.socket_events.append(socket_event("unload", unloading=True))
        self.assertTrue(collector.expected_console(error))
        error["text"] += " Additional unexpected error."
        self.assertFalse(collector.expected_console(error))

    def test_active_socket_error_cannot_be_hidden_by_nearby_expected_close(self):
        collector = evidence.BrowserEvidence(ORIGIN)
        collector.socket_events.append(socket_event("socket-error"))
        collector.socket_events.append(socket_event("client-close", closed=True))
        with self.assertRaises(AssertionError):
            collector.assert_clean()
        self.assertEqual(len(collector.report()["unexpected"]["websocket"]), 1)

    def test_native_console_diagnostic_uses_its_nearest_socket_error(self):
        collector = evidence.BrowserEvidence(ORIGIN)
        closing = socket_event('socket-error', closed=True)
        unrelated = socket_event('unclean-close')
        unrelated['time'] = 11
        collector.socket_events.extend([closing, unrelated])
        error = {'text': f'Firefox can’t establish a connection to the server at {SOCKET}.',
                 'location': {}, 'time': 10}
        self.assertTrue(collector.expected_console(error))
        collector.console_errors.append(error)
        with self.assertRaises(AssertionError):
            collector.assert_clean()
        self.assertEqual(len(collector.report()['unexpected']['websocket']), 1)

    def test_late_console_error_does_not_match_old_lifecycle_evidence(self):
        collector = evidence.BrowserEvidence(ORIGIN)
        collector.socket_events.append(socket_event("client-close", closed=True))
        error = {"text": f"Firefox can’t establish a connection to the server at {SOCKET}.",
                 "location": {}, "time": 13}
        self.assertFalse(collector.expected_console(error))

    def test_artifacts_retain_expected_and_unexpected_errors_without_truncation(self):
        collector = evidence.BrowserEvidence(ORIGIN)
        collector.http_failures.extend(non_repository() for _ in range(101))
        collector.page_errors.append("unhandled page exception")
        report = collector.report()
        self.assertEqual(len(report["http_failures"]), 101)
        self.assertEqual(report["unexpected"]["page"], ["unhandled page exception"])
        with self.assertRaises(AssertionError):
            collector.assert_clean()

    def test_response_capture_failure_is_not_treated_as_expected(self):
        collector = evidence.BrowserEvidence(ORIGIN)
        response = Mock(status=422, url=ORIGIN + "/api/git")
        response.request.method = "POST"
        response.request.headers = {}
        response.json.side_effect = ValueError("invalid JSON")
        collector.response(response)
        response.json.assert_not_called()
        collector.report()
        self.assertEqual(collector.http_failures[0]["capture_error"], "invalid JSON")
        with self.assertRaises(AssertionError):
            collector.assert_clean()

    def test_responses_arriving_during_body_capture_cannot_escape_the_gate(self):
        collector = evidence.BrowserEvidence(ORIGIN)
        first = Mock(status=503, url=ORIGIN + "/api/state")
        second = Mock(status=500, url=ORIGIN + "/api/command")
        for response in (first, second):
            response.request.method = "GET"
            response.request.headers = {}
        second.json.return_value = {"error": "second"}
        def capture_first():
            collector.response(second)
            return {"error": "first"}
        first.json.side_effect = capture_first
        collector.response(first)
        with self.assertRaises(AssertionError):
            collector.assert_clean()
        self.assertEqual(len(collector.report()["unexpected"]["http"]), 2)
        self.assertFalse(collector.pending_responses)

    def test_finished_error_response_is_captured_before_navigation_discards_it(self):
        collector = evidence.BrowserEvidence(ORIGIN)
        failure = non_repository()
        response = Mock(status=422, url=failure['url'])
        response.request.method = 'POST'
        response.request.headers = {'content-type': 'application/json'}
        response.request.post_data_json = failure['request']
        response.json.return_value = failure['response']
        collector.response(response)
        collector.finished(response.request)
        response.json.assert_called_once()
        response.json.side_effect = ValueError('response discarded by navigation')
        collector.assert_clean()
        response.json.assert_called_once()

    def test_browser_clone_recovers_only_the_matching_discarded_response(self):
        collector = evidence.BrowserEvidence(ORIGIN)
        failure = non_repository()
        captured = copy.deepcopy(failure)
        captured.update(kind='http-error', requestId='document:fetch:1')
        failure.pop('response')
        failure.update(request_id='document:fetch:1', capture_error='body discarded after navigation')
        collector.http_failures.append(failure)
        collector.fetch_failures[captured['requestId']] = captured
        collector.assert_clean()
        self.assertEqual(failure['body_source'], 'browser-response-clone')
        self.assertEqual(failure['body_capture_error'], 'body discarded after navigation')

    def test_different_request_id_or_bad_browser_clone_cannot_excuse_body_loss(self):
        collector = evidence.BrowserEvidence(ORIGIN)
        failure = non_repository()
        captured = copy.deepcopy(failure)
        captured.update(kind='http-error', requestId='document:fetch:2')
        failure.pop('response')
        failure.update(request_id='document:fetch:1', capture_error='body discarded after navigation')
        collector.http_failures.append(failure)
        collector.fetch_failures[captured['requestId']] = captured
        with self.assertRaises(AssertionError):
            collector.assert_clean()
        collector.fetch_failures.clear()
        captured['requestId'] = failure['request_id']
        captured['response']['stderr'] = 'fatal: permission denied'
        collector.fetch_failures[captured['requestId']] = captured
        with self.assertRaises(AssertionError):
            collector.assert_clean()

    def test_intentional_pane_removal_requires_backend_confirmation(self):
        collector = evidence.BrowserEvidence(ORIGIN)
        with self.assertRaisesRegex(AssertionError, 'Backend did not confirm'):
            with collector.expect_pane_removal(['p1'], lambda: False):
                pass
        self.assertEqual(collector.removals, [])
        collector.socket_events.append(socket_event('socket-error'))
        with self.assertRaises(AssertionError):
            collector.assert_clean()

    def test_confirmed_pane_removal_is_scoped_to_pane_origin_and_time(self):
        collector = evidence.BrowserEvidence(ORIGIN)
        with patch.object(evidence.time, 'monotonic', side_effect=[9, 10]):
            with collector.expect_pane_removal(['p1'], lambda: True):
                pass
        self.assertTrue(collector.expected_removal(SOCKET, 10))
        for url, timestamp in [(SOCKET, 8), (SOCKET, 13), (SOCKET + '-other', 10),
                               ('ws://127.0.0.1:9000/terminal/p1', 10)]:
            self.assertFalse(collector.expected_removal(url, timestamp))
        collector.socket_events.append(socket_event('socket-error'))
        collector.console_errors.append({
            'text': f"WebSocket connection to '{SOCKET}' failed: Error during WebSocket handshake: Unexpected response code: 400",
            'location': {}, 'time': 10,
        })
        collector.assert_clean()
        collector.console_errors[0]['text'] = collector.console_errors[0]['text'].replace('400', '403')
        with self.assertRaises(AssertionError):
            collector.assert_clean()

    def test_failed_removal_action_never_creates_an_exception_window(self):
        collector = evidence.BrowserEvidence(ORIGIN)
        with self.assertRaisesRegex(RuntimeError, 'remove failed'):
            with collector.expect_pane_removal(['p1'], lambda: True):
                raise RuntimeError('remove failed')
        self.assertEqual(collector.removals, [])


if __name__ == "__main__":
    unittest.main()
