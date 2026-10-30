"""Friendly setup guidance must not become a path around local safety checks."""

from dataclasses import replace

import pytest
import test_app_flow

from twitch_chat_control import app as app_module
from twitch_chat_control.models import Profile

gui = test_app_flow.gui


def test_initial_preview_guidance_needs_no_connection_and_does_not_start_input(gui):
    app = gui.app
    gui.backend.available = False
    gui.backend.safety_ready = False

    result = app._refresh_session_ui()

    assert result.ready
    assert "Preview" in result.detail
    assert "No Twitch account or game" in result.detail
    assert app.arm_button.instate(["!disabled"])
    assert app.sim_button.instate(["disabled"])
    assert app.engine.snapshot().state == "disarmed"
    assert not gui.clients
    assert not any(event[0] in {"down", "up"} for event in gui.backend.events)


def test_live_guidance_walks_through_connection_game_and_acknowledgment(gui):
    app = gui.app
    app.mode_var.set("live")
    app._mode_changed()
    assert app._refresh_session_ui().action == "connect"
    assert app.arm_button.instate(["disabled"])

    client = test_app_flow.connect(gui)
    client.on_status("connected", "Synthetic connection ready.")
    assert app._refresh_session_ui().action == "game"

    app._refresh_windows()
    app.window_combo.current(0)
    app._select_window()
    assert app._refresh_session_ui().action == "ack"
    assert not app.ack_var.get()

    app.ack_var.set(True)
    app._ack_changed()
    result = app._refresh_session_ui()
    assert result.ready
    assert app.arm_button.instate(["!disabled"])
    assert app.engine.snapshot().state == "disarmed"

    app._arm()
    assert app.engine.snapshot().state == "pending"
    assert not app._refresh_session_ui().ready
    assert app.arm_button.instate(["disabled"])
    assert not gui.backend.pressed


@pytest.mark.parametrize("missing", ["connection", "game", "ack", "hotkey"])
def test_direct_start_call_cannot_bypass_missing_live_setup(gui, missing):
    app = gui.app
    app.mode_var.set("live")
    app._mode_changed()
    app.engine.set_connected(missing != "connection")
    gui.backend.select_target(None if missing == "game" else test_app_flow.FakeWindow())
    gui.backend.safety_ready = missing != "hotkey"
    app.ack_var.set(missing != "ack")

    assert not app._refresh_session_ui().ready
    app._arm()

    assert app.engine.snapshot().state == "disarmed"
    assert not gui.backend.pressed
    assert not any(event[0] in {"down", "up"} for event in gui.backend.events)


def test_first_command_draft_can_be_validated_from_start_without_a_dead_end(gui):
    app = gui.app
    profile = replace(Profile.default(), commands=())
    app.engine.set_profile(profile)
    app._show_profile(profile)
    assert app._refresh_session_ui().action == "commands"
    assert app.arm_button.instate(["disabled"])

    app.command_editor.new_command()
    assert app.command_editor.dirty
    assert app._refresh_session_ui().ready
    app._arm()

    assert any(command.enabled for command in app._profile.commands)
    assert not app.command_editor.dirty
    assert app.engine.snapshot().state == "armed"
    assert app.engine.snapshot().preview
    assert app.sim_button.instate(["!disabled"])
    assert not any(event[0] in {"down", "up"} for event in gui.backend.events)


def test_start_checks_enabled_commands_again_after_applying_a_draft(gui):
    app = gui.app
    profile = replace(Profile.default(), commands=(Profile.default().commands[0],))
    app.engine.set_profile(profile)
    app._show_profile(profile)
    app.command_editor.enabled_var.set(False)
    assert app._refresh_session_ui().ready

    app._arm()

    assert not any(command.enabled for command in app._profile.commands)
    assert app.engine.snapshot().state == "disarmed"
    assert app._refresh_session_ui().action == "commands"


