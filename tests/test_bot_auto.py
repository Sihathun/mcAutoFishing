"""
The bot's automatic mode, driven by a fake screen and a fake game window. Dry-run only, so it can never
click, and nothing touches the real desktop.
"""
import contextlib
import sys
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import autofish as af  # noqa: E402
import synth  # noqa: E402
import winutil as wu  # noqa: E402

CW, CH = 1400, 800
ORIGIN = (100, 50)     # where the fake client area sits on the fake desktop


def fake_window(hwnd=7):
    return wu.WindowInfo(hwnd=hwnd, title="Fake Minecraft", cls="SDL_app", pid=1, exe="javaw.exe", width=CW, height=CH)


class FakeDesktop:
    """Serves whatever frame `.client` holds, the way mss would for the game's client area."""

    def __init__(self, client, size=(CW, CH)):
        self.client = client
        self.size = size
        self.minimized = False
        self.alive = True

    def grab_abs(self, _sct, left, top, w, h):
        x, y = left - ORIGIN[0], top - ORIGIN[1]
        return self.client[y:y + h, x:x + w].copy()

    def patches(self):
        return [
            mock.patch.object(af.winutil, "is_window", lambda h: self.alive),
            mock.patch.object(af.winutil, "is_minimized", lambda h: self.minimized),
            mock.patch.object(af.winutil, "client_rect", lambda h: (*ORIGIN, *self.size)),
            mock.patch.object(af, "grab_abs", self.grab_abs),
            mock.patch.object(af, "MSS", lambda: contextlib.nullcontext()),
        ]


def run_bot(desktop, seconds, cfg_extra=None, resolver=None, dry_run=True):
    cfg = dict(af.DEFAULTS, cooldown=0.3, poll_interval=0.01, start_delay=0, **(cfg_extra or {}))
    logs = []
    bot = af.FishingBot(cfg, log=logs.append, window_resolver=resolver or (lambda: fake_window()))
    with contextlib.ExitStack() as stack:
        for p in desktop.patches():
            stack.enter_context(p)
        bot.start(dry_run=dry_run)
        time.sleep(seconds)
        bot.stop()
        bot.join(3)
    return bot, logs


class AutoModeTests(unittest.TestCase):
    def test_learns_the_gui_scale_from_the_first_splash_and_detects_it(self):
        client, _ = synth.make_client(4, CW, CH)
        bot, logs = run_bot(FakeDesktop(client), 1.0)
        self.assertFalse(bot.running)
        self.assertEqual(bot.scale, 4)
        self.assertEqual(bot.cfg["gui_scale"], 4)
        self.assertEqual(bot.cfg["gui_scale_client"], [CW, CH])
        self.assertGreaterEqual(bot.catches, 1)
        self.assertTrue(any("GUI scale detected: 4x" in line for line in logs), logs)

    def test_no_splash_means_no_lock_and_no_detection(self):
        client, _ = synth.make_client(4, CW, CH, draw_text=False)
        bot, logs = run_bot(FakeDesktop(client), 0.6)
        self.assertIsNone(bot.scale)
        self.assertEqual(bot.catches, 0)
        self.assertEqual(bot.state, "idle")
        self.assertFalse(any("detected" in line for line in logs))

    def test_a_remembered_scale_is_used_straight_away(self):
        client, _ = synth.make_client(3, CW, CH)
        bot, logs = run_bot(FakeDesktop(client), 0.6, {"gui_scale": 3, "gui_scale_client": [CW, CH]})
        self.assertEqual(bot.scale, 3)
        self.assertGreaterEqual(bot.catches, 1)
        self.assertFalse(any("learning" in line.lower() for line in logs))

    def test_a_resized_window_forgets_the_scale(self):
        client, _ = synth.make_client(3, CW, CH)
        # Remembered at a different window size -> must not be trusted; relearned from the splash instead.
        bot, logs = run_bot(FakeDesktop(client), 0.8, {"gui_scale": 6, "gui_scale_client": [1000, 600]})
        self.assertTrue(any("learning the GUI scale again" in line for line in logs), logs)
        self.assertEqual(bot.scale, 3)

    def test_a_stale_scale_is_corrected_when_the_gui_scale_changed(self):
        # Same window size, but the player changed the GUI scale from 6 to 3 in the options.
        client, _ = synth.make_client(3, CW, CH)
        bot, logs = run_bot(FakeDesktop(client), 2.8, {"gui_scale": 6, "gui_scale_client": [CW, CH]})
        self.assertTrue(any("looks like 3x now" in line for line in logs), logs)
        self.assertEqual(bot.scale, 3)

    def test_waits_when_there_is_no_game_window(self):
        client, _ = synth.make_client(4, CW, CH)
        bot, logs = run_bot(FakeDesktop(client), 0.4, resolver=lambda: None)
        self.assertEqual(bot.catches, 0)
        self.assertIsNone(bot.window)

    def test_waits_while_minimized(self):
        client, _ = synth.make_client(4, CW, CH)
        desktop = FakeDesktop(client)
        desktop.minimized = True
        bot, _ = run_bot(desktop, 0.4)
        self.assertEqual(bot.catches, 0)


class FocusGuardTests(unittest.TestCase):
    def make_bot(self, **cfg):
        bot = af.FishingBot(dict(af.DEFAULTS, **cfg), log=lambda _m: None)
        bot.window = fake_window(hwnd=42)
        return bot

    def test_clicks_only_while_the_game_window_itself_is_focused(self):
        bot = self.make_bot()
        with mock.patch.object(af.winutil, "foreground_hwnd", lambda: 42):
            self.assertTrue(bot._minecraft_focused())
        with mock.patch.object(af.winutil, "foreground_hwnd", lambda: 99):
            self.assertFalse(bot._minecraft_focused())

    def test_the_window_title_is_irrelevant_in_auto_mode(self):
        # A renamed modpack title must not block clicks (that was a v1.0.0 risk).
        bot = self.make_bot(window_title="Minecraft")
        bot.window = wu.WindowInfo(hwnd=42, title="COBBLEVERSE", cls="SDL_app", pid=1, exe="javaw.exe",
                                   width=CW, height=CH)
        with mock.patch.object(af.winutil, "foreground_hwnd", lambda: 42), \
                mock.patch.object(af, "foreground_window_title", lambda: "COBBLEVERSE"):
            self.assertTrue(bot._minecraft_focused())

    def test_focus_check_can_be_turned_off(self):
        bot = self.make_bot(require_focus=False)
        with mock.patch.object(af.winutil, "foreground_hwnd", lambda: 99):
            self.assertTrue(bot._minecraft_focused())

    def test_never_clicks_without_a_known_window(self):
        bot = af.FishingBot(dict(af.DEFAULTS), log=lambda _m: None)
        self.assertFalse(bot._minecraft_focused())


class ModeTests(unittest.TestCase):
    def test_manual_mode_still_needs_a_template(self):
        with self.assertRaises(ValueError):
            af.FishingBot(dict(af.DEFAULTS, mode="manual"), template=None)

    def test_auto_is_the_default_mode(self):
        self.assertEqual(af.DEFAULTS["mode"], "auto")
        self.assertTrue(af.FishingBot(dict(af.DEFAULTS)).auto)


if __name__ == "__main__":
    unittest.main()
