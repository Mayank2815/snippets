"""Unit tests for the Linux session check — the guard against the silent
Wayland failure.

    python3 -m unittest discover -s engine/tests -v

linux_session.check() is a pure function of the environment, so every branch
is exercised here on any machine: no X server, no Wayland compositor, no
pynput, and no real input of any kind is generated.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backends import linux_session  # noqa: E402
from backends.unavailable import UnavailableBackend  # noqa: E402


class LinuxSessionCheckTest(unittest.TestCase):
    def test_plain_x11_session_is_allowed(self):
        v = linux_session.check({"DISPLAY": ":0", "XDG_SESSION_TYPE": "x11"})
        self.assertTrue(v.ok)
        self.assertEqual(v.kind, "x11")
        self.assertIsNone(v.message)

    def test_xdg_session_type_wayland_is_refused(self):
        v = linux_session.check({"DISPLAY": ":0", "XDG_SESSION_TYPE": "wayland"})
        self.assertFalse(v.ok)
        self.assertEqual(v.kind, "wayland")
        self.assertIn("Wayland", v.message)
        # The remedy must name the concrete thing to click, not just "use X11".
        self.assertIn("Xorg", v.remedy)

    def test_wayland_display_alone_is_refused(self):
        """The case that produced no warning at all before: WAYLAND_DISPLAY set
        while XDG_SESSION_TYPE is unset (sway/Hyprland from a text console)."""
        v = linux_session.check({"DISPLAY": ":0", "WAYLAND_DISPLAY": "wayland-0"})
        self.assertFalse(v.ok)
        self.assertEqual(v.kind, "wayland")

    def test_wayland_display_alone_with_tty_session_type_is_refused(self):
        v = linux_session.check(
            {"DISPLAY": ":0", "WAYLAND_DISPLAY": "wayland-0", "XDG_SESSION_TYPE": "tty"})
        self.assertFalse(v.ok)
        self.assertEqual(v.kind, "wayland")

    def test_session_type_is_case_insensitive(self):
        v = linux_session.check({"DISPLAY": ":0", "XDG_SESSION_TYPE": "Wayland"})
        self.assertFalse(v.ok)

    def test_empty_wayland_display_is_not_wayland(self):
        """An exported-but-empty WAYLAND_DISPLAY must not refuse a real X11 session."""
        v = linux_session.check(
            {"DISPLAY": ":0", "WAYLAND_DISPLAY": "", "XDG_SESSION_TYPE": "x11"})
        self.assertTrue(v.ok)

    def test_no_display_at_all_is_refused(self):
        v = linux_session.check({"XDG_SESSION_TYPE": "tty"})
        self.assertFalse(v.ok)
        self.assertEqual(v.kind, "headless")
        self.assertIn("DISPLAY", v.message)

    def test_empty_display_is_refused(self):
        v = linux_session.check({"DISPLAY": "", "XDG_SESSION_TYPE": "x11"})
        self.assertFalse(v.ok)
        self.assertEqual(v.kind, "headless")

    def test_override_allows_wayland_but_keeps_the_warning(self):
        """ENGINE_ALLOW_WAYLAND=1 downgrades the refusal to a caveat — it must
        never downgrade it to silence."""
        v = linux_session.check({
            "DISPLAY": ":0", "XDG_SESSION_TYPE": "wayland",
            linux_session.OVERRIDE_VAR: "1",
        })
        self.assertTrue(v.ok)
        self.assertEqual(v.kind, "wayland")
        self.assertTrue(v.message, "an overridden Wayland session must still carry a warning")
        self.assertIn("XWayland", v.message)

    def test_override_without_a_display_is_still_refused(self):
        """Overriding cannot conjure an X server to inject through."""
        v = linux_session.check({
            "XDG_SESSION_TYPE": "wayland", "WAYLAND_DISPLAY": "wayland-0",
            linux_session.OVERRIDE_VAR: "1",
        })
        self.assertFalse(v.ok)

    def test_override_must_be_exactly_1(self):
        for value in ("0", "true", "yes", ""):
            v = linux_session.check({
                "DISPLAY": ":0", "XDG_SESSION_TYPE": "wayland",
                linux_session.OVERRIDE_VAR: value,
            })
            self.assertFalse(v.ok, f"{value!r} should not have enabled the override")

    def test_full_text_joins_message_and_remedy(self):
        v = linux_session.check({"DISPLAY": ":0", "XDG_SESSION_TYPE": "wayland"})
        self.assertIn(v.message, v.full_text)
        self.assertIn(v.remedy, v.full_text)


class UnavailableBackendTest(unittest.TestCase):
    def setUp(self):
        self.backend = UnavailableBackend("pynput", "no input here", "do this instead")

    def test_it_reports_itself_as_unusable_under_the_intended_name(self):
        self.assertFalse(self.backend.input_ok)
        # /status must still say "pynput", the name the docs use.
        self.assertEqual(self.backend.name, "pynput")
        self.assertEqual(self.backend.input_error, "no input here")
        self.assertEqual(self.backend.input_remedy, "do this instead")

    def test_every_input_method_raises_rather_than_doing_nothing_quietly(self):
        cases = [
            ("mouse_position", ()), ("move_mouse", (1, 2)), ("click", (1, 2)),
            ("scroll", (1, 1)), ("tap_key", ("left",)), ("app_switch", (2,)),
            ("browser_tab_next", ()),
        ]
        for method, args in cases:
            with self.subTest(method=method):
                with self.assertRaises(RuntimeError):
                    getattr(self.backend, method)(*args)

    def test_the_methods_shutdown_and_the_banner_need_are_safe(self):
        # Both are called unconditionally, so neither may raise.
        self.assertIsNone(self.backend.release_modifiers())
        self.assertEqual(self.backend.screen_size(), UnavailableBackend.SCREEN)
        self.assertEqual(self.backend.visible_app_count(), 5)


if __name__ == "__main__":
    unittest.main()