def test_applied_unsaved_profile_does_not_require_a_disk_save_to_preview(gui):
    app = gui.app
    app.profile_name.set("Unsaved test profile")
    assert app._apply_profile()
    assert app._has_unsaved_changes()
    assert app._refresh_session_ui().ready
    assert app._profile_path is None

    app._arm()

    assert app.engine.snapshot().state == "armed"
    assert app.engine.snapshot().preview


@pytest.mark.parametrize("action,index", [
    ("commands", 1), ("safety", 2), ("connect", 0), ("game", 0), ("ack", 0),
])
def test_next_step_navigation_finds_the_right_page_without_arming(gui, action, index):
    app = gui.app
    app.notebook.select(3)

    app._go_to_step(action)

    assert app.notebook.index(app.notebook.select()) == index
    assert app.engine.snapshot().state == "disarmed"
    assert not app.ack_var.get()
    assert not gui.clients
    if action in {"connect", "game", "ack"}:
        assert app.connection_setup_var.get()


def test_profile_shortcuts_delegate_once_without_starting_a_session(gui, monkeypatch):
    app = gui.app
    calls = []
    monkeypatch.setattr(app, "_save_profile", lambda **kwargs: calls.append(("save", kwargs.get("save_as", False))))
    monkeypatch.setattr(app, "_import_profile", lambda: calls.append(("open",)))

    for action in ("save", "save_as", "open"):
        assert app._shortcut(action) == "break"

    assert calls == [("save", False), ("save", True), ("open",)]
    assert app.engine.snapshot().state == "disarmed"
    assert app.root.bind("<Control-s>")
    assert app.root.bind("<Control-o>")
    assert app.root.bind("<F8>")


@pytest.mark.parametrize("action", ["save", "save_as", "open", "tab_0", "tab_3"])
def test_shortcut_during_capture_cancels_capture_without_executing_action(gui, monkeypatch, action):
    app = gui.app
    app.notebook.select(1)
    monkeypatch.setattr(app, "_save_profile", lambda **kwargs: pytest.fail("Capture must consume save shortcuts"))
    monkeypatch.setattr(app, "_import_profile", lambda: pytest.fail("Capture must consume open shortcuts"))
    app.command_editor.start_capture()
    assert app.command_editor.capture_active

    assert app._shortcut(action) == "break"

    assert not app.command_editor.capture_active
    assert app.notebook.index(app.notebook.select()) == 1
    assert app.engine.snapshot().state == "disarmed"
    assert "capture canceled" in app.notice_var.get().lower()


def test_tab_shortcuts_only_navigate_and_never_arm(gui):
    app = gui.app
    for index in range(4):
        assert app._shortcut(f"tab_{index}") == "break"
        assert app.notebook.index(app.notebook.select()) == index
        assert app.engine.snapshot().state == "disarmed"
    assert not gui.clients


def test_ctrl_s_key_event_saves_once_from_a_focused_editor_field(gui, monkeypatch):
    app = gui.app
    calls = []
    monkeypatch.setattr(app, "_save_profile", lambda **kwargs: calls.append(kwargs))
    app.root.deiconify()
    app._go_to_step("commands")
    app.root.update()
    app.command_editor.trigger_entry.focus_force()
    app.root.update()
    assert app.root.focus_get() is app.command_editor.trigger_entry
    original = app.command_editor.trigger_var.get()

    app.command_editor.trigger_entry.event_generate("<Control-s>")
    app.root.update()

    assert calls == [{}]
    assert app.command_editor.trigger_var.get() == original
    assert app.engine.snapshot().state == "disarmed"


