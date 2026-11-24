# Changelog

All notable changes to AutoFishing. The entry for each version is used as the GitHub Release notes,
so write it for the people downloading the app, not for developers.

Format: one `## [x.y.z] - YYYY-MM-DD` section per release, newest first. Put work in progress under
`## [Unreleased]`; when releasing, rename it to the new version and add a fresh empty `[Unreleased]`.
Group bullets under **Added**, **Changed**, **Fixed** or **Removed**.

## [Unreleased]

## [1.1.0-beta.1] - 2026-11-24

### Added
- **Automatic setup: no more calibrating.** Turn on subtitles, press Start, and cast. The app finds your Minecraft window and learns your in-game GUI size from the first "Fishing Bobber splashes" bite, then reels that fish in and remembers the size for next time.
- **Game window picker.** A dropdown lists every open window. Minecraft is detected automatically, but if the app picks the wrong program you can choose the right one yourself (↻ refreshes the list). Your choice is remembered.
- **Setup status at a glance:** the app shows which window it is using, and whether your GUI size is known or still being learned.
- It re-learns the GUI size automatically if you resize the game window or change the GUI scale in Minecraft's video settings.
- Clear status messages when there is no Minecraft window, or when it is minimized or can't be captured.

### Changed
- The "only click while Minecraft is focused" safety net now follows the game window itself instead of matching its title. Modpacks that rename the window (for example "COBBLEVERSE") no longer risk blocking clicks.
- Manual calibration is now an advanced option (choose **Manual calibration**), for non-English games or font resource packs. Existing manual calibrations keep working.
- The command-line `run` also uses automatic setup, so it no longer needs calibrating first.

## [1.0.0] - 2026-11-24

First public release.

### Added
- Standalone **AutoFishing.exe** for Windows. Download it from this page and double-click, no Python needed.
- Graphical app: calibration wizard, live view of what the bot sees, a match meter, a catch counter, a log and editable settings.
- Auto-fishing driven by Minecraft's subtitles: when "Fishing Bobber splashes" appears, it right-clicks to reel in and recasts.
- **Test detection** mode that watches and reports splashes without ever clicking, so you can check your setup safely.
- Global hotkeys that work while Minecraft is focused: **F6** start, **F7** pause/resume, **F8** stop, **F9** capture during calibration.
- Multi-monitor support: calibration captures all screens, so Minecraft can be on any monitor.
- Safety net: the bot only clicks while the focused window's title contains "Minecraft" (configurable), and moving the mouse into a screen corner aborts it.
- Command-line mode (`autofish.py calibrate` / `run`) for running from source.

### Notes
- Windows may show a SmartScreen warning because the app is not code-signed: click **More info**, then **Run anyway**.
- Settings and calibration are stored in `%APPDATA%\AutoFishing`.
