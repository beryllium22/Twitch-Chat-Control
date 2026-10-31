"""Mouse safety and packet tests; all injection APIs are fake, including on Windows."""

import ctypes
from dataclasses import replace

import pytest
from test_windows import FakeAPI, FakeMonitor

from twitch_chat_control.engine import InputBlocked
from twitch_chat_control.models import MOUSE_BUTTONS as MODEL_BUTTONS
from twitch_chat_control.windows import (
    GUITHREADINFO,
    INPUT,
    MAX_HOLD_SECONDS,
    MOUSE_BUTTONS,
    POINT,
    RECT,
    VK_F8,
    InputSafetyError,
    WindowsBackend,
    _Win32API,
)


class FakeMouseAPI(FakeAPI):
    def __init__(self):
        super().__init__()
        self.cursor = (500, 400)
        self.bounds = (0, 0, 1280, 900)
        self.root_at = lambda point: self.target.hwnd
        self.routing_safe = True
        self.after_mouse = lambda: None
        self.acceleration = 1

    def cursor_position(self):
        return self.cursor

    def client_bounds(self, hwnd):
        return self.bounds

    def root_window_at(self, point):
        return self.root_at(point)

    def mouse_routing_safe(self, target):
        return self.routing_safe

    def send_mouse_button(self, button, *, up):
        self.events.append(("button", button, up))
        success = self.send_up_ok if up else self.send_down_ok
        if success:
            if up:
                self.pressed.discard(MOUSE_BUTTONS[button][3])
            else:
                self.pressed.add(MOUSE_BUTTONS[button][3])
        self.after_mouse()
        return success

    def send_mouse_move(self, dx, dy):
        self.events.append(("move", dx, dy))
        self.cursor = (self.cursor[0] + dx * self.acceleration,
                       self.cursor[1] + dy * self.acceleration)
        self.after_mouse()
        return self.send_down_ok

    def send_mouse_wheel(self, steps):
        self.events.append(("wheel", steps))
        self.after_mouse()
        return self.send_down_ok


@pytest.fixture
def mouse_native():
    api = FakeMouseAPI()
    clock = [100.0]
    backend = WindowsBackend(_api=api, _clock=lambda: clock[0])
    backend._registered = True
    backend._monitor = FakeMonitor()
    backend.select_target(api.target)
    backend.bind_live_session(True)
    yield backend, api, clock
    backend.close()


MOUSE_ACTIONS = (
    ("mouse_button_down", ("left",)),
    ("mouse_move", (10, -10)),
    ("mouse_wheel", (-1,)),
)


@pytest.mark.parametrize("button", MODEL_BUTTONS)
def test_all_buttons_press_and_release_with_owned_state(mouse_native, button):
    backend, api, _ = mouse_native
    assert set(MODEL_BUTTONS) == set(MOUSE_BUTTONS)
    backend.mouse_button_down(button)
    assert button in backend._held_buttons
    # The injected button's async state is not mistaken for another physical
    # button. A physical press of the same button cannot be distinguished.
    assert backend._monitor_once() == (False, None)
    backend.mouse_button_up(button)
    backend.mouse_button_up(button)
    assert backend._held_buttons == {}
    assert api.events == [("button", button, False), ("button", button, True)]


@pytest.mark.parametrize("method,args", MOUSE_ACTIONS)
def test_mouse_requires_explicit_live_session(mouse_native, method, args):
    backend, api, _ = mouse_native
    backend.bind_live_session(False)
    with pytest.raises(InputSafetyError, match="not armed"):
        getattr(backend, method)(*args)
    assert api.events == []


@pytest.mark.parametrize("method,args", MOUSE_ACTIONS)
@pytest.mark.parametrize("guard", ["focus", "identity", "desktop", "modifier", "hotkey"])
def test_mouse_uses_existing_live_guards(mouse_native, method, args, guard):
    backend, api, _ = mouse_native
    if guard == "focus":
        api.foreground = 999
    elif guard == "identity":
        api.target = replace(api.target, creation_time=987654)
    elif guard == "desktop":
        api.desktop_active = False
    elif guard == "modifier":
        api.pressed.add(0x11)
    else:
        backend._registered = False
    with pytest.raises(InputSafetyError):
        getattr(backend, method)(*args)
    assert not backend._live
    assert api.events == []