def test_capture_field_consumes_modified_key_events_before_profile_shortcuts(gui, monkeypatch):
    app = gui.app
    calls = []
    monkeypatch.setattr(app, "_save_profile", lambda **kwargs: calls.append(kwargs))
    app.root.deiconify()
    app._go_to_step("commands")
    app.root.update()
    app.root.focus_force()
    app.command_editor.start_capture()
    app.root.update()
    assert app.root.focus_get() is app.command_editor.capture_entry
    original = app.command_editor.key_var.get()

    app.command_editor.capture_entry.event_generate("<Control-s>")
    app.root.update()

    assert calls == []
    assert app.command_editor.key_var.get() == original
    assert app.engine.snapshot().state == "disarmed"
    assert not any(event[0] in {"down", "up"} for event in gui.backend.events)


def test_summary_feedback_does_not_echo_client_identifier(gui):
    app = gui.app
    private_value = "synthetic-runtime-client-value"
    app.client_id_var.set(private_value)
    app.mode_var.set("live")
    app._mode_changed()
    app._pump()
    result = app._refresh_session_ui()

    summary = " ".join((result.headline, result.detail, app.status_var.get(),
                        app.readiness_var.get(), app.readiness_detail_var.get(),
                        app.notice_var.get(), app.log.get("1.0", "end")))
    assert private_value not in summary
    assert app.client_entry.cget("show")
    assert app.engine.snapshot().state == "disarmed"


@pytest.mark.parametrize("operation", ["start", "save"])
def test_invalid_hidden_safety_field_keeps_both_drafts_and_guides_to_correction(
    gui, monkeypatch, tmp_path, operation,
):
    app, editor = gui.app, gui.app.command_editor
    original = app._profile
    destination = tmp_path / "gui-recovery.json"
    monkeypatch.setattr(app_module.filedialog, "asksaveasfilename", lambda **kwargs: str(destination))
    editor.trigger_var.set("!walk")
    app.limit_vars["queue_size"].set("99")
    assert editor.dirty and app._dirty
    assert not app._advanced_open
    app.root.deiconify()
    app.root.geometry("940x640+0+0")
    app.notebook.select(0)
    app.root.update()
    app.root.focus_force()

    if operation == "start":
        app._arm()
    else:
        assert not app._save_profile()
    app.root.update()

    assert app.engine.snapshot().state == "disarmed"
    assert app._profile == original
    assert app.engine.profile == original
    assert app._saved_profile == original
    assert app.notebook.index(app.notebook.select()) == 2
    assert app._advanced_open
    assert app.advanced_frame.winfo_ismapped()
    assert app.root.focus_get() is app.safety_entries["queue_size"]
    assert app.limit_vars["queue_size"].get() == "99"
    assert editor.trigger_var.get() == "!walk"
    assert editor.dirty and app._dirty
    assert not destination.exists()

    app.limit_vars["queue_size"].set("4")
    if operation == "start":
        app._arm()
        assert app.engine.snapshot().state == "armed"
        assert app.engine.snapshot().preview
    else:
        assert app._save_profile()
        assert destination.exists()
        assert app.engine.snapshot().state == "disarmed"
    assert app._profile.queue_size == 4
    assert app._profile.commands[0].trigger == "!walk"
    assert not editor.dirty
    assert not app._dirty


def test_invalid_command_from_another_tab_reveals_and_focuses_the_problem(gui):
    app, editor = gui.app, gui.app.command_editor
    original = app._profile
    editor.hold_var.set("451")
    app.root.deiconify()
    app.root.geometry("940x640+0+0")
    app.notebook.select(3)
    app.root.update()
    app.root.focus_force()

    app._arm()
    app.root.update()

    assert app.engine.snapshot().state == "disarmed"
    assert app._profile == original
    assert app.notebook.index(app.notebook.select()) == 1
    assert app.root.focus_get() is editor.hold_entry
    assert editor.hold_var.get() == "451"
    assert editor.dirty
    assert "450" in editor.error_var.get()


