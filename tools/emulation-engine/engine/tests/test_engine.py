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
import governor as gov_module  # noqa: E402
from backends.unavailable import UnavailableBackend  # noqa: E402

HEADERS = {engine.CONTROL_HEADER: "1"}
# The backend methods that actually put something on screen. Reads
# (screen_size, mouse_position) are not input, and the loop makes one
# screen_size call per run to work out where the pointer may go.
INPUT_METHODS = {"move_mouse", "click", "scroll", "tap_key", "app_switch", "browser_tab_next"}
# The pool as shipped, captured before any test narrows it (see setUp).
SHIPPED_MODE_POOL = list(engine.MODE_POOL)
# WHY 3 s / 2 s: a fast-mode cycle is ~0.2 s, so 3 s is a generous ceiling for
# "at least one call" and 2 s of silence proves the loop is really gone.
CALL_WAIT_SECONDS = 3
QUIET_SECONDS = 2


class EngineHarness(unittest.TestCase):
    """Server, fixtures and helpers, with no tests of its own.

    WHY it is separate from EngineTestCase: the classes below swap the backend
    for one that cannot deliver input, and inheriting the normal test methods
    would run them all again against that backend. Each subclass gets its own
    server on its own OS-picked port, so they cannot interfere either.
    """

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
        self.fake.user_idle_seconds = None
        self.fake.idle_polls = 0
        self._click_backup = engine.ALLOW_CLICK
        self._hours_backup = engine.MAX_RUN_HOURS
        # WHY pin the pool: one draw in six is THINKING, which does no backend
        # work at all, so a test waiting for "at least one call" could sit
        # through several of them and fail on timing alone. Tests that care
        # about THINKING set the pool themselves; everything else runs the
        # working profiles. use_modes() restores this in tearDown.
        self._modes_backup = engine.MODE_POOL
        engine.MODE_POOL = [m for m in engine.MODE_POOL if m != "THINKING"]

    def use_modes(self, *modes):
        """Force the profiles this test's cycles will draw from."""
        engine.MODE_POOL = list(modes)

    def tearDown(self):
        # Leave every test with the loop stopped and gone.
        self.fake.gate = None
        engine.shutdown_engine()
        self._wait_for_state("IDLE")
        engine.MODE_POOL = self._modes_backup
        engine.ALLOW_CLICK = self._click_backup
        engine.MAX_RUN_HOURS = self._hours_backup
        self.fake.user_idle_seconds = None

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

    def _wait_for_field(self, field, wanted, timeout=CALL_WAIT_SECONDS):
        """True once GET /status reports `field` as `wanted`."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.status().get(field) == wanted:
                return True
            time.sleep(0.02)
        return self.status().get(field) == wanted

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


class EngineTestCase(EngineHarness):
    """The engine on a backend that works — the normal case."""

    def test_status_reports_fake_backend_and_platform(self):
        payload = self.status()
        self.assertEqual(payload["status"], "IDLE")
        self.assertEqual(payload["backend"], "fake")
        self.assertEqual(payload["platform"], sys.platform)
        # A working backend must say so explicitly, so the console can trust
        # the field rather than inferring from its absence.
        self.assertTrue(payload["inputWorking"])
        self.assertIsNone(payload["warning"])

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
        # The run opens by asking how big the screen is - that is where the
        # pointer's reachable rectangle comes from - and then reads the pointer.
        self.assertEqual(self.fake.calls[0][0], "screen_size")
        self.assertIn("mouse_position", methods)

    # --- behaviour profiles ----------------------------------------------

    def test_every_profile_is_usable_and_ordered(self):
        """Each profile's ranges are (low, high) and non-negative, so
        random.randint/uniform cannot raise at three in the morning."""
        for mode, profile in engine.MODE_PROFILES.items():
            for field in ("strokes", "key_gap", "cycle_sleep"):
                low, high = profile[field]
                self.assertLessEqual(low, high, f"{mode}.{field} is back to front")
                self.assertGreaterEqual(low, 0, f"{mode}.{field} is negative")

    def test_the_pool_only_names_profiles_that_exist(self):
        # The shipped pool, not the one setUp narrowed for the other tests.
        for mode in SHIPPED_MODE_POOL:
            self.assertIn(mode, engine.MODE_PROFILES)
        # Every profile should be reachable, or it is dead configuration.
        self.assertEqual(set(SHIPPED_MODE_POOL), set(engine.MODE_PROFILES))
        # THINKING must stay rare: it is a minute of nothing.
        thinking = SHIPPED_MODE_POOL.count("THINKING")
        self.assertLessEqual(thinking / len(SHIPPED_MODE_POOL), 0.25)

    def test_status_names_the_profile_while_running(self):
        self.use_modes("READING")
        self.request("POST", "/start", HEADERS)
        self.assertTrue(self._wait_for_calls(1))
        self.assertEqual(self.status()["mode"], "READING")

    def test_status_reports_no_profile_when_idle(self):
        self.assertIsNone(self.status()["mode"])
        self.use_modes("BURST")
        self.request("POST", "/start", HEADERS)
        self.assertTrue(self._wait_for_calls(1))
        self.request("POST", "/stop", HEADERS)
        self._wait_for_state("IDLE")
        # A stale profile beside IDLE would read as "still working".
        self.assertIsNone(self.status()["mode"])

    def test_thinking_generates_no_input_at_all(self):
        """THINKING is the away-from-the-keyboard profile: the whole point is
        that it makes no calls, and the console must still say it is running."""
        self.use_modes("THINKING")
        self.request("POST", "/start", HEADERS)
        payload = self.status()
        self.assertEqual(payload["status"], "RUNNING")
        self.assertEqual(payload["mode"], "THINKING")
        # Long enough for several fast-mode thinking pauses (0.45-0.75 s each).
        time.sleep(QUIET_SECONDS)
        generated = [call for call in self.fake.calls if call[0] in INPUT_METHODS]
        self.assertEqual(generated, [], "THINKING generated input")
        self.assertEqual(self.status()["status"], "RUNNING")

    def test_stop_ends_a_thinking_pause_without_waiting_it_out(self):
        """stop.wait(), not sleep(): a 45-75 s pause must not delay Stop."""
        self.use_modes("THINKING")
        self.request("POST", "/start", HEADERS)
        self._wait_for_state("RUNNING")
        started = time.time()
        self.request("POST", "/stop", HEADERS)
        self.assertEqual(self._wait_for_state("IDLE"), "IDLE")
        # A full fast-mode pause is 0.75 s; anything near that means Stop waited.
        self.assertLess(time.time() - started, 1.0, "Stop waited out the pause")

    def test_burst_types_more_than_reading(self):
        """The profiles have to actually change the loop, not just be data."""
        def strokes_in(mode):
            self.use_modes(mode)
            self.fake.calls.clear()
            self.request("POST", "/start", HEADERS)
            self.assertTrue(self._wait_for_calls(1, method="tap_key"))
            # One cycle's worth: stop as soon as the burst has been seen.
            self.request("POST", "/stop", HEADERS)
            self._wait_for_state("IDLE")
            return sum(1 for call in self.fake.calls if call[0] == "tap_key")

        # BURST draws 24-36 keystrokes, READING 5-10, so even one cycle each
        # separates them with a wide margin.
        self.assertGreater(strokes_in("BURST"), strokes_in("READING"))


    # --- the pointer's reachable area ------------------------------------

    def test_the_target_rectangle_scales_with_the_screen(self):
        """The bug this fixes: a rectangle written for a 1280x800 laptop left
        the pointer inside 29 % of a 1470x956 screen, always in the top left."""
        for width, height in ((1280, 800), (1470, 956), (1920, 1080), (3840, 2160)):
            left, top, right, bottom = engine.target_rectangle(width, height)
            self.assertLess(left, right)
            self.assertLess(top, bottom)
            # Inset from every edge, so menu bars, docks, taskbars and hot
            # corners are all out of reach.
            self.assertGreaterEqual(left, engine.TARGET_MIN_INSET_PX)
            self.assertGreaterEqual(top, engine.TARGET_MIN_INSET_PX)
            self.assertLessEqual(right, width - engine.TARGET_MIN_INSET_PX)
            self.assertLessEqual(bottom, height - engine.TARGET_MIN_INSET_PX)
            # And it is most of the screen, not a corner of it.
            covered = (right - left) * (bottom - top) / float(width * height)
            self.assertGreater(covered, 0.55, f"only {covered:.0%} of {width}x{height} is reachable")

    def test_a_bigger_screen_gets_a_bigger_rectangle(self):
        small = engine.target_rectangle(1280, 800)
        large = engine.target_rectangle(3840, 2160)
        self.assertGreater(large[2] - large[0], small[2] - small[0])
        self.assertGreater(large[3] - large[1], small[3] - small[1])

    def test_a_tiny_screen_still_gets_a_usable_rectangle(self):
        """A 320x240 virtual display in a container is smaller than twice the
        inset floor; it must not come back inside out."""
        left, top, right, bottom = engine.target_rectangle(320, 240)
        self.assertLess(left, right)
        self.assertLess(top, bottom)

    def test_the_pointer_reaches_every_part_of_the_rectangle(self):
        """Hops are a fraction of the rectangle, so the whole of it gets
        visited whatever the screen size - on a 4K display a fixed 300 px hop
        would take minutes to cross one."""
        for width, height in ((1470, 956), (3840, 2160)):
            rect = engine.target_rectangle(width, height)
            hop = engine.pointer_hop(rect)
            left, top, right, bottom = rect
            x, y = (left + right) // 2, (top + bottom) // 2
            cells = set()
            for _ in range(600):
                x, y = engine.next_pointer_target(x, y, rect, hop)
                self.assertTrue(left <= x <= right and top <= y <= bottom,
                                f"({x}, {y}) escaped {rect}")
                cells.add((int((x - left) * 3 / (right - left + 1)),
                           int((y - top) * 3 / (bottom - top + 1))))
            self.assertEqual(len(cells), 9,
                             f"{width}x{height}: only {len(cells)} of 9 regions were visited")

    # --- clicking ---------------------------------------------------------

    def test_clicking_is_off_unless_the_environment_asks_for_it(self):
        """A click lands on whatever is in front - a link, a Send button, a
        Delete. It is the one thing the engine does that cannot be undone."""
        engine.ALLOW_CLICK = False
        # Certainty, not probability: if a click were going to happen at all
        # under these settings, it would happen every cycle.
        was = engine.CLICK_PROBABILITY
        engine.CLICK_PROBABILITY = 1.0
        try:
            self.use_modes("BURST")
            self.request("POST", "/start", HEADERS)
            self.assertTrue(self._wait_for_calls(3, method="tap_key"))
            time.sleep(0.3)
            self.assertNotIn("click", {call[0] for call in self.fake.calls})
        finally:
            engine.CLICK_PROBABILITY = was

    def test_clicking_works_when_it_is_switched_on(self):
        engine.ALLOW_CLICK = True
        was = engine.CLICK_PROBABILITY
        engine.CLICK_PROBABILITY = 1.0
        try:
            self.use_modes("BURST")
            self.request("POST", "/start", HEADERS)
            self.assertTrue(self._wait_for_calls(1, method="click"),
                            "ENGINE_ALLOW_CLICK=1 did not produce a click")
        finally:
            engine.CLICK_PROBABILITY = was

    def test_status_says_whether_clicking_is_on(self):
        engine.ALLOW_CLICK = False
        self.assertFalse(self.status()["clickEnabled"])
        engine.ALLOW_CLICK = True
        self.assertTrue(self.status()["clickEnabled"])

    # --- the governor, through the engine ---------------------------------

    def test_status_carries_the_governor_while_running(self):
        self.use_modes("STANDARD")
        self.request("POST", "/start", HEADERS)
        self.assertTrue(self._wait_for_calls(1, method="tap_key"))
        payload = self.status()
        governor = payload["governor"]
        self.assertIsNotNone(governor, "/status carried no governor while running")
        self.assertEqual(governor["blocksPerWindow"], gov_module.BLOCKS_PER_WINDOW)
        self.assertEqual(governor["ceilingPercent"], gov_module.CEILING_PERCENT)
        self.assertGreaterEqual(governor["windowUsedBlocks"], 1)
        self.assertLessEqual(governor["windowUsedBlocks"], gov_module.CEILING_BLOCKS)
        self.assertGreater(governor["windowTargetBlocks"], 0)
        self.assertFalse(payload["pausedForUser"])

    def test_status_has_no_governor_when_idle(self):
        """A stale window read-out beside IDLE would say the engine is working."""
        payload = self.status()
        self.assertIsNone(payload["governor"])
        self.assertIsNone(payload["runSecondsRemaining"])
        self.assertFalse(payload["pausedForUser"])

    def test_the_loop_never_passes_the_ceiling_in_a_real_run(self):
        """Not the governor in isolation - the whole engine, through HTTP."""
        self.use_modes("BURST")
        self.request("POST", "/start", HEADERS)
        self.assertTrue(self._wait_for_calls(1, method="tap_key"))
        deadline = time.time() + 2.0
        while time.time() < deadline:
            governor = self.status()["governor"]
            if governor is not None:
                self.assertLessEqual(governor["windowUsedBlocks"], gov_module.CEILING_BLOCKS)
                self.assertLessEqual(governor["rollingUsedBlocks"], gov_module.CEILING_BLOCKS)
            time.sleep(0.05)

    # --- pausing while the person works -----------------------------------

    def test_the_engine_pauses_while_the_user_is_working(self):
        """It used to yank the pointer and press arrow keys into whatever the
        person was typing. Now it stands down."""
        self.use_modes("BURST")
        self.request("POST", "/start", HEADERS)
        self.assertTrue(self._wait_for_calls(1, method="tap_key"))

        # The person touches the keyboard: the OS idle timer reads zero and
        # keeps reading zero.
        self.fake.user_idle_seconds = 0.0
        paused = self._wait_for_field("pausedForUser", True)
        self.assertTrue(paused, "the engine did not notice the user")
        self.assertEqual(self.status()["status"], "RUNNING")

        before = len([c for c in self.fake.calls if c[0] in INPUT_METHODS])
        time.sleep(QUIET_SECONDS)
        after = len([c for c in self.fake.calls if c[0] in INPUT_METHODS])
        self.assertEqual(after, before, "the engine kept generating input while the user was working")

    def test_the_engine_resumes_once_the_user_has_been_quiet(self):
        self.use_modes("BURST")
        self.request("POST", "/start", HEADERS)
        self.fake.user_idle_seconds = 0.0
        self.assertTrue(self._wait_for_field("pausedForUser", True))

        # Nobody has touched it for well over the quiet period.
        self.fake.user_idle_seconds = gov_module.USER_ACTIVE_QUIET_SECONDS * 10
        self.assertTrue(self._wait_for_field("pausedForUser", False),
                        "the engine never came back")
        self.fake.calls.clear()
        self.assertTrue(self._wait_for_calls(1, method="tap_key"),
                        "the engine came back but generated nothing")

    def test_a_platform_with_no_idle_timer_never_pauses(self):
        """user_idle_seconds is None by default - the Linux-without-XScreenSaver
        case. The engine must simply carry on."""
        self.use_modes("BURST")
        self.request("POST", "/start", HEADERS)
        self.assertTrue(self._wait_for_calls(2, method="tap_key"))
        self.assertFalse(self.status()["pausedForUser"])
        self.assertGreater(self.fake.idle_polls, 0, "the idle timer was never polled")

    # --- the run limit ----------------------------------------------------

    def test_the_run_stops_itself_at_the_time_limit_and_goes_idle(self):
        """A forgotten engine must not still be driving the machine at 3am."""
        # WHY this many seconds: engine_time() is scaled by ENGINE_FAST too, so
        # in these tests one governor-second is 10 ms of wall clock. Five
        # governor-seconds is 50 ms - long enough for the loop to do a cycle
        # first, short enough not to slow the suite.
        engine.MAX_RUN_HOURS = 5.0 / 3600.0
        self.use_modes("BURST")
        self.request("POST", "/start", HEADERS)
        self.assertEqual(self._wait_for_state("IDLE"), "IDLE")
        self.assertIsNone(self.status()["governor"])

    def test_status_counts_down_the_remaining_time(self):
        engine.MAX_RUN_HOURS = 1.0
        self.use_modes("BURST")
        self.request("POST", "/start", HEADERS)
        self.assertTrue(self._wait_for_calls(1))
        first = self.status()["runSecondsRemaining"]
        self.assertIsNotNone(first)
        self.assertLessEqual(first, 3600.0)
        time.sleep(0.2)
        second = self.status()["runSecondsRemaining"]
        self.assertLess(second, first, "the remaining time did not go down")

    def test_zero_hours_means_no_limit(self):
        engine.MAX_RUN_HOURS = 0.0
        self.use_modes("BURST")
        self.request("POST", "/start", HEADERS)
        self.assertTrue(self._wait_for_calls(1))
        self.assertIsNone(self.status()["runSecondsRemaining"])
        self.assertEqual(self.status()["status"], "RUNNING")

    def test_a_nonsense_max_hours_falls_back_to_the_default(self):
        was = os.environ.get("ENGINE_MAX_HOURS")
        try:
            os.environ["ENGINE_MAX_HOURS"] = "three"
            self.assertEqual(engine._max_run_hours(), engine.DEFAULT_MAX_RUN_HOURS)
            os.environ["ENGINE_MAX_HOURS"] = "-5"
            self.assertEqual(engine._max_run_hours(), 0.0)
            os.environ["ENGINE_MAX_HOURS"] = "2.5"
            self.assertEqual(engine._max_run_hours(), 2.5)
        finally:
            if was is None:
                os.environ.pop("ENGINE_MAX_HOURS", None)
            else:
                os.environ["ENGINE_MAX_HOURS"] = was

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

    def test_a_second_sigterm_does_not_interrupt_the_shutdown(self):
        """run.sh traps INT and TERM, so a Ctrl-C there delivers two signals.
        The second used to land inside release_modifiers() and print a traceback
        instead of releasing the held modifier."""
        was = engine._shutting_down
        engine._shutting_down = False
        try:
            with self.assertRaises(KeyboardInterrupt):
                engine._on_sigterm(15, None)
            # Every later signal is absorbed, however many arrive.
            for _ in range(3):
                self.assertIsNone(engine._on_sigterm(15, None))
        finally:
            engine._shutting_down = was

    def test_shutdown_releases_modifiers(self):
        self.request("POST", "/start", HEADERS)
        self.assertTrue(self._wait_for_calls(1))
        engine.shutdown_engine()
        self.assertEqual(self._wait_for_state("IDLE"), "IDLE")
        self.assertEqual(self.fake.calls[-1][0], "release_modifiers")


class UnavailableInputTestCase(EngineHarness):
    """The engine with a backend that cannot deliver input — the Wayland case.

    The whole point is that this state is impossible to mistake for a working
    one: /status says so, /start is refused, and no loop thread is ever
    created. Swapping engine.backend is exactly what get_backend() does at
    import time on a Wayland machine, so these run the real request path.
    """

    MESSAGE = "Wayland session detected — synthetic input is not delivered to applications."
    REMEDY = "Log out and choose \"Ubuntu on Xorg\" at the login screen."

    def setUp(self):
        super().setUp()
        self.real_backend = engine.backend
        engine.backend = UnavailableBackend("pynput", self.MESSAGE, self.REMEDY)

    def tearDown(self):
        engine.backend = self.real_backend
        super().tearDown()

    def test_status_says_input_is_not_working_and_explains_why(self):
        payload = self.status()
        self.assertEqual(payload["status"], "IDLE")
        self.assertFalse(payload["inputWorking"])
        # Both halves reach the console: what is wrong AND what to do about it.
        self.assertIn(self.MESSAGE, payload["warning"])
        self.assertIn(self.REMEDY, payload["warning"])
        # The backend name stays the one the docs use.
        self.assertEqual(payload["backend"], "pynput")

    def test_start_is_refused_and_no_loop_is_created(self):
        code, _, payload = self.request("POST", "/start", HEADERS)
        self.assertEqual(code, 503)
        self.assertFalse(payload["success"])
        self.assertIn(self.MESSAGE, payload["message"])
        self.assertFalse(payload["inputWorking"])
        # The engine must not merely refuse the HTTP call — it must not have
        # started a worker, and must not have touched the backend at all.
        self.assertEqual(self.status()["status"], "IDLE")
        self.assertEqual(self.real_backend.calls, [])

    def test_repeated_starts_stay_refused(self):
        for _ in range(3):
            code, _, _ = self.request("POST", "/start", HEADERS)
            self.assertEqual(code, 503)
        self.assertEqual(self.status()["status"], "IDLE")

    def test_the_control_header_is_still_required_first(self):
        """A missing header is still 403 — the new refusal must not become a
        way to probe the engine without one."""
        code, _, payload = self.request("POST", "/start")
        self.assertEqual(code, 403)
        self.assertIn(engine.CONTROL_HEADER, payload["message"])

    def test_a_disallowed_origin_is_still_403_not_503(self):
        code, _, payload = self.request(
            "POST", "/start", {**HEADERS, "Origin": "http://evil.example"})
        self.assertEqual(code, 403)
        self.assertEqual(payload["message"], "origin not allowed")

    def test_stop_still_answers(self):
        """/stop is always safe, so blocking it would only confuse."""
        code, _, payload = self.request("POST", "/stop", HEADERS)
        self.assertEqual(code, 200)
        self.assertEqual(payload["message"], "Already idle")

    def test_console_and_shutdown_still_work(self):
        code, _, body = self.request("GET", "/")
        self.assertEqual(code, 200)
        self.assertIn(b"Core Emulation Engine Control Console", body)
        # shutdown_engine() calls release_modifiers() unconditionally; on this
        # backend that must be a harmless no-op rather than a crash on exit.
        engine.shutdown_engine()


class CaveatedInputTestCase(EngineHarness):
    """A backend that works but has something worth saying (ENGINE_ALLOW_WAYLAND).
    The warning must reach /status while /start keeps working normally."""

    def setUp(self):
        super().setUp()
        self.fake.input_error = "Wayland session — input reaches only XWayland windows."
        self.fake.input_remedy = "Switch to Xorg for the rest."

    def tearDown(self):
        self.fake.input_error = None
        self.fake.input_remedy = None
        super().tearDown()

    def test_warning_is_reported_while_input_still_works(self):
        payload = self.status()
        self.assertTrue(payload["inputWorking"])
        self.assertIn("XWayland", payload["warning"])

    def test_start_is_allowed(self):
        code, _, payload = self.request("POST", "/start", HEADERS)
        self.assertEqual(code, 200)
        self.assertTrue(payload["success"])
        self.assertTrue(self._wait_for_calls(1), "the loop never called the backend")


if __name__ == "__main__":
    unittest.main()
