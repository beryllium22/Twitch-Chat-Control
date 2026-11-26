"""Mouse bindings through the real GUI, with fake Twitch and input boundaries."""

import json
from dataclasses import replace

import pytest
import test_app_flow

from twitch_chat_control import app as app_module
from twitch_chat_control.control_editor import ACTION_LABELS, BUTTON_LABELS
from twitch_chat_control.models import Command, Profile
from twitch_chat_control.presets import PreviewBackend
from twitch_chat_control.profile_io import load_profile

gui = test_app_flow.gui

MOUSE_COMMANDS = (
    Command("!fire", action_type="mouse_button", button="x2", hold_ms=180, cooldown_ms=700),
    Command("!look", action_type="mouse_move", dx=-25, dy=10, cooldown_ms=800),
    Command("!next", action_type="mouse_wheel", wheel=-2, cooldown_ms=900),
)
INPUT_METHODS = ("key_down", "key_up", "mouse_button_down", "mouse_button_up", "mouse_move", "mouse_wheel")


def edit_new_mouse_command(app, command):
    editor = app.command_editor
    editor.new_command()
    editor.trigger_var.set(command.trigger)
    editor.action_var.set(ACTION_LABELS[command.action_type])
    editor.cooldown_var.set(str(command.cooldown_ms))
    if command.action_type == "mouse_button":
        editor.button_var.set(BUTTON_LABELS[command.button])
        editor.hold_var.set(str(command.hold_ms))
    elif command.action_type == "mouse_move":
        editor.dx_var.set(str(command.dx))
        editor.dy_var.set(str(command.dy))
    else:
        editor.wheel_direction_var.set("Down" if command.wheel < 0 else "Up")
        editor.wheel_steps_var.set(str(abs(command.wheel)))
    assert editor.dirty


def arm_mouse_profile(gui, profile):
    app = gui.app
    app.engine.set_profile(profile)
    app._show_profile(profile)
    client = test_app_flow.connect(gui)
    client.on_status("connected", "Synthetic connection ready.")
    app._refresh_windows()
    app.window_combo.current(0)
    app._select_window()
    app.mode_var.set("live")
    app._mode_changed()
    app.ack_var.set(True)
    app._arm()
    assert app.engine.snapshot().state == "pending"
    test_app_flow.advance(gui, 3.05)
    assert app.engine.snapshot().state == "armed"
    assert not app.engine.snapshot().preview
    return client


def add_fake_mouse_input(gui, monkeypatch):
    """Extend the existing GUI fixture; never construct a native backend."""
    backend = gui.backend

    def dispatch(*event):
        assert backend.live and backend.target_selected
        assert not backend.pressed, "Mouse and keyboard actions must not overlap"
        backend.events.append(event)

    def down(button):
        dispatch("mouse_down", button)
        backend.pressed.add("mouse:" + button)

    def up(button):
        backend.pressed.discard("mouse:" + button)
        backend.events.append(("mouse_up", button))

    monkeypatch.setattr(backend, "mouse_button_down", down, raising=False)
    monkeypatch.setattr(backend, "mouse_button_up", up, raising=False)
    monkeypatch.setattr(backend, "mouse_move", lambda dx, dy: dispatch("move", dx, dy), raising=False)
    monkeypatch.setattr(backend, "mouse_wheel", lambda steps: dispatch("wheel", steps), raising=False)


@pytest.mark.parametrize("command", MOUSE_COMMANDS, ids=lambda command: command.action_type)
def test_mouse_editor_applies_saves_and_reopens_with_keyboard_profile(gui, tmp_path, monkeypatch, command):
    app = gui.app
    old_keys = app._profile.commands
    app.client_id_var.set("synthetic-session-only-client-id")
    app.channel_var.set("synthetic_session_channel")
    edit_new_mouse_command(app, command)
    assert app.command_editor.apply_pending()
    assert app._profile.commands == old_keys + (command,)
    assert app.engine.profile == app._profile
    assert app._profile.schema_version == 2
    assert app.engine.snapshot().state == "disarmed"

    path = tmp_path / "mixed-controls.json"
    monkeypatch.setattr(app_module.filedialog, "asksaveasfilename", lambda **kwargs: str(path))
    assert app._save_profile()
    saved = app._profile
    assert load_profile(path) == saved
    raw = path.read_text(encoding="utf-8")
    assert json.loads(raw)["commands"][-1] == command.to_dict()
    assert "synthetic-session-only-client-id" not in raw
    assert "synthetic_session_channel" not in raw

    # Make an actual unsaved edit, then reopen the saved file and discard it.
    app.command_editor.enabled_var.set(False)
    assert app.command_editor.apply_pending()
    assert app._profile != saved
    monkeypatch.setattr(app_module.filedialog, "askopenfilename", lambda **kwargs: str(path))
    monkeypatch.setattr(app_module.messagebox, "askyesnocancel", lambda *args, **kwargs: False)
    app._import_profile()
    assert app._profile == saved == app.engine.profile
    assert app.command_editor.commands == saved.commands
    assert app._saved_profile == saved
    assert app._profile_path == path
    assert not app._has_unsaved_changes()
    assert app.engine.snapshot().state == "disarmed"


