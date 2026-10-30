"""Small, fail-closed Windows input backend.

This is an input limiter, not an operating-system sandbox. SendInput is a global
input stream: the foreground check cannot be atomic with the next input event.
No keyboard hooks, drivers, privilege escalation, or arbitrary Win32 messages are
exposed to a chat command. The module can be imported on other operating systems.
"""

from __future__ import annotations

import ctypes
import ntpath
import os
import secrets
import sys
import threading
import time
from dataclasses import dataclass
from typing import Callable

DWORD = ctypes.c_uint32
WORD = ctypes.c_uint16
LONG = ctypes.c_int32
BOOL = ctypes.c_int32
UINT = ctypes.c_uint32
ULONG_PTR = ctypes.c_size_t
HANDLE = ctypes.c_void_p
HWND = ctypes.c_void_p
LPARAM = ctypes.c_ssize_t
WPARAM = ctypes.c_size_t


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", LONG),
        ("dy", LONG),
        ("mouseData", DWORD),
        ("dwFlags", DWORD),
        ("time", DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", WORD),
        ("wScan", WORD),
        ("dwFlags", DWORD),
        ("time", DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [("uMsg", DWORD), ("wParamL", WORD), ("wParamH", WORD)]


class INPUT_UNION(ctypes.Union):
    # Keeping the complete union is required: keyboard-only definitions give the
    # wrong cbSize to SendInput, especially in 64-bit Python.
    _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT)]


class INPUT(ctypes.Structure):
    _anonymous_ = ("data",)
    _fields_ = [("type", DWORD), ("data", INPUT_UNION)]


class FILETIME(ctypes.Structure):
    _fields_ = [("low", DWORD), ("high", DWORD)]


class POINT(ctypes.Structure):
    _fields_ = [("x", LONG), ("y", LONG)]


class MSG(ctypes.Structure):
    _fields_ = [
        ("hwnd", HWND),
        ("message", UINT),
        ("wParam", WPARAM),
        ("lParam", LPARAM),
        ("time", DWORD),
        ("pt", POINT),
        ("lPrivate", DWORD),
    ]


class SID_AND_ATTRIBUTES(ctypes.Structure):
    _fields_ = [("Sid", ctypes.c_void_p), ("Attributes", DWORD)]


class TOKEN_MANDATORY_LABEL(ctypes.Structure):
    _fields_ = [("Label", SID_AND_ATTRIBUTES)]


SAFE_KEYS = frozenset("abcdefghijklmnopqrstuvwxyz0123456789") | {"left", "right", "up", "down", "space"}
KEY_VK = {key: ord(key.upper()) for key in SAFE_KEYS if len(key) == 1}
KEY_VK.update(left=0x25, up=0x26, right=0x27, down=0x28, space=0x20)
EXTENDED_KEYS = frozenset({"left", "up", "right", "down"})
MODIFIER_VKS = (
    0x10,
    0x11,
    0x12,
    0x5B,
    0x5C,
    0xA0,
    0xA1,
    0xA2,
    0xA3,
    0xA4,
    0xA5,
)  # Shift, Ctrl, Alt, Win, both sides.
MAX_HOLD_SECONDS = 0.500
MONITOR_INTERVAL_SECONDS = 0.010
HOTKEY_ID = 0x5443
VK_F8 = 0x77
WM_HOTKEY = 0x0312

