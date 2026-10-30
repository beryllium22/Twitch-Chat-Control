"""Adversarial native-boundary tests, without injecting any real keyboard input."""

import ctypes
import os
import sys
import threading
from dataclasses import replace

import pytest

from twitch_chat_control.windows import (
    INPUT,
    INPUT_UNION,
    KEY_VK,
    KEYBDINPUT,
    MAX_HOLD_SECONDS,
    MODIFIER_VKS,
    MOUSEINPUT,
    SAFE_KEYS,
    VK_F8,
    InputSafetyError,
    WindowsBackend,
    WindowTarget,
    _safe_title,
    _Win32API,
)


class FakeAPI:
    def __init__(self):
        self.target = WindowTarget(
            101, os.getpid() + 1000, 1234567, r"C:\Games\Example\game.exe", "Example Game", "GameWindow", 7
        )
        self.foreground = self.target.hwnd
        self.markers = {}
        self.pressed = set()
        self.events = []
        self.unprivileged = True
        self.is_minimized = False
        self.desktop_active = True
        self.hotkey_available = True
        self.hotkey_pending = False
        self.send_down_ok = True
        self.send_up_ok = True
        self.marker_ok = True
        self.unregisters = 0

    def own_process_unprivileged(self):
        return self.unprivileged

    def inspect_window(self, hwnd):
        return self.target if self.target and hwnd == self.target.hwnd else None

    def enumerate_windows(self):
        return [self.target] if self.target else []

    def foreground_window(self):
        return self.foreground

    def input_desktop_active(self):
        return self.desktop_active

    def minimized(self, hwnd):
        return self.is_minimized

    def key_pressed(self, vk):
        return vk in self.pressed

    def set_marker(self, hwnd, name, value):
        if self.marker_ok:
            self.markers[hwnd, name] = value
        return self.marker_ok

    def get_marker(self, hwnd, name):
        return self.markers.get((hwnd, name), 0)

    def remove_marker(self, hwnd, name):
        self.markers.pop((hwnd, name), None)

    def send_key(self, key, *, up):
        self.events.append((key, up))
        return self.send_up_ok if up else self.send_down_ok

    def register_panic(self):
        return self.hotkey_available

    def unregister_panic(self):
        self.unregisters += 1

    def poll_panic(self):
        result = self.hotkey_pending
        self.hotkey_pending = False
        return result


class FakeMonitor:
    def is_alive(self):
        return True

    def join(self, timeout=None):
        pass


@pytest.fixture
def native():
    api = FakeAPI()
    clock = [100.0]
    backend = WindowsBackend(_api=api, _clock=lambda: clock[0])
    backend._registered = True
    backend._monitor = FakeMonitor()
    backend.select_target(api.target)
    yield backend, api, clock
    backend.close()


def arm(backend):
    backend.bind_live_session(True)


def test_sendinput_has_full_windows_abi_union():
    expected = 40 if ctypes.sizeof(ctypes.c_void_p) == 8 else 28
    assert ctypes.sizeof(INPUT) == expected
    assert ctypes.sizeof(INPUT_UNION) == ctypes.sizeof(MOUSEINPUT)
    assert ctypes.sizeof(INPUT_UNION) > ctypes.sizeof(KEYBDINPUT)
    assert INPUT.data.offset == (8 if expected == 40 else 4)


def test_safe_key_table_has_no_os_shortcut_keys():
    assert SAFE_KEYS == set("abcdefghijklmnopqrstuvwxyz0123456789") | {"left", "right", "up", "down", "space"}
    assert set(KEY_VK) == SAFE_KEYS
    for key in ("enter", "tab", "escape", "win", "ctrl", "alt", "shift", "f4", "f8", "delete", "`", ";"):
        assert key not in KEY_VK


def test_backend_requires_explicit_session_and_registered_emergency_stop(native):
    backend, api, _ = native
    with pytest.raises(InputSafetyError, match="not armed"):
        backend.key_down("w")
    backend._registered = False
    with pytest.raises(InputSafetyError):
        arm(backend)
    assert api.events == []


