import base64
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import appicon  # noqa: E402
import autofish as af  # noqa: E402
import theme  # noqa: E402
import winutil  # noqa: E402


def luminance(color: str) -> float:
    def channel(v):
        v /= 255
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    r, g, b = (int(color[i:i + 2], 16) for i in (1, 3, 5))
    return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)


def contrast(a: str, b: str) -> float:
    hi, lo = sorted((luminance(a), luminance(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


BOT_STATES = ("idle", "starting", "waiting", "learning", "watching", "testing", "reeling", "cooldown",
              "paused", "warn", "wait")


class PaletteTests(unittest.TestCase):
    def test_light_and_dark_define_the_same_keys(self):
        self.assertEqual(set(theme.LIGHT), set(theme.DARK))

    def test_all_colours_are_valid_hex(self):
        for name, palette in theme.PALETTES.items():
            for key, value in palette.items():
                with self.subTest(theme=name, key=key):
                    self.assertRegex(value, r"^#[0-9a-fA-F]{6}$")

    def test_body_text_is_readable(self):
        for name, p in theme.PALETTES.items():
            for fg, bg in (("fg", "bg"), ("fg", "field"), ("log_fg", "log_bg"), ("tip_fg", "tip_bg"),
                           ("ok", "bg"), ("bad", "bg"), ("muted", "bg")):
                with self.subTest(theme=name, pair=f"{fg} on {bg}"):
                    self.assertGreaterEqual(contrast(p[fg], p[bg]), 4.5)

    def test_banner_colours_exist_for_every_state_in_both_themes(self):
        for mode in ("light", "dark"):
            for state in BOT_STATES:
                with self.subTest(mode=mode, state=state):
                    bg, fg = theme.STATE_COLORS[mode][state]
                    self.assertGreaterEqual(contrast(bg, fg), 4.5)

    def test_shade(self):
        self.assertEqual(theme.shade("#000000", 1.0), "#ffffff")
        self.assertEqual(theme.shade("#ffffff", -1.0), "#000000")
        self.assertEqual(theme.shade("#808080", 0.0), "#808080")


class ChoiceTests(unittest.TestCase):
    def test_explicit_choices_win(self):
        self.assertEqual(theme.resolve("light"), "light")
        self.assertEqual(theme.resolve("dark"), "dark")

    def test_system_follows_windows(self):
        with mock.patch.object(theme, "system_prefers_dark", lambda: True):
            self.assertEqual(theme.resolve("system"), "dark")
        with mock.patch.object(theme, "system_prefers_dark", lambda: False):
            self.assertEqual(theme.resolve("system"), "light")

    def test_unknown_values_fall_back_to_the_system_setting(self):
        with mock.patch.object(theme, "system_prefers_dark", lambda: True):
            self.assertEqual(theme.resolve("rainbow"), "dark")

    def test_registry_problems_mean_light(self):
        with mock.patch("winreg.OpenKey", side_effect=OSError):
            self.assertFalse(theme.system_prefers_dark())

    def test_default_is_to_follow_windows(self):
        self.assertEqual(af.DEFAULTS["theme"], "system")
        self.assertEqual(theme.THEME_CHOICES[0], "system")


class IconTests(unittest.TestCase):
    def test_embedded_icon_is_a_square_rgba_png_with_a_transparent_background(self):
        im = Image.open(io.BytesIO(base64.b64decode(appicon.ICON_PNG_B64)))
        self.assertEqual(im.format, "PNG")
        self.assertEqual(im.size, (256, 256))
        alpha = im.convert("RGBA").getchannel("A")
        lo, hi = alpha.getextrema()
        self.assertEqual((lo, hi), (0, 255))                  # transparent corners, solid rod
        self.assertEqual(alpha.getpixel((0, 0)), 0)

    def test_ico_file_has_every_size_windows_asks_for(self):
        with Image.open(ROOT / "assets" / "AutoFishing.ico") as ico:
            sizes = {s[0] for s in ico.info["sizes"]}
        self.assertTrue({16, 24, 32, 48, 64, 128, 256} <= sizes, sizes)

    def test_logo_source_is_kept(self):
        self.assertTrue((ROOT / "assets" / "logo_source.png").exists())

    def test_spec_uses_the_icon(self):
        spec = (ROOT / "AutoFishing.spec").read_text(encoding="utf-8")
        self.assertIn("icon=ICON", spec)
        self.assertTrue((ROOT / "assets" / "AutoFishing.ico").exists())


FAKE_GAME = winutil.WindowInfo(hwnd=1, title="Fake Minecraft", cls="SDL_app", pid=1, exe="javaw.exe",
                               width=1280, height=720)


class GuiSmokeTests(unittest.TestCase):
    """
    Builds the real window (no bot started) and flips between themes.

    The set of open windows is faked, so the results never depend on whether Minecraft happens to be running
    on the machine (it is on a developer's PC, it is not on the CI runner).
    """

    def build_app(self, tmp, windows, theme_choice="dark"):
        import gui   # imported here: it requests DPI awareness and an app id at import time
        (Path(tmp) / "config.json").write_text(json.dumps({"theme": theme_choice}))
        with mock.patch.object(winutil, "list_windows", lambda include_self=False: list(windows)):
            return gui, gui.App()

    def test_banner_says_so_when_there_is_no_game_window(self):
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(af, "CONFIG_PATH", Path(tmp) / "config.json"), \
                mock.patch.object(af, "TEMPLATE_PATH", Path(tmp) / "missing.png"), \
                mock.patch.object(winutil, "list_windows", lambda include_self=False: []):
            for choice in ("light", "dark"):
                with self.subTest(theme=choice):
                    _, app = self.build_app(tmp, [], choice)
                    try:
                        app.update()
                        self.assertIn("No Minecraft window found", app.banner.cget("text"))
                        self.assertEqual(app.header.cget("bg"), theme.STATE_COLORS[choice]["warn"][0])
                    finally:
                        app._on_close()

    def test_window_themes_and_settings_dialog(self):
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(af, "CONFIG_PATH", Path(tmp) / "config.json"), \
                mock.patch.object(af, "TEMPLATE_PATH", Path(tmp) / "missing.png"), \
                mock.patch.object(winutil, "list_windows", lambda include_self=False: [FAKE_GAME]):
            gui, app = self.build_app(tmp, [FAKE_GAME])
            try:
                app.update()
                self.assertEqual(app.mode, "dark")
                self.assertEqual(len(app._icons), len(gui.ICON_SIZES))

                app._open_settings()
                app.update()
                dlg = app._settings_dlg
                self.assertTrue(dlg.winfo_exists())
                app._open_settings()               # a second click must not open a second dialog
                self.assertIs(app._settings_dlg, dlg)

                for choice, expected in (("light", "light"), ("dark", "dark")):
                    app.theme_choice.set(choice)
                    app._on_theme_change()
                    app.update()
                    self.assertEqual(app.mode, expected)
                    self.assertEqual(json.loads((Path(tmp) / "config.json").read_text())["theme"], choice)
                    self.assertEqual(app.header.cget("bg"), theme.STATE_COLORS[expected]["idle"][0])

                with mock.patch.object(theme, "system_prefers_dark", lambda: True):
                    app.theme_choice.set("system")
                    app._on_theme_change()
                    self.assertEqual(app.mode, "dark")
            finally:
                if app._settings_dlg is not None and app._settings_dlg.winfo_exists():
                    app._settings_dlg.destroy()
                app._on_close()


if __name__ == "__main__":
    unittest.main()
