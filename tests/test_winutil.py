import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import winutil as wu  # noqa: E402


def win(hwnd=1, title="", cls="", exe="", w=1280, h=720, minimized=False, pid=1):
    return wu.WindowInfo(hwnd=hwnd, title=title, cls=cls, pid=pid, exe=exe, width=w, height=h, minimized=minimized)


class MinecraftScoreTests(unittest.TestCase):
    def test_recognises_java_edition_windows(self):
        self.assertEqual(wu.minecraft_score(win(title="Minecraft* 26.3 - Singleplayer", cls="SDL_app", exe="javaw.exe")), 110)
        self.assertEqual(wu.minecraft_score(win(title="Minecraft 1.21", cls="GLFW30", exe="java.exe")), 110)

    def test_modpack_with_a_renamed_title_is_still_found_by_class(self):
        # The owner's modpack once showed "COBBLEVERSE" as the title.
        self.assertEqual(wu.minecraft_score(win(title="COBBLEVERSE", cls="GLFW30", exe="javaw.exe")), 90)
        self.assertEqual(wu.minecraft_score(win(title="COBBLEVERSE", cls="SDL_app", exe="javaw.exe")), 90)

    def test_title_alone_is_enough_for_a_java_process(self):
        self.assertEqual(wu.minecraft_score(win(title="Minecraft 1.8.9", cls="SunAwtFrame", exe="javaw.exe")), 80)

    def test_things_that_are_not_the_game_score_zero(self):
        for w in (
            win(title="Minecraft Wiki - Mozilla Firefox", cls="MozillaWindowClass", exe="firefox.exe"),
            win(title="Prism Launcher - Minecraft", cls="Qt6QWindowIcon", exe="prismlauncher.exe"),
            win(title="Minecraft Launcher", cls="Chrome_WidgetWin_1", exe="minecraft.exe"),
            win(title="JDownloader 2", cls="SunAwtFrame", exe="javaw.exe"),     # Java, but not Minecraft
            win(title="Some game", cls="SDL_app", exe="game.exe"),               # right class, not Java
            win(title="", cls="GLFW30", exe=""),
        ):
            with self.subTest(title=w.title, exe=w.exe):
                self.assertEqual(wu.minecraft_score(w), 0)


class FindAndResolveTests(unittest.TestCase):
    def setUp(self):
        self.game = win(10, "Minecraft* 26.3", "SDL_app", "javaw.exe", 2560, 1351)
        self.second = win(11, "Minecraft 1.21 (alt)", "GLFW30", "javaw.exe", 800, 600)
        self.discord = win(12, "Discord", "Chrome_WidgetWin_1", "discord.exe")
        self.mini = win(13, "Minecraft* 26.3", "SDL_app", "javaw.exe", 0, 0, minimized=True)
        self.all = [self.discord, self.second, self.game]

    def test_picks_the_best_candidate(self):
        self.assertIs(wu.find_minecraft(self.all), self.game)     # higher score and bigger

    def test_prefers_a_visible_window_over_a_minimized_one(self):
        self.assertIs(wu.find_minecraft([self.mini, self.game]), self.game)

    def test_none_when_there_is_no_game(self):
        self.assertIsNone(wu.find_minecraft([self.discord]))
        self.assertIsNone(wu.find_minecraft([]))

    def test_user_choice_beats_auto_detection(self):
        self.assertIs(wu.resolve_target(pinned_hwnd=12, windows=self.all), self.discord)

    def test_remembered_choice_is_found_again_after_a_restart(self):
        hints = self.second.hints()
        reopened = win(99, "Minecraft 1.21 (alt)", "GLFW30", "javaw.exe", 800, 600)   # new handle, same program
        self.assertIs(wu.resolve_target(hints=hints, windows=[self.game, reopened]), reopened)

    def test_remembered_choice_prefers_the_same_title(self):
        a = win(1, "World A", "SDL_app", "javaw.exe", 800, 600)
        b = win(2, "World B", "SDL_app", "javaw.exe", 1600, 900)
        self.assertIs(wu.match_hints([a, b], {"exe": "javaw.exe", "cls": "SDL_app", "title": "World A"}), a)

    def test_stale_choices_fall_back_to_auto_detect(self):
        self.assertIs(wu.resolve_target({"exe": "gone.exe", "cls": "x", "title": "y"}, pinned_hwnd=404,
                                        windows=self.all), self.game)

    def test_nothing_found(self):
        self.assertIsNone(wu.resolve_target(windows=[self.discord]))


class DropdownLabelTests(unittest.TestCase):
    def test_labels(self):
        self.assertTrue(win(title="Minecraft*", cls="SDL_app", exe="javaw.exe").label().startswith("★"))
        self.assertNotIn("★", win(title="Discord", exe="discord.exe").label())
        self.assertIn("minimized", win(title="X", exe="a.exe", minimized=True).label())
        self.assertLessEqual(len(win(title="T" * 200, exe="a.exe").label()), 90)


class LiveWindowTests(unittest.TestCase):
    """Touches the real desktop, but read-only."""

    def test_list_windows_runs_and_excludes_this_process(self):
        import os
        windows = wu.list_windows()
        self.assertIsInstance(windows, list)
        for w in windows:
            self.assertIsInstance(w, wu.WindowInfo)
            self.assertNotEqual(w.pid, os.getpid())
            self.assertTrue(w.title)

    def test_bad_handles_are_handled(self):
        self.assertFalse(wu.is_window(0))
        self.assertIsNone(wu.window_info(0))


if __name__ == "__main__":
    unittest.main()