def test_focus_navigation_reveals_an_offscreen_control_in_a_small_window(gui):
    app = gui.app
    app.root.deiconify()
    app.root.geometry("940x640+0+0")
    app.notebook.select(2)
    app._toggle_advanced()
    app.root.update()
    app.root.focus_force()
    app.root.update()
    page = app._pages[2]
    target = app.safety_entries["blocked_user_ids"]
    page.canvas.yview_moveto(0)
    app.root.update_idletasks()
    assert target.winfo_rooty() + target.winfo_height() > page.canvas.winfo_rooty() + page.canvas.winfo_height()

    app._focus_widget(target)
    app.root.update()

    assert app.root.focus_get() is target
    assert page.canvas.yview()[0] > 0
    assert target.winfo_rooty() >= page.canvas.winfo_rooty()
    assert target.winfo_rooty() + target.winfo_height() <= page.canvas.winfo_rooty() + page.canvas.winfo_height()
    assert app.engine.snapshot().state == "disarmed"


def test_active_live_session_remains_authoritative_over_an_unapplied_mode_choice(gui):
    app = gui.app
    test_app_flow.arm_live(gui)
    assert gui.backend.live
    assert app.engine.snapshot().state == "armed"
    assert not app.engine.snapshot().preview

    app.mode_var.set("preview")  # Deliberately bypass the normal disarming callback.
    result = app._refresh_session_ui()

    assert app.mode_var.get() == "live"
    assert app.state_var.get() == "ARMED · LIVE GAME INPUT"
    assert "Live control active" in app.status_var.get()
    assert result.headline == "Chat control is active"
    summary = (app.status_var.get() + result.headline + result.detail).lower()
    assert "no keys" not in summary and "no keyboard input" not in summary
    assert not result.ready
    assert app.sim_button.instate(["disabled"])
    assert app.preview_card.winfo_manager() == ""
    assert app.game_card.winfo_manager() == "grid"
    assert "Pause live control" in app.sim_result_var.get()
    assert "nothing will be sent" not in app.sim_result_var.get().lower()
    assert app.engine.snapshot().state == "armed"
    assert not app.engine.snapshot().preview
    assert not any(event[0] in {"down", "up"} for event in gui.backend.events)


def test_unchanged_session_refresh_is_cached_and_real_changes_invalidate_it(gui, monkeypatch):
    app = gui.app
    baseline = app._refresh_session_ui()
    configured = []

    def watch(widget, name):
        original = widget.configure

        def configure(*args, **kwargs):
            configured.append(name)
            return original(*args, **kwargs)

        monkeypatch.setattr(widget, "configure", configure)

    watch(app.status_label, "status")
    watch(app.arm_button, "start")
    assert app._refresh_session_ui() is baseline
    assert app._refresh_session_ui() is baseline
    assert configured == []

    app.command_editor.hold_var.set("200")
    draft = app._refresh_session_ui()
    assert draft is not baseline
    assert draft.ready and draft.action == "commands"
    assert "pending command edits" in draft.detail
    app.command_editor.discard_pending()
    app.mode_var.set("live")
    disconnected = app._refresh_session_ui()
    assert disconnected.action == "connect"
    app.engine.set_connected(True)
    connected = app._refresh_session_ui()
    assert connected is not disconnected
    assert connected.action == "game"
    assert not connected.ready


def test_pump_keeps_one_scheduled_callback_and_close_cancels_it(gui, monkeypatch):
    app = gui.app

    def pump_callbacks():
        return [identifier for identifier in app.root.tk.call("after", "info")
                if str(app.root.tk.call("after", "info", identifier)[0]).endswith("_pump")]

    for _ in range(3):
        app._pump()
        assert pump_callbacks() == [app._pump_id]
    pending = app._pump_id
    canceled = []
    original_cancel = app.root.after_cancel

    def after_cancel(identifier):
        canceled.append(identifier)
        original_cancel(identifier)

    monkeypatch.setattr(app.root, "after_cancel", after_cancel)
    app.close(force=True)

    assert pending in canceled
    assert app._pump_id is None
    assert not pump_callbacks()
    app._pump()  # A previously queued callback is harmless after shutdown.
    assert not pump_callbacks()