@pytest.mark.parametrize("method,args", MOUSE_ACTIONS)
@pytest.mark.parametrize("cursor", [(0, 400), (500, 0), (1280, 400), (500, 900), (-1, 400)])
def test_cursor_outside_client_or_on_frame_blocks_every_mouse_action(mouse_native, method, args, cursor):
    backend, api, _ = mouse_native
    api.cursor = cursor
    with pytest.raises(InputBlocked, match="mouse_pointer_outside_target"):
        getattr(backend, method)(*args)
    assert api.events == []
    assert not backend._live
    # A routine boundary block allows explicit local re-arming after correction.
    assert backend.safety_ready
    api.cursor = (500, 400)
    backend.bind_live_session(True)


@pytest.mark.parametrize("missing", ["cursor", "bounds"])
def test_unknown_pointer_or_client_bounds_fail_closed(mouse_native, missing):
    backend, api, _ = mouse_native
    setattr(api, missing, None)
    with pytest.raises(InputBlocked, match="mouse_pointer_outside_target"):
        backend.mouse_button_down("left")
    assert api.events == []


@pytest.mark.parametrize("method,args", MOUSE_ACTIONS)
def test_overlay_window_under_pointer_blocks_mouse(mouse_native, method, args):
    backend, api, _ = mouse_native
    api.root_at = lambda point: 999
    with pytest.raises(InputBlocked, match="mouse_pointer_outside_target"):
        getattr(backend, method)(*args)
    assert api.events == []


@pytest.mark.parametrize("method,args", MOUSE_ACTIONS)
def test_native_menu_or_foreign_capture_blocks_mouse(mouse_native, method, args):
    backend, api, _ = mouse_native
    api.routing_safe = False
    with pytest.raises(InputBlocked, match="mouse_capture_unsafe"):
        getattr(backend, method)(*args)
    assert api.events == []


@pytest.mark.parametrize("button", MODEL_BUTTONS)
@pytest.mark.parametrize("method,args", MOUSE_ACTIONS)
def test_streamer_mouse_button_blocks_any_new_mouse_action(mouse_native, button, method, args):
    backend, api, _ = mouse_native
    api.pressed.add(MOUSE_BUTTONS[button][3])
    with pytest.raises(InputBlocked, match="physical_mouse_button_held"):
        getattr(backend, method)(*args)
    assert api.events == []
    assert not backend._held_buttons


@pytest.mark.parametrize("method,args", MOUSE_ACTIONS)
def test_held_key_blocks_mouse_chord_and_releases_key(mouse_native, method, args):
    backend, api, _ = mouse_native
    backend.key_down("w")
    with pytest.raises(InputSafetyError, match="one game input"):
        getattr(backend, method)(*args)
    assert api.events == [("w", False), ("w", True)]


def test_held_mouse_blocks_key_chord_and_releases_button(mouse_native):
    backend, api, _ = mouse_native
    backend.mouse_button_down("left")
    with pytest.raises(InputSafetyError, match="one game key"):
        backend.key_down("w")
    assert api.events == [("button", "left", False), ("button", "left", True)]


@pytest.mark.parametrize("method,args", MOUSE_ACTIONS)
def test_held_mouse_blocks_additional_mouse_input(mouse_native, method, args):
    backend, api, _ = mouse_native
    backend.mouse_button_down("left")
    with pytest.raises(InputSafetyError, match="one game input"):
        getattr(backend, method)(*args)
    assert api.events == [("button", "left", False), ("button", "left", True)]


@pytest.mark.parametrize("dx,dy", [(100, 100), (-100, -100), (0, -100), (100, 0)])
def test_signed_relative_steps_and_fourfold_acceleration(mouse_native, dx, dy):
    backend, api, _ = mouse_native
    api.bounds = (-1000, -1000, 2000, 2000)
    api.cursor = (-100, -100)
    api.acceleration = 4
    backend.mouse_move(dx, dy)
    assert api.events == [("move", dx, dy)]
    assert api.cursor == (-100 + dx * 4, -100 + dy * 4)


@pytest.mark.parametrize("dx,dy", [(101, 0), (0, -101), (0, 0), (True, 0), (1.0, 0), ("1", 0)])
def test_invalid_relative_step_never_reaches_native_sender(mouse_native, dx, dy):
    backend, api, _ = mouse_native
    with pytest.raises(InputSafetyError):
        backend.mouse_move(dx, dy)
    assert api.events == []


@pytest.mark.parametrize("dx,dy", [(100, 0), (-100, 0), (0, 100), (0, -100)])
def test_accelerated_envelope_cannot_cross_client_edge(mouse_native, dx, dy):
    backend, api, _ = mouse_native
    api.bounds = (300, 200, 700, 600)
    with pytest.raises(InputBlocked, match="mouse_move_outside_target"):
        backend.mouse_move(dx, dy)
    assert api.events == []


