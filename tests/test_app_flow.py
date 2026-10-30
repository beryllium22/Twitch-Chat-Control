"""Desktop integration with real Tk widgets and fake network/input boundaries.

These run on a Windows desktop runner or under Xvfb. Headless environments with
no Tk display skip them; no test opens a network connection or injects OS input.
"""

from dataclasses import dataclass
from types import SimpleNamespace

import pytest

tk = pytest.importorskip("tkinter", reason="Desktop integration requires tkinter")

from twitch_chat_control import app as app_module  # noqa: E402
from twitch_chat_control.models import Command, Profile  # noqa: E402
from twitch_chat_control.twitch import ACTIVATE_URL, ChatMessage  # noqa: E402


class Clock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now


@dataclass(frozen=True)
class FakeWindow:
    label: str = "Synthetic game window"
    title: str = "Synthetic game window"


class FakeBackend:
    available = True
    safety_ready = True

    def __init__(self):
        self.target = None
        self.live = False
        self.pressed = set()
        self.events = []
        self.closed = False

    @property
    def target_selected(self):
        return self.target is not None

    def enumerate_windows(self):
        return [FakeWindow()]

    def select_target(self, target):
        self.target = target

    def is_target_foreground(self):
        return self.target_selected

    def physical_modifiers_down(self):
        return False

    def bind_live_session(self, enabled):
        self.live = enabled

    def key_down(self, key):
        assert self.live and self.target_selected
        assert not self.pressed
        self.pressed.add(key)
        self.events.append(("down", key))

    def key_up(self, key):
        self.pressed.discard(key)
        self.events.append(("up", key))

    def release_all(self):
        self.pressed.clear()
        self.events.append(("release_all",))
        return True

    def close(self):
        self.release_all()
        self.closed = True


class FakeTwitch:
    def __init__(self, *, client_id, channel, on_chat, on_status, on_device):
        self.client_id = client_id
        self.channel = channel
        self.on_chat = on_chat
        self.on_status = on_status
        self.on_device = on_device
        self.running = False
        self.stop_calls = 0

    def start(self):
        self.running = True

    def stop(self):
        self.running = False
        self.stop_calls += 1


@pytest.fixture
def gui(monkeypatch):
    try:
        root = tk.Tk()
    except tk.TclError:
        pytest.skip("Desktop integration requires an available Tk display (use Xvfb on Linux)")
    root.withdraw()
    clock = Clock()
    monkeypatch.setattr(app_module, "time", SimpleNamespace(monotonic=clock))
    messages = []
    for method in ("showerror", "showinfo", "showwarning"):
        monkeypatch.setattr(app_module.messagebox, method,
                            lambda *args, **kwargs: messages.append(args))
    backend = FakeBackend()
    clients = []

    def factory(**kwargs):
        client = FakeTwitch(**kwargs)
        clients.append(client)
        return client

    app = None
    try:
        app = app_module.App(root, backend=backend, twitch_factory=factory, start_workers=False)
        app.engine._clock = clock
        app.engine._last_tick = clock()
        root.update_idletasks()
        yield SimpleNamespace(app=app, backend=backend, clock=clock, clients=clients, messages=messages)
    finally:
        # No event-loop sleeps: cancel scheduled pump callbacks before destroying
        # their Tcl commands so subsequent Tk test instances remain isolated.
        try:
            for callback in root.tk.call("after", "info"):
                root.after_cancel(callback)
        except tk.TclError:
            pass
        if app is not None and not app._closed:
            app.close(force=True)
        elif app is None:
            root.destroy()


def connect(gui):
    gui.app.client_id_var.set("synthetic-public-client-id")
    gui.app.channel_var.set("synthetic_channel")
    gui.app._connect()
    return gui.clients[-1]


def chat(gui, identifier="synthetic-message-1", user="123"):
    return ChatMessage(message_id=identifier, user_id=user, login="synthetic_viewer",
                       text="!forward", roles=frozenset({"moderator"}), channel_id="999",
                       received_at=gui.clock())


def advance(gui, seconds):
    end = gui.clock.now + seconds
    while gui.clock.now < end:
        gui.clock.now = min(end, gui.clock.now + 0.05)
        gui.app.engine.tick()


def arm_live(gui):
    app = gui.app
    profile = Profile(commands=(Command("!forward", "w", 450, 0),), global_cooldown_ms=100)
    app.engine.set_profile(profile)
    app._show_profile(profile)
    client = connect(gui)
    client.on_status("connected", "Synthetic connection ready.")
    app._refresh_windows()
    app.window_combo.current(0)
    app._select_window()
    app.mode_var.set("live")
    app._mode_changed()
    app.ack_var.set(True)
    app._arm()
    assert app.engine.snapshot().state == "pending"
    advance(gui, 3.05)
    assert app.engine.snapshot().state == "armed"
    assert not app.engine.snapshot().preview
    return client


def active_and_queued(gui):
    client = arm_live(gui)
    client.on_chat(chat(gui))
    gui.app.engine.tick()
    assert gui.backend.pressed == {"w"}
    advance(gui, 0.11)
    client.on_chat(chat(gui, "synthetic-message-2", "456"))
    assert gui.app.engine.snapshot().queue_depth == 1
    return client


