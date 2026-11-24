"""
Windows helpers: list application windows, find the Minecraft one, read a window's client area.

Windows only (ctypes + user32). Coordinates are physical screen pixels, so the process must be
DPI-aware; that is requested on import (a no-op if the app already did it).
"""
import ctypes
import os
from ctypes import wintypes as wt
from dataclasses import dataclass

try:
    ctypes.windll.shcore.SetProcessDpiAwareness(1)
except Exception:
    pass

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
dwmapi = ctypes.WinDLL("dwmapi")

EnumWindowsProc = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
user32.EnumWindows.argtypes = [EnumWindowsProc, wt.LPARAM]
user32.IsWindow.argtypes = [wt.HWND]
user32.IsWindowVisible.argtypes = [wt.HWND]
user32.IsIconic.argtypes = [wt.HWND]
user32.GetWindowTextLengthW.argtypes = [wt.HWND]
user32.GetWindowTextW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
user32.GetClassNameW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
user32.GetWindowThreadProcessId.argtypes = [wt.HWND, ctypes.POINTER(wt.DWORD)]
user32.GetClientRect.argtypes = [wt.HWND, ctypes.POINTER(wt.RECT)]
user32.ClientToScreen.argtypes = [wt.HWND, ctypes.POINTER(wt.POINT)]
user32.GetWindowLongW.argtypes = [wt.HWND, ctypes.c_int]
user32.GetWindowLongW.restype = wt.LONG
user32.GetForegroundWindow.restype = wt.HWND
kernel32.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
kernel32.OpenProcess.restype = wt.HANDLE
kernel32.QueryFullProcessImageNameW.argtypes = [wt.HANDLE, wt.DWORD, wt.LPWSTR, ctypes.POINTER(wt.DWORD)]
kernel32.CloseHandle.argtypes = [wt.HANDLE]
dwmapi.DwmGetWindowAttribute.argtypes = [wt.HWND, wt.DWORD, ctypes.c_void_p, wt.DWORD]

GWL_EXSTYLE = -20
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_APPWINDOW = 0x00040000
DWMWA_CLOAKED = 14
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

JAVA_EXES = {"javaw.exe", "java.exe"}
# Java Edition's window class: GLFW30 on older versions, SDL_app on newer ones (seen on 26.x).
GAME_CLASSES = {"GLFW30", "SDL_app"}
SHELL_CLASSES = {"Progman", "WorkerW", "Shell_TrayWnd", "Shell_SecondaryTrayWnd"}

_exe_cache: dict[int, str] = {}


@dataclass(frozen=True)
class WindowInfo:
    hwnd: int
    title: str
    cls: str
    pid: int
    exe: str          # lower-case file name, e.g. "javaw.exe" ("" if it couldn't be read)
    width: int        # client-area size in pixels (0 when minimized)
    height: int
    minimized: bool = False

    @property
    def minecraft_score(self) -> int:
        return minecraft_score(self)

    def hints(self) -> dict:
        """What we remember about a window to find it again after a restart (handles aren't stable)."""
        return {"exe": self.exe, "cls": self.cls, "title": self.title}

    def label(self) -> str:
        size = "minimized" if self.minimized else f"{self.width}x{self.height}"
        star = "★ " if self.minecraft_score else ""
        title = self.title if len(self.title) <= 56 else self.title[:55] + "…"
        return f"{star}{title}  [{self.exe or '?'}, {size}]"


# --------------------------------------------------------------------------- #
# Low-level queries
# --------------------------------------------------------------------------- #
def _text(hwnd: int) -> str:
    n = user32.GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(n + 1)
    user32.GetWindowTextW(hwnd, buf, n + 1)
    return buf.value


def _class(hwnd: int) -> str:
    buf = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, buf, 256)
    return buf.value


def _exe_name(pid: int) -> str:
    if pid in _exe_cache:
        return _exe_cache[pid]
    name = ""
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if handle:
        try:
            buf = ctypes.create_unicode_buffer(1024)
            size = wt.DWORD(1024)
            if kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
                name = os.path.basename(buf.value).lower()
        finally:
            kernel32.CloseHandle(handle)
    _exe_cache[pid] = name
    return name


def _is_cloaked(hwnd: int) -> bool:
    """True for windows Windows hides on purpose (e.g. UWP apps on another virtual desktop)."""
    value = wt.DWORD(0)
    if dwmapi.DwmGetWindowAttribute(hwnd, DWMWA_CLOAKED, ctypes.byref(value), ctypes.sizeof(value)) == 0:
        return value.value != 0
    return False