def test_overlay_at_accelerated_destination_blocks_move(mouse_native):
    backend, api, _ = mouse_native
    api.root_at = lambda point: 999 if point[0] >= 540 else api.target.hwnd
    with pytest.raises(InputBlocked, match="mouse_move_outside_target"):
        backend.mouse_move(10, 0)
    assert api.events == []


def test_pointer_change_after_move_disarms_before_any_later_click(mouse_native):
    backend, api, _ = mouse_native
    api.after_mouse = lambda: setattr(api, "cursor", (1300, 400))
    with pytest.raises(InputBlocked, match="mouse_pointer_outside_target"):
        backend.mouse_move(10, 0)
    with pytest.raises(InputSafetyError, match="not armed"):
        backend.mouse_button_down("left")
    assert api.events == [("move", 10, 0)]
    assert backend.safety_ready


def test_pointer_guard_is_rechecked_immediately_before_button_down(mouse_native):
    backend, api, _ = mouse_native
    positions = iter([(500, 400), (1300, 400)])
    api.cursor_position = lambda: next(positions)
    with pytest.raises(InputBlocked, match="mouse_pointer_outside_target"):
        backend.mouse_button_down("left")
    assert api.events == []


@pytest.mark.parametrize("steps", [-3, -1, 1, 3])
def test_bounded_wheel_notches(mouse_native, steps):
    backend, api, _ = mouse_native
    backend.mouse_wheel(steps)
    assert api.events == [("wheel", steps)]
    assert not backend._held_buttons


@pytest.mark.parametrize("steps", [-4, 0, 4, True, 1.0, "1"])
def test_invalid_wheel_input_never_reaches_native_sender(mouse_native, steps):
    backend, api, _ = mouse_native
    with pytest.raises(InputSafetyError):
        backend.mouse_wheel(steps)
    assert api.events == []


@pytest.mark.parametrize("button", ["Left", "back", "double_click", "", ["left"], None])
def test_invalid_button_never_reaches_native_sender(mouse_native, button):
    backend, api, _ = mouse_native
    with pytest.raises(InputSafetyError):
        backend.mouse_button_down(button)
    assert api.events == []


@pytest.mark.parametrize("cause", ["focus", "pointer", "capture", "modifier", "other_button", "deadline", "panic"])
def test_monitor_releases_mouse_without_engine_or_gui_tick(mouse_native, cause):
    backend, api, clock = mouse_native
    backend.mouse_button_down("left")
    if cause == "focus":
        api.foreground = 999
    elif cause == "pointer":
        api.cursor = (1300, 400)
    elif cause == "capture":
        api.routing_safe = False
    elif cause == "modifier":
        api.pressed.add(0x11)
    elif cause == "other_button":
        api.pressed.add(MOUSE_BUTTONS["right"][3])
    elif cause == "deadline":
        clock[0] += MAX_HOLD_SECONDS
    else:
        api.pressed.add(VK_F8)
    panic, reason = backend._monitor_once()
    assert panic if cause == "panic" else reason
    assert not backend._live
    assert not backend._held_buttons
    assert api.events == [("button", "left", False), ("button", "left", True)]


def test_monitor_keeps_checking_pointer_after_instant_mouse_action(mouse_native):
    backend, api, _ = mouse_native
    backend.mouse_wheel(1)
    api.cursor = (1300, 400)
    _, reason = backend._monitor_once()
    assert reason == "mouse_pointer_outside_target"
    assert not backend._live


def test_new_keyboard_only_session_does_not_inherit_mouse_pointer_gate(mouse_native):
    backend, api, _ = mouse_native
    backend.mouse_wheel(1)
    backend.bind_live_session(False)
    api.cursor = (1300, 400)
    backend.bind_live_session(True)
    backend.key_down("w")
    assert backend._monitor_once() == (False, None)


@pytest.mark.parametrize("action", ["pause", "target", "close"])
def test_local_shutdown_paths_release_mouse(mouse_native, action):
    backend, api, _ = mouse_native
    backend.mouse_button_down("right")
    if action == "pause":
        backend.bind_live_session(False)
    elif action == "target":
        backend.select_target(None)
    else:
        backend.close()
    assert not backend._live
    assert not backend._held_buttons
    assert api.events == [("button", "right", False), ("button", "right", True)]