def test_desktop_starts_disarmed_preview_and_connect_does_not_arm(gui):
    app = gui.app
    assert app.engine.snapshot().state == "disarmed"
    assert app.engine.snapshot().preview
    assert app.mode_var.get() == "preview"
    assert app.audience_var.get() == "moderator"
    assert not app.ack_var.get()
    assert not gui.backend.target_selected
    client = connect(gui)
    client.on_status("connected", "Synthetic connection ready.")
    assert app.engine.snapshot().connected
    assert app.engine.snapshot().state == "disarmed"
    assert not gui.backend.pressed


def test_preview_simulator_runs_through_policy_without_native_keys(gui):
    app = gui.app
    app._arm()
    app.sim_var.set("!jump")
    app.sim_role.set("moderator")
    app._simulate()
    assert app.engine.snapshot().accepted == 1
    app.engine.tick()
    app._pump()
    assert "would_tap" in app.log.get("1.0", "end")
    assert not any(event[0] in {"down", "up"} for event in gui.backend.events)
    assert not gui.clients


@pytest.mark.parametrize("change", ["mode", "profile", "disconnect"])
def test_local_mode_profile_and_disconnect_changes_release_and_clear(gui, change):
    client = active_and_queued(gui)
    if change == "mode":
        gui.app.mode_var.set("preview")
        gui.app._mode_changed()
    elif change == "profile":
        # A real Tk StringVar trace disarms immediately, before Apply is pressed.
        gui.app.profile_name.set("Edited profile")
        assert gui.app._dirty
    else:
        gui.app._disconnect()
        assert client.stop_calls == 1
    snapshot = gui.app.engine.snapshot()
    assert snapshot.state == "disarmed"
    assert snapshot.queue_depth == 0
    assert not gui.backend.pressed
    assert not gui.backend.live


def test_old_connection_callbacks_cannot_touch_a_new_session(gui):
    app = gui.app
    old = connect(gui)
    old.on_status("connected", "Old synthetic connection ready.")
    app._disconnect()
    current = connect(gui)
    assert not app.engine.snapshot().connected
    old.on_status("connected", "Stale connection callback.")
    assert not app.engine.snapshot().connected
    current.on_status("connected", "Current synthetic connection ready.")
    app._arm()
    assert app.engine.snapshot().state == "armed"
    old.on_chat(chat(gui, "stale-chat"))
    old.on_status("disconnected", "Stale disconnect callback.")
    queue_before_device = app._events.qsize()
    old.on_device(ACTIVATE_URL, "STALE-CODE")
    assert app._events.qsize() == queue_before_device
    assert app.engine.snapshot().accepted == 0
    assert app.engine.snapshot().connected
    assert app.engine.snapshot().state == "armed"
    current.on_chat(chat(gui, "current-chat"))
    assert app.engine.snapshot().accepted == 1
    app._pump()
    assert app.connection_var.get() == "Twitch: connected"
    assert app._device_dialog is None


def test_simulator_cannot_submit_during_live_input_even_if_ui_mode_is_changed(gui):
    arm_live(gui)
    app = gui.app
    app.sim_var.set("!forward")
    app._simulate()
    assert app.engine.snapshot().accepted == 0
    assert app.engine.snapshot().queue_depth == 0
    # Verify the second guard against stale/inconsistent UI mode as well.
    app.mode_var.set("preview")
    app._simulate()
    assert app.engine.snapshot().accepted == 0
    assert not gui.backend.pressed


def test_authorization_code_is_cleared_on_close_and_never_logged(gui):
    app = gui.app
    client = connect(gui)
    code = "SYNTHETIC-DEVICE-CODE"
    client.on_device(ACTIVATE_URL, code)
    app._pump()
    assert app._device_dialog is not None
    code_variable = app._device_value
    assert code_variable.get() == code
    assert code not in app.log.get("1.0", "end")
    app.close()
    assert code_variable.get() == ""
    assert app._device_value is None
    assert app._device_dialog is None
    assert app.client_id_var.get() == ""
    assert client.stop_calls == 1
    assert gui.backend.closed
    assert app.engine.snapshot().state == "disarmed"
    queue_after_close = app._events.qsize()
    client.on_device(ACTIVATE_URL, "LATE-CODE")
    client.on_status("connected", "Late connection callback.")
    assert app._events.qsize() == queue_after_close
    assert not app.engine.snapshot().connected


def test_full_ui_queue_cannot_delay_synchronous_disconnect_safety(gui):
    client = active_and_queued(gui)
    for _ in range(1_000):
        gui.app._post("notice", "Synthetic background event.")
    assert gui.app._events.qsize() == 256
    assert gui.backend.pressed == {"w"}
    client.on_status("disconnected", "Synthetic connection lost.")
    # Intentionally do not pump Tk: safety must not wait for UI status delivery.
    snapshot = gui.app.engine.snapshot()
    assert not snapshot.connected
    assert snapshot.state == "disarmed"
    assert snapshot.queue_depth == 0
    assert not gui.backend.pressed
    assert not gui.backend.live
    assert gui.app._events.qsize() <= 256