@pytest.mark.parametrize("command", MOUSE_COMMANDS, ids=lambda command: command.action_type)
def test_mouse_preview_describes_action_without_native_input_or_connected_chat_execution(gui, monkeypatch, command):
    app = gui.app
    client = arm_mouse_profile(gui, Profile(commands=(command,)))
    calls = []

    def forbidden(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError("Preview reached an input method")

    for method in INPUT_METHODS:
        monkeypatch.setattr(gui.backend, method, forbidden, raising=False)
        monkeypatch.setattr(PreviewBackend, method, forbidden)
    accepted_before = app.engine.snapshot().accepted
    app.command_editor.preview_command()
    result = app.command_editor.preview_var.get()
    assert command.action_summary in result
    assert "no keyboard or mouse input sent" in result
    assert "Allowed as moderator" in result
    assert app.mode_var.get() == "preview"
    assert app.engine.snapshot().state == "disarmed"
    assert client.running
    client.on_chat(replace(test_app_flow.chat(gui), text=command.trigger))
    app.engine.tick()
    assert app.engine.snapshot().accepted == accepted_before

    app._arm()
    app.sim_var.set(command.trigger)
    app._simulate()
    app.engine.tick()
    app._pump()
    assert app.engine.snapshot().accepted == accepted_before + 1
    assert "No keyboard or mouse input was sent." in app.log.get("1.0", "end")
    assert not calls
    assert not gui.backend.pressed


@pytest.mark.parametrize("command,field,bad_value", [
    (MOUSE_COMMANDS[0], "button_var", "Double click"),
    (MOUSE_COMMANDS[1], "dx_var", "101"),
    (MOUSE_COMMANDS[2], "wheel_steps_var", "0"),
])
def test_invalid_mouse_draft_save_preserves_existing_file_and_applied_profile(
    gui, tmp_path, monkeypatch, command, field, bad_value,
):
    app = gui.app
    edit_new_mouse_command(app, command)
    assert app.command_editor.apply_pending()
    path = tmp_path / "controls.json"
    monkeypatch.setattr(app_module.filedialog, "asksaveasfilename", lambda **kwargs: str(path))
    assert app._save_profile()
    saved = app._profile
    before = path.read_bytes()
    getattr(app.command_editor, field).set(bad_value)

    assert not app._save_profile()

    assert path.read_bytes() == before
    assert app._profile == saved == app.engine.profile
    assert app._saved_profile == saved and app._profile_path == path
    assert getattr(app.command_editor, field).get() == bad_value
    assert app.command_editor.dirty
    assert app.command_editor.error_var.get()
    assert app.notebook.index(app.notebook.select()) == 1
    assert app.engine.snapshot().state == "disarmed"


@pytest.mark.parametrize("stop", ["command_edit", "safety_edit", "pause", "panic", "disconnect", "close"])
def test_live_twitch_mouse_hold_and_queue_stop_through_gui_lifecycle(gui, monkeypatch, stop):
    app = gui.app
    add_fake_mouse_input(gui, monkeypatch)
    press = Command("!fire", action_type="mouse_button", button="left", hold_ms=450, cooldown_ms=0)
    move = Command("!look", action_type="mouse_move", dx=10, cooldown_ms=0)
    profile = Profile(commands=(press, move), global_cooldown_ms=100, queue_ttl_ms=750)
    client = arm_mouse_profile(gui, profile)
    client.on_chat(replace(test_app_flow.chat(gui), text=press.trigger))
    app.engine.tick()
    assert gui.backend.pressed == {"mouse:left"}
    assert app.engine.snapshot().active_action == press.action_label
    test_app_flow.advance(gui, 0.11)
    client.on_chat(replace(test_app_flow.chat(gui, "queued-move", "456"), text=move.trigger))
    assert app.engine.snapshot().queue_depth == 1

    if stop == "command_edit":
        app.command_editor.button_var.set(BUTTON_LABELS["right"])
        assert app.command_editor.dirty
        assert app._profile.commands[0] == press  # unapplied edit is preserved
    elif stop == "safety_edit":
        app.limit_vars["global_cooldown_ms"].set("500")
        assert app._dirty
    elif stop == "pause":
        app._pause()
    elif stop == "panic":
        app._panic()
    elif stop == "disconnect":
        app._disconnect()
        assert client.stop_calls == 1
    else:
        app.close(force=True)
        assert gui.backend.closed

    snapshot = app.engine.snapshot()
    assert snapshot.state == "disarmed" and snapshot.queue_depth == 0
    assert snapshot.active_action is None
    assert not gui.backend.pressed and not gui.backend.live
    assert not any(event[0] == "move" for event in gui.backend.events)
    accepted = snapshot.accepted
    client.on_chat(replace(test_app_flow.chat(gui, "after-stop", "789"), text=move.trigger))
    app.engine.tick()
    assert app.engine.snapshot().accepted == accepted


@pytest.mark.parametrize("command,event", [
    (MOUSE_COMMANDS[0], ("mouse_down", "x2")),
    (MOUSE_COMMANDS[1], ("move", -25, 10)),
    (MOUSE_COMMANDS[2], ("wheel", -2)),
])
def test_accepted_fake_twitch_mouse_command_reaches_only_its_configured_action(gui, monkeypatch, command, event):
    add_fake_mouse_input(gui, monkeypatch)
    client = arm_mouse_profile(gui, Profile(commands=(command,)))
    client.on_chat(replace(test_app_flow.chat(gui), text=command.trigger))
    gui.app.engine.tick()
    assert gui.app.engine.snapshot().accepted == 1
    assert gui.backend.events.count(event) == 1
    test_app_flow.advance(gui, 0.2)
    assert not gui.backend.pressed
    if command.is_held:
        assert ("mouse_up", command.button) in gui.backend.events
    assert gui.app.engine.snapshot().state == "armed"
    gui.app._pause()
    assert not gui.backend.live


def test_enlarged_minimum_window_keeps_pause_start_guidance_and_mouse_fields_reachable():
    # Scaling must be configured before App creates its fonts. This reproduces
    # the small-window layout where packing the page used to hide the footer.
    try:
        root = test_app_flow.tk.Tk()
    except test_app_flow.tk.TclError:
        pytest.skip("Desktop integration requires an available Tk display")
    original_scaling = root.tk.call("tk", "scaling")
    app = None
    try:
        root.tk.call("tk", "scaling", 2.0)
        app = app_module.App(root, backend=test_app_flow.FakeBackend(), start_workers=False)
        root.geometry("940x640")
        profile = Profile(commands=(Command("!look", action_type="mouse_move", dx=-100, dy=100),))
        app.engine.set_profile(profile)
        app._show_profile(profile)
        app.notebook.select(1)
        root.update()
        root.focus_force()
        root.update()

        def inside(widget, container):
            assert widget.winfo_ismapped()
            assert widget.winfo_rootx() >= container.winfo_rootx()
            assert widget.winfo_rooty() >= container.winfo_rooty()
            assert widget.winfo_rootx() + widget.winfo_width() <= container.winfo_rootx() + container.winfo_width()
            assert widget.winfo_rooty() + widget.winfo_height() <= container.winfo_rooty() + container.winfo_height()

        def footer_visible():
            page_bottom = app.notebook.winfo_rooty() + app.notebook.winfo_height()
            for widget in (app.arm_button, app.pause_button, app.readiness_heading, app.readiness_label):
                inside(widget, root)
                assert widget.winfo_rooty() >= page_bottom

        assert root.winfo_width() == 940 and root.winfo_height() == 640
        footer_visible()
        editor = app.command_editor
        assert editor._layout_mode == "stacked"
        # Each control must be reachable by focus, while the session controls
        # remain visible outside the shrinking scrollable page viewport.
        editor.dx_var.set("-25")
        for widget in (editor.dx_entry, editor.dy_entry, editor.apply_button):
            app._focus_widget(widget)
            app._refresh_session_ui()
            app.notice_var.set(
                "Review this mouse command before starting. Movement uses relative input units; "
                "choose a small value and keep the pointer inside the game during live control."
            )
            root.update()
            inside(widget, app._pages[1].canvas)
            footer_visible()
    finally:
        for callback in root.tk.call("after", "info"):
            root.after_cancel(callback)
        root.tk.call("tk", "scaling", original_scaling)
        if app is not None and not app._closed:
            app.close(force=True)
        else:
            root.destroy()