# This is defense in depth against accidental selection, not executable trust.
# A local attacker can rename a binary; the supported threat is hostile chat,
# not hostile software already executing in the streamer's Windows account.
DENIED_EXECUTABLES = frozenset(
    {
        "explorer.exe",
        "cmd.exe",
        "conhost.exe",
        "openconsole.exe",
        "powershell.exe",
        "powershell_ise.exe",
        "pwsh.exe",
        "wt.exe",
        "windowsterminal.exe",
        "wsl.exe",
        "wslhost.exe",
        "bash.exe",
        "sh.exe",
        "python.exe",
        "pythonw.exe",
        "py.exe",
        "node.exe",
        "cscript.exe",
        "wscript.exe",
        "mshta.exe",
        "rundll32.exe",
        "regedit.exe",
        "mmc.exe",
        "taskmgr.exe",
        "control.exe",
        "systemsettings.exe",
        "searchhost.exe",
        "startmenuexperiencehost.exe",
        "shellexperiencehost.exe",
        "applicationframehost.exe",
        "chrome.exe",
        "msedge.exe",
        "firefox.exe",
        "brave.exe",
        "opera.exe",
        "iexplore.exe",
        "vivaldi.exe",
        "librewolf.exe",
        "arc.exe",
        "notepad.exe",
        "notepad++.exe",
        "code.exe",
        "devenv.exe",
        "windowsterminalpreview.exe",
        "mintty.exe",
        "putty.exe",
        "kitty.exe",
        "discord.exe",
        "slack.exe",
        "teams.exe",
        "ms-teams.exe",
        "outlook.exe",
        "winword.exe",
        "excel.exe",
        "obs64.exe",
        "obs32.exe",
        "twitchchatcontrol.exe",
        "twitch-chat-control.exe",
    }
)
DENIED_CLASSES = frozenset(
    {
        "progman",
        "workerw",
        "shell_traywnd",
        "shell_secondarytraywnd",
        "consolewindowclass",
        "cascadia_hosting_window_class",
        "#32770",
    }
)


class InputSafetyError(RuntimeError):
    """A bounded, nonsecret description of a rejected native input operation."""


@dataclass(frozen=True)
class WindowTarget:
    hwnd: int
    pid: int
    creation_time: int
    executable: str
    title: str
    class_name: str = ""
    thread_id: int = 0

    @property
    def label(self) -> str:
        name = ntpath.basename(self.executable)
        return f"{self.title} — {name} (PID {self.pid})"

    @property
    def display_label(self) -> str:
        return self.label


def _safe_title(title: str) -> str:
    # Never render control characters or bidi overrides from a window title.
    return "".join(
        c
        for c in title[:240]
        if c.isprintable() and not 0x202A <= ord(c) <= 0x202E and not 0x2066 <= ord(c) <= 0x2069
    ).strip()


def _acceptable_target(target: WindowTarget, own_pid: int) -> bool:
    return bool(
        target.hwnd
        and target.pid > 0
        and target.pid != own_pid
        and target.creation_time > 0
        and target.executable
        and target.title
        and ntpath.isabs(target.executable)
        and ntpath.basename(target.executable).casefold() not in DENIED_EXECUTABLES
        and target.class_name.casefold() not in DENIED_CLASSES
    )