def test_failed_mouse_release_is_retained_for_retry_and_requires_restart(mouse_native):
    backend, api, _ = mouse_native
    backend.mouse_button_down("x2")
    api.send_up_ok = False
    with pytest.raises(InputSafetyError, match="could not release"):
        backend.mouse_button_up("x2")
    assert "x2" in backend._held_buttons
    assert not backend.safety_ready
    assert backend.release_all() is False
    api.send_up_ok = True
    backend._monitor_once()
    assert not backend._held_buttons
    assert not backend.safety_ready
    with pytest.raises(InputSafetyError):
        backend.bind_live_session(True)


def test_best_effort_release_attempts_mouse_even_if_key_release_fails(mouse_native):
    backend, api, clock = mouse_native
    # The public path forbids overlap, but cleanup must be robust even when
    # multiple outstanding releases are retained after an uncertain failure.
    backend._held["w"] = clock[0]
    backend._held_buttons["left"] = clock[0]
    api.send_up_ok = False
    assert backend.release_all() is False
    assert api.events == [("w", True), ("button", "left", True)]
    assert backend._held and backend._held_buttons
    api.send_up_ok = True
    assert backend.release_all() is True
    assert not backend._held and not backend._held_buttons
    assert not backend.safety_ready


@pytest.mark.parametrize("method,args", MOUSE_ACTIONS)
def test_rejected_native_mouse_event_latches_fault(mouse_native, method, args):
    backend, api, _ = mouse_native
    api.send_down_ok = False
    with pytest.raises(InputSafetyError, match="rejected"):
        getattr(backend, method)(*args)
    assert not backend.safety_ready
    assert not backend._held_buttons
    if method == "mouse_button_down":
        assert api.events[-1] == ("button", "left", True)


def test_uncertain_native_button_exception_attempts_release(mouse_native):
    backend, api, _ = mouse_native
    normal_send = api.send_mouse_button

    def uncertain(button, *, up):
        normal_send(button, up=up)
        if not up:
            raise OSError("simulated native failure")
        return True

    api.send_mouse_button = uncertain
    with pytest.raises(InputSafetyError, match="native input error"):
        backend.mouse_button_down("middle")
    assert api.events == [("button", "middle", False), ("button", "middle", True)]
    assert not backend.safety_ready


class PacketUser32:
    def __init__(self):
        self.events = []
        self.outcomes = []

    def SendInput(self, count, packets, size):
        assert count == 1 and size == ctypes.sizeof(INPUT)
        packet = ctypes.cast(packets, ctypes.POINTER(INPUT)).contents
        assert packet.type == 0
        self.events.append((packet.mi.dx, packet.mi.dy, packet.mi.mouseData, packet.mi.dwFlags))
        result = self.outcomes.pop(0) if self.outcomes else 1
        if isinstance(result, Exception):
            raise result
        return result


def mouse_sender(user32=None):
    api = _Win32API.__new__(_Win32API)
    api.user32 = user32 or PacketUser32()
    api._pressed_mouse_buttons = set()
    return api


@pytest.mark.parametrize("button,flags,data", [
    ("left", (0x0002, 0x0004), 0), ("right", (0x0008, 0x0010), 0),
    ("middle", (0x0020, 0x0040), 0), ("x1", (0x0080, 0x0100), 1),
    ("x2", (0x0080, 0x0100), 2),
])
def test_native_button_packets_have_exact_flags_and_xbutton_data(button, flags, data):
    api = mouse_sender()
    assert api.send_mouse_button(button, up=True)  # no unowned release
    assert api.user32.events == []
    assert api.send_mouse_button(button, up=False)
    assert not api.send_mouse_button(button, up=False)  # no duplicate down
    assert api.send_mouse_button(button, up=True)
    assert api._pressed_mouse_buttons == set()
    assert api.user32.events == [(0, 0, data, flag) for flag in flags]


@pytest.mark.parametrize("outcome", [0, OSError("uncertain press")])
def test_native_uncertain_button_press_retained_until_successful_release(outcome):
    api = mouse_sender()
    api.user32.outcomes = [outcome, 0, 1]
    if isinstance(outcome, Exception):
        with pytest.raises(OSError):
            api.send_mouse_button("x1", up=False)
    else:
        assert not api.send_mouse_button("x1", up=False)
    assert api._pressed_mouse_buttons == {"x1"}
    assert not api.send_mouse_button("x1", up=True)
    assert api._pressed_mouse_buttons == {"x1"}
    assert api.send_mouse_button("x1", up=True)
    assert api._pressed_mouse_buttons == set()
    assert api.user32.events == [(0, 0, 1, 0x0080), (0, 0, 1, 0x0100), (0, 0, 1, 0x0100)]


