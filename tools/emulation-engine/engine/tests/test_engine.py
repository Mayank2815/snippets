"""Runs the real engine — HTTP server, loop, shutdown — against the fake backend.

    make test
    python3 -m unittest discover -s engine/tests -v

Standard library only. ENGINE_BACKEND=fake is set before the engine is
imported so no real input is ever generated, and ENGINE_FAST=1 shrinks every
sleep so a whole loop cycle takes milliseconds instead of ~15 s. The server
is bound to a spare port picked by the OS, so an engine already running on
4320 is left alone.
"""

import http.client
import json
import os
import sys
import threading
import time
import unittest
from http.server import HTTPServer

# Both must be set before `engine` is imported: it picks the backend and the
# sleep scale at import time.
os.environ["ENGINE_BACKEND"] = "fake"
os.environ["ENGINE_FAST"] = "1"

# engine.py and its backends/ package live one directory up; put that first on
# the path so `import engine` finds the module, not a namespace package named
# after the engine/ folder when the tool root happens to be on sys.path.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import engine  # noqa: E402

HEADERS = {engine.CONTROL_HEADER: "1"}
# WHY 3 s / 2 s: a fast-mode cycle is ~0.2 s, so 3 s is a generous ceiling for
# "at least one call" and 2 s of silence proves the loop is really gone.
CALL_WAIT_SECONDS = 3
QUIET_SECONDS = 2


class EngineTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = HTTPServer((engine.BIND_HOST, 0), engine.EngineBridgeHandler)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        engine.shutdown_engine()

    def setUp(self):
        self.fake = engine.backend
        self.assertEqual(self.fake.name, "fake")
        self.fake.gate = None
        self.fake.calls.clear()

    def tearDown(self):
        # Leave every test with the loop stopped and gone.
        self.fake.gate = None
        engine.shutdown_engine()
        self._wait_for_state("IDLE")

    # -- helpers -----------------------------------------------------------

    def request(self, method, path, headers=None):
        conn = http.client.HTTPConnection(engine.BIND_HOST, self.port, timeout=5)
        try:
            conn.request(method, path, headers=headers or {})
            res = conn.getresponse()
            body = res.read()
            payload = json.loads(body) if res.getheader("Content-Type", "").startswith("application/json") else body
            return res.status, dict(res.getheaders()), payload
        finally:
            conn.close()

    def status(self):
        return self.request("GET", "/status")[2]

    def _wait_for_state(self, wanted, timeout=CALL_WAIT_SECONDS):
        deadline = time.time() + timeout
        while time.time() < deadline:
            state = self.status()["status"]
            if state == wanted:
                return state
            time.sleep(0.02)
        return self.status()["status"]

    def _wait_for_calls(self, minimum=1, method=None, timeout=CALL_WAIT_SECONDS):
        """True once the fake has recorded `minimum` calls (of `method`, when given)."""
        def count():
            return sum(1 for call in self.fake.calls if method is None or call[0] == method)
        deadline = time.time() + timeout
        while time.time() < deadline:
            if count() >= minimum:
                return True
            time.sleep(0.02)
        return count() >= minimum

    # -- tests -------------------------------------------------------------

    def test_status_reports_fake_backend_and_platform(self):
        payload = self.status()
        self.assertEqual(payload["status"], "IDLE")
        self.assertEqual(payload["backend"], "fake")
        self.assertEqual(payload["platform"], sys.platform)

    def test_console_is_served_at_root(self):
        code, headers, body = self.request("GET", "/")
        self.assertEqual(code, 200)
        self.assertTrue(headers["Content-Type"].startswith("text/html"))
        self.assertIn(b"Core Emulation Engine Control Console", body)
        self.assertIn(b'id="backend-line"', body)

    def test_start_runs_the_loop_against_the_backend(self):
        code, _, payload = self.request("POST", "/start", HEADERS)
        self.assertEqual(code, 200)
        self.assertTrue(payload["success"])
        self.assertEqual(self.status()["status"], "RUNNING")
        self.assertTrue(self._wait_for_calls(1), "the loop never called the backend")
        # A cycle is pointer read -> curved move -> keystroke burst; wait for
        # the burst so the whole first half of a cycle is proven.
        self.assertTrue(self._wait_for_calls(1, method="tap_key"), "the loop never reached the keystroke burst")
        methods = {call[0] for call in self.fake.calls}
        self.assertIn("mouse_position", methods)
        self.assertIn("move_mouse", methods)
        self.assertIn(self.fake.calls[0][0], ("mouse_position",))

    def test_stop_ends_the_loop(self):
        self.request("POST", "/start", HEADERS)
        self.assertTrue(self._wait_for_calls(1))
        code, _, payload = self.request("POST", "/stop", HEADERS)
        self.assertEqual(code, 200)
        self.assertTrue(payload["success"])
        self.assertIn(self.status()["status"], ("STOPPING", "IDLE"))
        self.assertEqual(self._wait_for_state("IDLE"), "IDLE")
        # No thread left: shutdown_engine must find nothing alive, and the
        # backend must go quiet.
        self.assertFalse(engine.engine_thread.is_alive())
        seen = len(self.fake.calls)
        time.sleep(QUIET_SECONDS)
        self.assertEqual(len(self.fake.calls), seen, "the backend kept receiving calls after /stop")

    def test_start_while_stopping_answers_409(self):
        # Park the worker inside its first backend call so the stop request
        # cannot complete: that is the STOPPING window /start must refuse.
        gate = threading.Event()
        self.fake.gate = gate
        self.request("POST", "/start", HEADERS)
        deadline = time.time() + CALL_WAIT_SECONDS
        while engine.engine_thread is None or not engine.engine_thread.is_alive():
            self.assertLess(time.time(), deadline, "worker did not start")
            time.sleep(0.01)
        code, _, payload = self.request("POST", "/stop", HEADERS)
        self.assertEqual((code, payload["success"]), (200, True))
        self.assertEqual(self.status()["status"], "STOPPING")

        code, _, payload = self.request("POST", "/start", HEADERS)
        self.assertEqual(code, 409)
        self.assertFalse(payload["success"])
        self.assertIn("stopping", payload["message"])

        # A second /stop while stopping is idempotent.
        code, _, payload = self.request("POST", "/stop", HEADERS)
        self.assertEqual((code, payload["message"]), (200, "Already stopping"))

        gate.set()
        self.assertEqual(self._wait_for_state("IDLE"), "IDLE")
        # Now a fresh start is accepted again and produces one worker only.
        code, _, _ = self.request("POST", "/start", HEADERS)
        self.assertEqual(code, 200)
        self.assertEqual(self.status()["status"], "RUNNING")

    def test_post_without_control_header_is_403(self):
        for path in ("/start", "/stop"):
            code, _, payload = self.request("POST", path)
            self.assertEqual(code, 403, path)
            self.assertFalse(payload["success"])
            self.assertIn(engine.CONTROL_HEADER, payload["message"])
        self.assertEqual(self.status()["status"], "IDLE")
        self.assertEqual(self.fake.calls, [])

    def test_disallowed_origin_gets_no_cors_headers_and_403_on_post(self):
        bad = {"Origin": "http://evil.example"}
        code, headers, _ = self.request("GET", "/status", bad)
        self.assertEqual(code, 200)
        self.assertNotIn("Access-Control-Allow-Origin", headers)
        self.assertNotIn("Access-Control-Allow-Methods", headers)

        code, headers, _ = self.request("OPTIONS", "/start", bad)
        self.assertNotIn("Access-Control-Allow-Origin", headers)

        code, _, payload = self.request("POST", "/start", {**HEADERS, **bad})
        self.assertEqual(code, 403)
        self.assertEqual(payload["message"], "origin not allowed")
        self.assertEqual(self.status()["status"], "IDLE")

    def test_allowed_origin_is_reflected(self):
        good = {"Origin": "http://localhost:4310"}
        code, headers, _ = self.request("GET", "/status", good)
        self.assertEqual(code, 200)
        self.assertEqual(headers["Access-Control-Allow-Origin"], "http://localhost:4310")
        self.assertEqual(headers["Vary"], "Origin")
        self.assertIn(engine.CONTROL_HEADER, headers["Access-Control-Allow-Headers"])

    def test_unknown_paths_are_404(self):
        code, _, payload = self.request("GET", "/nope")
        self.assertEqual((code, payload["error"]), (404, "not found"))
        code, _, payload = self.request("POST", "/nope", HEADERS)
        self.assertEqual(code, 404)

    def test_shutdown_releases_modifiers(self):
        self.request("POST", "/start", HEADERS)
        self.assertTrue(self._wait_for_calls(1))
        engine.shutdown_engine()
        self.assertEqual(self._wait_for_state("IDLE"), "IDLE")
        self.assertEqual(self.fake.calls[-1][0], "release_modifiers")


if __name__ == "__main__":
    unittest.main()