def test_elevated_app_cannot_select_target_or_arm():
    api = FakeAPI()
    api.unprivileged = False
    backend = WindowsBackend(_api=api)
    assert not backend.safety_ready
    assert backend.enumerate_windows() == []
    with pytest.raises(InputSafetyError):
        backend.select_target(api.target)
    with pytest.raises(InputSafetyError):
        arm(backend)
    backend.close()


@pytest.mark.parametrize(
    "executable", ["cmd.exe", "powershell.exe", "explorer.exe", "chrome.exe", "msedge.exe", "code.exe"]
)
def test_sensitive_applications_excluded_and_cannot_be_selected(executable):
    api = FakeAPI()
    api.target = replace(api.target, executable=rf"C:\Apps\{executable}")
    backend = WindowsBackend(_api=api)
    assert backend.enumerate_windows() == []
    with pytest.raises(InputSafetyError):
        backend.select_target(api.target)
    backend.close()


def test_own_process_is_excluded():
    api = FakeAPI()
    api.target = replace(api.target, pid=os.getpid())
    backend = WindowsBackend(_api=api)
    with pytest.raises(InputSafetyError):
        backend.select_target(api.target)
    backend.close()


@pytest.mark.parametrize(
    "changed",
    [
        {"pid": 998877},
        {"creation_time": 345},
        {"executable": r"C:\Other\game.exe"},
        {"class_name": "AnotherWindow"},
        {"thread_id": 88},
    ],
)
def test_changed_process_identity_blocks_input(native, changed):
    backend, api, _ = native
    arm(backend)
    api.target = replace(api.target, **changed)
    with pytest.raises(InputSafetyError):
        backend.key_down("w")
    assert api.events == []
    assert not backend._live


def test_destroyed_then_reused_window_handle_loses_marker(native):
    backend, api, _ = native
    arm(backend)
    api.markers.clear()  # Windows removes properties when a window is destroyed.
    with pytest.raises(InputSafetyError):
        backend.key_down("w")
    assert api.events == []


def test_game_title_can_change_without_rebinding(native):
    backend, api, _ = native
    api.target = replace(api.target, title="Example Game — Level Two")
    assert backend.is_target_foreground()


def test_marker_failure_refuses_binding():
    api = FakeAPI()
    api.marker_ok = False
    backend = WindowsBackend(_api=api)
    with pytest.raises(InputSafetyError, match="safely bound"):
        backend.select_target(api.target)
    assert not backend.target_selected
    backend.close()


@pytest.mark.parametrize("vk", MODIFIER_VKS)
def test_every_modifier_stops_live_input(native, vk):
    backend, api, _ = native
    arm(backend)
    api.pressed.add(vk)
    with pytest.raises(InputSafetyError, match="modifier"):
        backend.key_down("w")
    assert not backend._live
    assert api.events == []


def test_streamer_held_key_is_never_pressed_or_released(native):
    backend, api, _ = native
    arm(backend)
    api.pressed.add(KEY_VK["w"])
    with pytest.raises(InputSafetyError, match="already held"):
        backend.key_down("w")
    assert api.events == []


def test_focus_loss_releases_existing_key_and_latches_session_off(native):
    backend, api, _ = native
    arm(backend)
    backend.key_down("w")
    api.foreground = 999
    panic, reason = backend._monitor_once()
    assert not panic
    assert "lost focus" in reason
    assert api.events == [("w", False), ("w", True)]
    api.foreground = 101
    with pytest.raises(InputSafetyError, match="not armed"):
        backend.key_down("w")


def test_minimized_target_is_not_eligible(native):
    backend, api, _ = native
    api.is_minimized = True
    assert not backend.is_target_foreground()
    with pytest.raises(InputSafetyError):
        arm(backend)


def test_watchdog_releases_key_without_engine_or_ui_tick(native):
    backend, api, clock = native
    arm(backend)
    backend.key_down("space")
    clock[0] += MAX_HOLD_SECONDS
    _, reason = backend._monitor_once()
    assert "watchdog" in reason
    assert api.events == [("space", False), ("space", True)]
    assert not backend._live


def test_native_refuses_overlapping_keys_and_releases_first(native):
    backend, api, _ = native
    arm(backend)
    backend.key_down("w")
    with pytest.raises(InputSafetyError, match="one game key"):
        backend.key_down("a")
    assert api.events == [("w", False), ("w", True)]