class _Win32API:
    """Native calls are separated so failure paths can be tested without Windows."""

    def __init__(self) -> None:
        if sys.platform != "win32":
            raise OSError("Windows input is unavailable on this operating system.")
        self.user32 = ctypes.WinDLL("user32", use_last_error=True)
        self.kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self.advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
        self.wtsapi32 = ctypes.WinDLL("wtsapi32", use_last_error=True)
        self._enum_proc = ctypes.WINFUNCTYPE(BOOL, HWND, LPARAM)
        self._bind()

    def _bind(self) -> None:
        declarations = (
            (self.user32, "EnumWindows", [self._enum_proc, LPARAM], BOOL),
            (self.user32, "IsWindow", [HWND], BOOL),
            (self.user32, "IsWindowVisible", [HWND], BOOL),
            (self.user32, "IsIconic", [HWND], BOOL),
            (self.user32, "GetWindowTextW", [HWND, ctypes.c_wchar_p, ctypes.c_int], ctypes.c_int),
            (self.user32, "GetClassNameW", [HWND, ctypes.c_wchar_p, ctypes.c_int], ctypes.c_int),
            (self.user32, "GetWindowThreadProcessId", [HWND, ctypes.POINTER(DWORD)], DWORD),
            (self.user32, "GetForegroundWindow", [], HWND),
            (self.user32, "OpenInputDesktop", [DWORD, BOOL, DWORD], HANDLE),
            (self.user32, "CloseDesktop", [HANDLE], BOOL),
            (self.user32, "GetThreadDesktop", [DWORD], HANDLE),
            (
                self.user32,
                "GetUserObjectInformationW",
                [HANDLE, ctypes.c_int, ctypes.c_void_p, DWORD, ctypes.POINTER(DWORD)],
                BOOL,
            ),
            (self.user32, "GetAsyncKeyState", [ctypes.c_int], ctypes.c_int16),
            (self.user32, "SendInput", [UINT, ctypes.POINTER(INPUT), ctypes.c_int], UINT),
            (self.user32, "MapVirtualKeyW", [UINT, UINT], UINT),
            (self.user32, "SetPropW", [HWND, ctypes.c_wchar_p, HANDLE], BOOL),
            (self.user32, "GetPropW", [HWND, ctypes.c_wchar_p], HANDLE),
            (self.user32, "RemovePropW", [HWND, ctypes.c_wchar_p], HANDLE),
            (self.user32, "RegisterHotKey", [HWND, ctypes.c_int, UINT, UINT], BOOL),
            (self.user32, "UnregisterHotKey", [HWND, ctypes.c_int], BOOL),
            (self.user32, "PeekMessageW", [ctypes.POINTER(MSG), HWND, UINT, UINT, UINT], BOOL),
            (self.kernel32, "OpenProcess", [DWORD, BOOL, DWORD], HANDLE),
            (self.kernel32, "GetCurrentThreadId", [], DWORD),
            (self.kernel32, "ProcessIdToSessionId", [DWORD, ctypes.POINTER(DWORD)], BOOL),
            (
                self.wtsapi32,
                "WTSQuerySessionInformationW",
                [HANDLE, DWORD, ctypes.c_int, ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(DWORD)],
                BOOL,
            ),
            (self.wtsapi32, "WTSFreeMemory", [ctypes.c_void_p], None),
            (self.kernel32, "CloseHandle", [HANDLE], BOOL),
            (
                self.kernel32,
                "GetProcessTimes",
                [
                    HANDLE,
                    ctypes.POINTER(FILETIME),
                    ctypes.POINTER(FILETIME),
                    ctypes.POINTER(FILETIME),
                    ctypes.POINTER(FILETIME),
                ],
                BOOL,
            ),
            (
                self.kernel32,
                "QueryFullProcessImageNameW",
                [HANDLE, DWORD, ctypes.c_wchar_p, ctypes.POINTER(DWORD)],
                BOOL,
            ),
            (self.advapi32, "OpenProcessToken", [HANDLE, DWORD, ctypes.POINTER(HANDLE)], BOOL),
            (
                self.advapi32,
                "GetTokenInformation",
                [HANDLE, ctypes.c_int, ctypes.c_void_p, DWORD, ctypes.POINTER(DWORD)],
                BOOL,
            ),
            (self.advapi32, "GetSidSubAuthorityCount", [ctypes.c_void_p], ctypes.POINTER(ctypes.c_ubyte)),
            (self.advapi32, "GetSidSubAuthority", [ctypes.c_void_p, DWORD], ctypes.POINTER(DWORD)),
        )
        for library, name, args, result in declarations:
            function = getattr(library, name)
            function.argtypes = args
            function.restype = result

    def _unprivileged(self, process: int) -> bool:
        token = HANDLE()
        if not self.advapi32.OpenProcessToken(process, 0x0008, ctypes.byref(token)):
            return False
        try:
            elevation, size = DWORD(), DWORD()
            if not self.advapi32.GetTokenInformation(
                token, 20, ctypes.byref(elevation), ctypes.sizeof(elevation), ctypes.byref(size)
            ):
                return False
            if elevation.value:
                return False
            self.advapi32.GetTokenInformation(token, 25, None, 0, ctypes.byref(size))
            if not 1 <= size.value <= 8192:
                return False
            data = ctypes.create_string_buffer(size.value)
            if not self.advapi32.GetTokenInformation(token, 25, data, len(data), ctypes.byref(size)):
                return False
            label = ctypes.cast(data, ctypes.POINTER(TOKEN_MANDATORY_LABEL)).contents
            sid = label.Label.Sid
            count = self.advapi32.GetSidSubAuthorityCount(sid)
            if not count or count[0] < 1:
                return False
            rid = self.advapi32.GetSidSubAuthority(sid, count[0] - 1)
            # Refuse high/system as well as medium-plus/UIAccess tokens.
            return bool(rid) and rid[0] <= 0x2000
        finally:
            self.kernel32.CloseHandle(token)

    def process_identity(self, pid: int) -> tuple[int, str] | None:
        process = self.kernel32.OpenProcess(0x1000, False, pid)
        if not process:
            return None
        try:
            if not self._unprivileged(process):
                return None
            creation, exit_time, kernel, user = FILETIME(), FILETIME(), FILETIME(), FILETIME()
            if not self.kernel32.GetProcessTimes(
                process,
                ctypes.byref(creation),
                ctypes.byref(exit_time),
                ctypes.byref(kernel),
                ctypes.byref(user),
            ):
                return None
            path, length = ctypes.create_unicode_buffer(32768), DWORD(32768)
            if not self.kernel32.QueryFullProcessImageNameW(process, 0, path, ctypes.byref(length)):
                return None
            stamp = (creation.high << 32) | creation.low
            return stamp, path.value
        finally:
            self.kernel32.CloseHandle(process)

    def own_process_unprivileged(self) -> bool:
        return self.process_identity(os.getpid()) is not None

    def inspect_window(self, hwnd: int) -> WindowTarget | None:
        if not self.user32.IsWindow(hwnd) or not self.user32.IsWindowVisible(hwnd):
            return None
        pid = DWORD()
        thread_id = self.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if not thread_id or pid.value == os.getpid():
            return None
        info = self.process_identity(pid.value)
        if info is None:
            return None
        title, class_name = ctypes.create_unicode_buffer(241), ctypes.create_unicode_buffer(256)
        if not self.user32.GetWindowTextW(hwnd, title, len(title)):
            return None
        if not self.user32.GetClassNameW(hwnd, class_name, len(class_name)):
            return None
        return WindowTarget(
            int(hwnd), pid.value, info[0], info[1], _safe_title(title.value), class_name.value, int(thread_id)
        )

    def enumerate_windows(self) -> list[WindowTarget]:
        targets: list[WindowTarget] = []
        visited = 0

        @self._enum_proc
        def callback(hwnd: int, _: int) -> int:
            nonlocal visited
            visited += 1
            if visited > 1024:
                return 0
            try:
                target = self.inspect_window(hwnd)
                if target and _acceptable_target(target, os.getpid()):
                    targets.append(target)
            except (OSError, ValueError, TypeError):
                pass
            return 1

        self.user32.EnumWindows(callback, 0)
        return sorted(targets, key=lambda target: target.label.casefold())[:256]

    def foreground_window(self) -> int:
        return int(self.user32.GetForegroundWindow() or 0)

    def input_desktop_active(self) -> bool:
        # GetAsyncKeyState can return all zeros on an inaccessible desktop.
        # Verify desktop and session availability rather than interpreting zeros
        # during lock/UAC/disconnect as evidence that no modifier is held.
        session = DWORD()
        if not self.kernel32.ProcessIdToSessionId(os.getpid(), ctypes.byref(session)):
            return False
        state, size = ctypes.c_void_p(), DWORD()
        if not self.wtsapi32.WTSQuerySessionInformationW(
            None, session.value, 8, ctypes.byref(state), ctypes.byref(size)
        ):
            return False
        try:
            if not state or size.value < ctypes.sizeof(DWORD):
                return False
            if ctypes.cast(state, ctypes.POINTER(DWORD))[0] != 0:  # WTSActive
                return False
        finally:
            self.wtsapi32.WTSFreeMemory(state)
        desktop = self.user32.OpenInputDesktop(0, False, 0x0001)
        if not desktop:
            return False
        try:
            own = self.user32.GetThreadDesktop(self.kernel32.GetCurrentThreadId())
            names = []
            for handle in (desktop, own):
                if not handle:
                    return False
                name, needed = ctypes.create_unicode_buffer(256), DWORD()
                if (
                    not self.user32.GetUserObjectInformationW(
                        handle, 2, name, ctypes.sizeof(name), ctypes.byref(needed)
                    )
                    or not name.value
                ):
                    return False
                names.append(name.value.casefold())
            return names[0] == names[1]
        finally:
            self.user32.CloseDesktop(desktop)

    def minimized(self, hwnd: int) -> bool:
        return bool(self.user32.IsIconic(hwnd))

    def key_pressed(self, vk: int) -> bool:
        return bool(self.user32.GetAsyncKeyState(vk) & 0x8000)

    def set_marker(self, hwnd: int, name: str, value: int) -> bool:
        return bool(self.user32.SetPropW(hwnd, name, value))

    def get_marker(self, hwnd: int, name: str) -> int:
        return int(self.user32.GetPropW(hwnd, name) or 0)

    def remove_marker(self, hwnd: int, name: str) -> None:
        self.user32.RemovePropW(hwnd, name)

    def send_key(self, key: str, *, up: bool) -> bool:
        # This method is deliberately not a general-purpose input API.
        if key not in SAFE_KEYS:
            return False
        virtual_key = KEY_VK[key]
        scan = self.user32.MapVirtualKeyW(virtual_key, 0)
        if not scan or scan > 0xFF:
            return False
        flags = 0x0008  # KEYEVENTF_SCANCODE: games commonly expect scan codes.
        if key in EXTENDED_KEYS:
            flags |= 0x0001
        if up:
            flags |= 0x0002
        packet = INPUT(type=1)
        packet.ki = KEYBDINPUT(0, scan, flags, 0, 0)
        return self.user32.SendInput(1, ctypes.byref(packet), ctypes.sizeof(INPUT)) == 1

    def register_panic(self) -> bool:
        return bool(self.user32.RegisterHotKey(None, HOTKEY_ID, 0x4000, VK_F8))

    def unregister_panic(self) -> None:
        self.user32.UnregisterHotKey(None, HOTKEY_ID)

    def poll_panic(self) -> bool:
        message = MSG()
        result = False
        # Drain only our hotkey messages. No unrelated messages are dispatched.
        for _ in range(32):
            if not self.user32.PeekMessageW(ctypes.byref(message), None, WM_HOTKEY, WM_HOTKEY, 1):
                break
            result |= message.wParam == HOTKEY_ID
        return result


