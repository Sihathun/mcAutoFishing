# AutoFishing

A Python auto-fisher for Minecraft (Java Edition, tested on Cobbleverse). It reads Minecraft's on-screen **subtitles**, and when it sees **"Fishing Bobber splashes"** it right-clicks to reel in and right-clicks again to recast.

It only looks at your screen and sends mouse clicks. It does not modify the game or read its memory.

> **Heads up:** Some servers ban or restrict automation. Check your server's rules before using this.

## Requirements

- Windows 10/11 (the `keyboard` library and screen capture are set up for Windows)
- Python 3.10 or newer
- Minecraft Java Edition running **windowed** or **borderless fullscreen** (exclusive fullscreen can't be captured)

## Setup

1. **Get the code**

   ```bash
   git clone <your-repo-url>
   cd AutoFishing
   ```

2. **(Optional) Create a virtual environment**

   ```bash
   python -m venv .venv
   .venv\Scripts\activate
   ```

3. **Install dependencies**

   ```bash
   pip install -r requirements.txt
   ```

4. **Turn on subtitles in Minecraft**

   `Options` → `Accessibility Settings` → `Show Subtitles: ON`

   The subtitle box appears in the bottom-right corner by default.

## Using the GUI (recommended)

```bash
python gui.py
```

You can also double-click `start_gui.bat`.

1. **Calibrate** (once per setup): click **Calibrate (F9)**, switch to Minecraft, cast your rod, and press **F9** the moment "Fishing Bobber splashes" shows in the subtitles. Back in the app, drag a box around the **whole subtitle area** (Confirm), then a **tight box around just the "Fishing Bobber splashes" text** (Confirm). No game screenshot handy? **From screenshot...** lets you calibrate from a saved image of your whole desktop.
2. **Test detection** (optional): watches the subtitles and reports splashes **without clicking**. The live view shows what the bot sees and the match bar shows how close it is to triggering.
3. **Start** (or **F6**), then switch to Minecraft and hold the rod. You have a few seconds (the *Start delay* setting).

| Key | Action |
| --- | --- |
| **F6** | Start |
| **F7** | Pause / resume |
| **F8** | Stop |
| **F9** | Capture the screen while calibrating |
| Mouse into a screen corner | Emergency abort (PyAutoGUI failsafe) |

The hotkeys work even while Minecraft has focus. All settings are editable in the app (hover a setting for an explanation) and are saved automatically.

**Safety net:** by default the bot only clicks while the focused window's title contains `Minecraft`. If you alt-tab away, it pauses clicking instead of right-clicking in another program. You can change the text or turn this off in the Settings box.

## Command line (alternative)

### Calibrate (once per setup)

Calibration teaches the script where the subtitles are on your screen and what the splash line looks like.

```bash
python autofish.py calibrate
```

1. Switch to Minecraft, hold a fishing rod and cast it.
2. When a fish bites and **"Fishing Bobber splashes"** shows in the subtitles, press **F9**. Subtitles fade after about 3 seconds, so be quick.
3. A window shows a screenshot of all your monitors. Drag a box around the **whole subtitle area** and press **Enter**.
4. A second window shows just that area. Drag a **tight box around only the "Fishing Bobber splashes" text** and press **Enter**.

The script prints a self-match score, which should be about `1.00`. It saves `config.json` and `template.png` next to the script.

You can also calibrate from an existing screenshot of your whole desktop: `python autofish.py calibrate --image screenshot.png`.

**Re-calibrate** if you change resolution, GUI scale or subtitle position, or if you move the Minecraft window.

### Run

```bash
python autofish.py run
```

Then switch to Minecraft with the fishing rod in your hand. The script starts after a countdown (`start_delay`).

Press **F7** to pause/resume and **F8** to quit. Use `python autofish.py run --debug` to print the live match score.

## Configuration

`config.json` is created by calibration and saved by the GUI. You can edit it by hand too:

| Setting | Default | Meaning |
| --- | --- | --- |
| `match_threshold` | `0.80` | How closely the screen must match the splash line (0 to 1). Lower it if bites are missed, raise it if you get false triggers. |
| `reel_delay` | `0.15` | Seconds between detecting the splash and reeling in |
| `recast_delay` | `1.0` | Seconds between reeling in and recasting |
| `cooldown` | `4.0` | Seconds to ignore the screen after a catch, so the lingering subtitle doesn't trigger twice |
| `poll_interval` | `0.05` | Seconds between screen checks |
| `start_delay` | `5` | Seconds to switch to Minecraft after pressing Start |
| `require_focus` | `true` | Only click while the focused window's title contains `window_title` |
| `window_title` | `"Minecraft"` | Text the focused window's title must contain |
| `monitor` | `0` | Capture target. `0` means all monitors combined. |

## Troubleshooting

- **Calibration screenshot isn't Minecraft:** check `calibration_screenshot.png`. Make sure the game window is visible and not in exclusive fullscreen.
- **Never triggers:** use **Test detection** in the GUI (or `run --debug`) and watch the match bar when a splash appears. If it stays below the threshold, re-calibrate with a tighter box on the text or lower the match threshold.
- **Log says "Splash seen but not clicking: focused window is ...":** the focus safety net is blocking clicks because your game window's title doesn't contain the configured text. Change the text in Settings to match, or untick the option.
- **Triggers at random:** raise `match_threshold` (for example `0.9`).
- **Clicks don't register in the game:** make sure the Minecraft window is focused and the mouse is captured (in-game, not in a menu).
- **Hotkeys don't work:** try running the terminal as administrator.

## How it works

1. `mss` captures the subtitle region of the screen.
2. OpenCV template matching (`TM_CCOEFF_NORMED` on grayscale) looks for the saved "Fishing Bobber splashes" image. This method ignores brightness, so it still matches when the subtitle text has faded.
3. On a match, `pyautogui` right-clicks to reel in and right-clicks again to recast, then waits out the cooldown.

## Files

- `gui.py`: the graphical app (calibration wizard, live view, settings, start/stop)
- `start_gui.bat`: double-click launcher for the GUI
- `autofish.py`: core logic (screen capture, matching, the fishing bot) plus the command-line interface
- `requirements.txt`: Python dependencies
- `config.json`, `template.png`, `calibration_screenshot.png`: generated per machine and git-ignored
# mcAutoFishing