def is_window(hwnd: int) -> bool:
    return bool(hwnd) and bool(user32.IsWindow(hwnd))


def is_minimized(hwnd: int) -> bool:
    return bool(user32.IsIconic(hwnd))


def foreground_hwnd() -> int:
    return int(user32.GetForegroundWindow() or 0)


def client_rect(hwnd: int):
    """(left, top, width, height) of the window's client area in screen pixels, or None."""
    rect = wt.RECT()
    if not user32.GetClientRect(hwnd, ctypes.byref(rect)):
        return None
    origin = wt.POINT(0, 0)
    if not user32.ClientToScreen(hwnd, ctypes.byref(origin)):
        return None
    if rect.right <= 0 or rect.bottom <= 0:
        return None
    return origin.x, origin.y, rect.right, rect.bottom


def window_info(hwnd: int):
    """Describe one window (None if it is gone)."""
    if not is_window(hwnd):
        return None
    pid = wt.DWORD(0)
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    minimized = is_minimized(hwnd)
    rect = client_rect(hwnd)
    return WindowInfo(
        hwnd=int(hwnd), title=_text(hwnd), cls=_class(hwnd), pid=pid.value, exe=_exe_name(pid.value),
        width=rect[2] if rect else 0, height=rect[3] if rect else 0, minimized=minimized,
    )


# --------------------------------------------------------------------------- #
# Finding windows
# --------------------------------------------------------------------------- #
def minecraft_score(w: WindowInfo) -> int:
    """
    How likely a window is Minecraft Java Edition (0 = not a candidate).

    It must be a Java process AND look like the game (known window class or "Minecraft" in the title).
    The title alone is not trusted: modpacks rename it (e.g. "COBBLEVERSE") and launchers/browsers
    mention Minecraft too.
    """
    class_ok = w.cls in GAME_CLASSES
    title_ok = "minecraft" in w.title.lower()
    if w.exe not in JAVA_EXES or not (class_ok or title_ok):
        return 0
    return 60 + (30 if class_ok else 0) + (20 if title_ok else 0)


def list_windows(include_self: bool = False) -> list[WindowInfo]:
    """
    Visible top-level application windows (what you'd see in Alt+Tab), Minecraft candidates first.
    """
    found: list[WindowInfo] = []
    me = os.getpid()

    @EnumWindowsProc
    def callback(hwnd, _lparam):
        hwnd = int(hwnd)
        if not user32.IsWindowVisible(hwnd) or _is_cloaked(hwnd):
            return True
        ex_style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        if ex_style & WS_EX_TOOLWINDOW and not ex_style & WS_EX_APPWINDOW:
            return True
        if not _text(hwnd) or _class(hwnd) in SHELL_CLASSES:
            return True
        info = window_info(hwnd)
        if info is None or (not include_self and info.pid == me):
            return True
        if not info.minimized and (info.width <= 0 or info.height <= 0):
            return True
        found.append(info)
        return True

    user32.EnumWindows(callback, 0)
    found.sort(key=lambda w: (-w.minecraft_score, w.title.lower()))
    return found


def find_minecraft(windows) -> "WindowInfo | None":
    """The most likely Minecraft window: best score, then the biggest, preferring non-minimized."""
    candidates = [w for w in windows if w.minecraft_score]
    if not candidates:
        return None
    return max(candidates, key=lambda w: (w.minecraft_score, not w.minimized, w.width * w.height))


def match_hints(windows, hints: dict) -> "WindowInfo | None":
    """Find a previously chosen window again: same program + window class, preferring the same title."""
    same = [w for w in windows if w.exe == hints.get("exe") and w.cls == hints.get("cls")]
    if not same:
        return None
    exact = [w for w in same if w.title == hints.get("title")]
    return max(exact or same, key=lambda w: w.width * w.height)


def resolve_target(hints: "dict | None" = None, pinned_hwnd: "int | None" = None, windows=None):
    """
    Pick the game window: the exact window the user chose (if it still exists), else the one matching their
    remembered choice, else automatic Minecraft detection.
    """
    windows = list_windows() if windows is None else windows
    if pinned_hwnd:
        for w in windows:
            if w.hwnd == pinned_hwnd:
                return w
    if hints:
        match = match_hints(windows, hints)
        if match:
            return match
    return find_minecraft(windows)