def test_repeated_key_up_never_releases_an_unowned_key(native):
    backend, api, _ = native
    backend.key_up("w")
    backend.key_up("ctrl")
    assert api.events == []


def test_sendinput_failure_requires_restart_and_attempts_key_release(native):
    backend, api, _ = native
    arm(backend)
    api.send_down_ok = False
    with pytest.raises(InputSafetyError, match="rejected"):
        backend.key_down("w")
    assert api.events == [("w", False), ("w", True)]
    assert not backend.safety_ready
    with pytest.raises(InputSafetyError):
        arm(backend)


def test_failed_release_retained_for_retry_and_never_rearms(native):
    backend, api, _ = native
    arm(backend)
    backend.key_down("w")
    api.send_up_ok = False
    with pytest.raises(InputSafetyError, match="could not release"):
        backend.key_up("w")
    assert "w" in backend._held
    assert not backend.safety_ready
    api.send_up_ok = True
    backend._monitor_once()
    assert not backend._held
    with pytest.raises(InputSafetyError):
        arm(backend)


def test_switching_target_releases_and_disarms(native):
    backend, api, _ = native
    arm(backend)
    backend.key_down("d")
    backend.select_target(None)
    assert not backend.target_selected
    assert not backend._live
    assert api.events == [("d", False), ("d", True)]
    assert api.markers == {}


def test_f8_also_detected_while_modifier_is_held(native):
    backend, api, _ = native
    arm(backend)
    backend.key_down("w")
    api.pressed.update({VK_F8, 0x11})
    panic, reason = backend._monitor_once()
    assert panic
    assert reason is None
    assert api.events[-1] == ("w", True)
    assert not backend._live


def test_real_monitor_failure_disables_live_control():
    api = FakeAPI()
    api.hotkey_available = False
    backend = WindowsBackend(_api=api)
    notified = threading.Event()
    backend.start_safety_monitor(lambda _: notified.set(), lambda: None)
    assert notified.wait(0.5)
    assert not backend.safety_ready
    with pytest.raises(InputSafetyError):
        arm(backend)
    backend.close()


def test_real_monitor_releases_before_invoking_panic_callback():
    api = FakeAPI()
    backend = WindowsBackend(_api=api)
    notified = threading.Event()
    observations = []

    def panic_callback():
        observations.append((backend._live, dict(backend._held), list(api.events)))
        notified.set()

    backend.start_safety_monitor(lambda _: None, panic_callback)
    try:
        backend.select_target(api.target)
        arm(backend)
        backend.key_down("w")
        api.hotkey_pending = True
        assert notified.wait(0.5)
        assert observations == [(False, {}, [("w", False), ("w", True)])]
    finally:
        backend.close()
    assert api.unregisters == 1
    assert not backend.safety_ready


def test_window_title_controls_removed():
    assert _safe_title("Game\n\x1b\u202e.exe\u2066") == "Game.exe"


def test_locked_or_disconnected_desktop_stops_live_input(native):
    backend, api, _ = native
    arm(backend)
    backend.key_down("w")
    api.desktop_active = False
    _, reason = backend._monitor_once()
    assert reason
    assert not backend._live
    assert api.events == [("w", False), ("w", True)]


def test_invalid_key_type_fails_closed(native):
    backend, api, _ = native
    arm(backend)
    with pytest.raises(InputSafetyError):
        backend.key_down(["w"])
    assert not backend._live
    assert api.events == []


@pytest.mark.skipif(sys.platform != "win32", reason="Read-only Win32 ABI check requires Windows")
def test_native_win32_declarations_with_read_only_calls():
    # Windows CI exercises real ctypes signatures without emitting any input.
    # Hosted runners may be elevated/headless, so both safe false outcomes are
    # valid; only successful API invocation and bounded result types are tested.
    api = _Win32API()
    assert isinstance(api.own_process_unprivileged(), bool)
    assert isinstance(api.foreground_window(), int)
    assert isinstance(api.input_desktop_active(), bool)
    assert isinstance(api.enumerate_windows(), list)