def test_native_moves_are_relative_and_not_combined_with_button_or_absolute_flags():
    api = mouse_sender()
    assert api.send_mouse_move(-100, 100)
    assert api.user32.events == [(-100, 100, 0, 0x0001)]
    assert not api.send_mouse_move(101, 0)
    assert not api.send_mouse_move(True, 0)
    assert not api.send_mouse_move(0, 0)
    assert len(api.user32.events) == 1


@pytest.mark.parametrize("steps", [-3, -1, 1, 3])
def test_native_wheel_signed_delta_has_dword_bit_pattern(steps):
    api = mouse_sender()
    assert api.send_mouse_wheel(steps)
    assert api.user32.events == [(0, 0, (steps * 120) & 0xFFFFFFFF, 0x0800)]
    assert not api.send_mouse_wheel(4)
    assert not api.send_mouse_wheel(True)
    assert len(api.user32.events) == 1


class GeometryUser32:
    def __init__(self):
        self.cursor_ok = self.rect_ok = self.convert_ok = self.gui_ok = True
        self.offset = (-1280, -200)
        self.window = self.root = 101
        self.flags = 0
        self.active = 101
        self.focus = 102
        self.capture = self.menu = self.move = 0

    def GetCursorPos(self, point):
        value = ctypes.cast(point, ctypes.POINTER(POINT)).contents
        value.x, value.y = -800, 50
        return self.cursor_ok

    def GetClientRect(self, hwnd, rect):
        value = ctypes.cast(rect, ctypes.POINTER(RECT)).contents
        value.left, value.top, value.right, value.bottom = 0, 0, 1280, 900
        return self.rect_ok

    def ClientToScreen(self, hwnd, point):
        value = ctypes.cast(point, ctypes.POINTER(POINT)).contents
        value.x += self.offset[0]
        value.y += self.offset[1]
        return self.convert_ok

    def WindowFromPoint(self, point):
        assert isinstance(point, POINT)
        return self.window

    def GetAncestor(self, hwnd, flag):
        assert flag == 2
        return self.root

    def GetGUIThreadInfo(self, thread_id, pointer):
        assert thread_id == 7
        value = ctypes.cast(pointer, ctypes.POINTER(GUITHREADINFO)).contents
        assert value.cbSize == ctypes.sizeof(GUITHREADINFO)
        value.flags = self.flags
        value.hwndActive, value.hwndFocus, value.hwndCapture = self.active, self.focus, self.capture
        value.hwndMenuOwner, value.hwndMoveSize = self.menu, self.move
        return self.gui_ok


def test_mouse_native_read_helpers_handle_negative_monitors_and_exclusive_bounds():
    api = mouse_sender(GeometryUser32())
    assert api.cursor_position() == (-800, 50)
    assert api.client_bounds(101) == (-1280, -200, 0, 700)
    assert api.root_window_at((-800, 50)) == 101
    api.user32.window = 0
    assert api.root_window_at((-800, 50)) == 0


@pytest.mark.parametrize("failure", ["cursor_ok", "rect_ok", "convert_ok"])
def test_failed_native_geometry_reads_are_not_interpreted_as_valid_zero_coordinates(failure):
    api = mouse_sender(GeometryUser32())
    setattr(api.user32, failure, False)
    if failure == "cursor_ok":
        assert api.cursor_position() is None
    else:
        assert api.client_bounds(101) is None


@pytest.mark.parametrize("field,value", [
    ("gui_ok", False), ("flags", 0x02), ("flags", 0x04), ("flags", 0x08), ("flags", 0x10),
    ("active", 999), ("root", 999), ("menu", 101), ("move", 101),
])
def test_native_gui_routing_rejects_unknown_foreign_menu_and_move_size_state(field, value):
    api = mouse_sender(GeometryUser32())
    target = FakeAPI().target
    assert api.mouse_routing_safe(target)
    setattr(api.user32, field, value)
    assert not api.mouse_routing_safe(target)


def test_native_gui_routing_accepts_game_child_capture_but_not_foreign_capture():
    api = mouse_sender(GeometryUser32())
    target = FakeAPI().target
    api.user32.capture = 103
    assert api.mouse_routing_safe(target)
    api.user32.root = 999
    assert not api.mouse_routing_safe(target)
    assert not api.mouse_routing_safe(replace(target, thread_id=0))