class WindowsBackend:
    """One locally selected, identity-bound window and one bounded key at a time.

    ``_api`` and ``_clock`` are dependency-injection seams for deterministic
    unit tests. They are not exposed by the UI or by imported profiles.
    """

    def __init__(self, *, _api=None, _clock: Callable[[], float] = time.monotonic) -> None:
        self._lock = threading.RLock()
        self._clock = _clock
        self._api = _api
        self._target: WindowTarget | None = None
        self._marker_name = f"TwitchChatControl.{secrets.token_hex(16)}"
        self._marker_value = secrets.randbits(ctypes.sizeof(HANDLE) * 8 - 1) or 1
        self._held: dict[str, float] = {}
        self._live = False
        self._healthy = True
        self._registered = False
        self._monitor: threading.Thread | None = None
        self._stop = threading.Event()
        self._started = threading.Event()
        self._closed = False
        self._on_fault: Callable[[str], None] = lambda _: None
        self._on_panic: Callable[[], None] = lambda: None
        self._reason = "Windows is required for live game input."
        if self._api is None and sys.platform == "win32":
            try:
                self._api = _Win32API()
            except (OSError, AttributeError):
                self._reason = "Windows safety APIs could not be initialized."
        self._unprivileged = False
        if self._api is not None:
            try:
                self._unprivileged = self._api.own_process_unprivileged()
            except Exception:
                self._unprivileged = False
            self._reason = (
                "Start the safety monitor before enabling live input."
                if self._unprivileged
                else "Live input is disabled while running elevated or when privileges cannot be verified."
            )

    @property
    def available(self) -> bool:
        return self._api is not None

    @property
    def safety_ready(self) -> bool:
        with self._lock:
            return bool(
                self._api
                and self._unprivileged
                and self._healthy
                and self._registered
                and not self._closed
                and self._monitor
                and self._monitor.is_alive()
            )

    @property
    def safety_reason(self) -> str:
        with self._lock:
            return "F8 emergency stop is ready." if self.safety_ready else self._reason

    @property
    def target(self) -> WindowTarget | None:
        with self._lock:
            return self._target

    @property
    def target_selected(self) -> bool:
        with self._lock:
            return self._target is not None

    def enumerate_windows(self) -> list[WindowTarget]:
        if not self.available or not self._unprivileged or self._closed:
            return []
        try:
            return [
                target for target in self._api.enumerate_windows() if _acceptable_target(target, os.getpid())
            ]
        except Exception:
            return []

    def _matches(self, target: WindowTarget) -> bool:
        actual = self._api.inspect_window(target.hwnd)
        return bool(
            actual
            and _acceptable_target(actual, os.getpid())
            and actual.pid == target.pid
            and actual.creation_time == target.creation_time
            and ntpath.normcase(actual.executable) == ntpath.normcase(target.executable)
            and actual.thread_id == target.thread_id
            and actual.class_name == target.class_name
        )

    def select_target(self, target: WindowTarget | None) -> None:
        with self._lock:
            self._live = False
            self._release_locked()
            old = self._target
            self._target = None
            if old and self._api:
                try:
                    if self._api.get_marker(old.hwnd, self._marker_name) == self._marker_value:
                        self._api.remove_marker(old.hwnd, self._marker_name)
                except Exception:
                    pass
            if target is None:
                return
            if (
                not self._api
                or not self._unprivileged
                or self._closed
                or not isinstance(target, WindowTarget)
                or not _acceptable_target(target, os.getpid())
            ):
                raise InputSafetyError("This window cannot be used for live game input.")
            try:
                if not self._matches(target):
                    raise InputSafetyError("The selected game window changed. Refresh the window list.")
                if not self._api.set_marker(target.hwnd, self._marker_name, self._marker_value):
                    raise InputSafetyError("The selected window cannot be safely bound.")
                self._target = target
            except InputSafetyError:
                raise
            except Exception:
                raise InputSafetyError("The selected window could not be verified.") from None

    def is_target_foreground(self) -> bool:
        with self._lock:
            if not self._api or not self._target or self._closed:
                return False
            try:
                return bool(
                    self._api.input_desktop_active()
                    and self._api.foreground_window() == self._target.hwnd
                    and not self._api.minimized(self._target.hwnd)
                    and self._api.get_marker(self._target.hwnd, self._marker_name) == self._marker_value
                    and self._matches(self._target)
                )
            except Exception:
                return False

    def physical_modifiers_down(self) -> bool:
        with self._lock:
            if not self._api or self._closed:
                return True
            try:
                return any(self._api.key_pressed(vk) for vk in MODIFIER_VKS)
            except Exception:
                return True

    def bind_live_session(self, enabled: bool) -> None:
        with self._lock:
            if not enabled:
                self._live = False
                self._release_locked()
                return
            if not self.safety_ready or not self.is_target_foreground() or self.physical_modifiers_down():
                self._live = False
                self._release_locked()
                raise InputSafetyError(
                    "Live input requires a focused, verified game and the F8 safety monitor."
                )
            self._live = True

    def key_down(self, key: str) -> None:
        with self._lock:
            reason = None
            if not isinstance(key, str) or key not in SAFE_KEYS:
                reason = "That key is outside the game-input allowlist."
            elif not self._live or not self.safety_ready:
                reason = "Live input is not armed."
            elif self._held:
                reason = "Only one game key may be held at a time."
            elif not self.is_target_foreground():
                reason = "The selected game lost focus or changed identity."
            elif self.physical_modifiers_down():
                reason = "A physical modifier key is held. Live input was paused."
            if reason:
                self._live = False
                self._release_locked()
                raise InputSafetyError(reason)
            try:
                if self._api.key_pressed(KEY_VK[key]):
                    raise InputSafetyError("The action key is already held by the streamer.")
                # Recheck immediately before SendInput. Windows offers no atomic
                # window-targeted equivalent; the remaining race is documented.
                if not self.is_target_foreground() or self.physical_modifiers_down():
                    raise InputSafetyError("The input safety check changed before the key press.")
                # Track first so even an unexpected API exception causes a key-up.
                self._held[key] = self._clock() + MAX_HOLD_SECONDS
                if not self._api.send_key(key, up=False):
                    self._healthy = False
                    self._reason = "Windows rejected an input event. Restart the app before live use."
                    raise InputSafetyError(self._reason)
            except Exception as exc:
                self._live = False
                self._release_locked()
                if isinstance(exc, InputSafetyError):
                    raise
                self._healthy = False
                self._reason = "A native input error stopped live control. Restart the app."
                raise InputSafetyError(self._reason) from None

    def key_up(self, key: str) -> None:
        with self._lock:
            if key not in self._held:
                return
            try:
                success = self._api.send_key(key, up=True)
            except Exception:
                success = False
            if success:
                self._held.pop(key, None)
            else:
                self._live = False
                self._healthy = False
                self._reason = "Windows could not release an input. Live control is disabled."
                raise InputSafetyError(self._reason)

    def _release_locked(self) -> bool:
        success = True
        for key in tuple(self._held):
            try:
                self.key_up(key)
            except InputSafetyError:
                success = False
        return success

    def release_all(self) -> bool:
        # Key-up events must still be attempted after focus loss. Suppressing
        # these events based on foreground state would leave synthetic keys down.
        with self._lock:
            return self._release_locked()

    def start_safety_monitor(self, on_fault: Callable[[str], None], on_panic: Callable[[], None]) -> None:
        with self._lock:
            if self._closed:
                raise InputSafetyError("The input backend is closed.")
            self._on_fault = on_fault
            self._on_panic = on_panic
            if self._monitor and self._monitor.is_alive():
                return
            if not self._api or not self._unprivileged:
                return
            self._stop.clear()
            self._started.clear()
            self._monitor = threading.Thread(target=self._monitor_loop, name="game-input-safety", daemon=True)
            self._monitor.start()
        # Bounded startup wait; the monitor never relies on the Tk event loop.
        self._started.wait(1.0)

    def _monitor_once(self) -> tuple[bool, str | None]:
        """One independent safety pass; separated for deterministic tests."""
        with self._lock:
            panic = self._api.poll_panic()
            down = self._api.key_pressed(VK_F8)
            panic |= down and not getattr(self, "_panic_was_down", False)
            self._panic_was_down = down
            reason = None
            if panic:
                self._live = False
                self._release_locked()
            elif self._live:
                if not self.is_target_foreground():
                    reason = "The selected game lost focus or changed identity."
                elif self.physical_modifiers_down():
                    reason = "A modifier key was pressed. Live input was paused."
                elif any(self._clock() >= deadline for deadline in self._held.values()):
                    reason = "The independent key-release watchdog paused live input."
                if reason:
                    self._live = False
                    self._release_locked()
            elif self._held:
                self._release_locked()
            return bool(panic), reason

    @staticmethod
    def _notify(callback: Callable, *args) -> None:
        # Release keys and disable native input before notifying another layer;
        # a failed UI callback cannot prevent the safety action.
        try:
            callback(*args)
        except Exception:
            pass

    def _monitor_loop(self) -> None:
        try:
            registered = self._api.register_panic()
            with self._lock:
                self._registered = bool(registered)
                if not registered:
                    self._reason = "F8 is unavailable. Close the app using it, then restart this app."
            self._started.set()
            if not registered:
                self._notify(self._on_fault, self._reason)
                return
            while not self._stop.wait(MONITOR_INTERVAL_SECONDS):
                panic, reason = self._monitor_once()
                if panic:
                    self._notify(self._on_panic)
                elif reason:
                    self._notify(self._on_fault, reason)
        except Exception:
            with self._lock:
                self._healthy = False
                self._reason = "The input safety monitor stopped. Live control is disabled."
                self._live = False
                self._release_locked()
            self._notify(self._on_fault, self._reason)
        finally:
            with self._lock:
                self._live = False
                self._release_locked()
                self._registered = False
            try:
                self._api.unregister_panic()
            except Exception:
                pass
            self._started.set()

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._live = False
            self._release_locked()
            self._stop.set()
            monitor = self._monitor
        if monitor and monitor is not threading.current_thread():
            monitor.join(timeout=1.0)
        with self._lock:
            self.select_target(None)
            self._closed = True
            self._registered = False
