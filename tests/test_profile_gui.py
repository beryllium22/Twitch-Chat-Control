"""Profile navigation and editor integration with no network or native input."""

import json
from dataclasses import replace

import pytest
import test_app_flow

from twitch_chat_control import app as app_module
from twitch_chat_control.models import Profile
from twitch_chat_control.profile_io import save_profile

gui = test_app_flow.gui


@pytest.fixture
def saved_profile(gui, tmp_path, monkeypatch):
    path = tmp_path / "original.json"
    monkeypatch.setattr(app_module.filedialog, "asksaveasfilename", lambda **kwargs: str(path))
    assert gui.app._save_profile()
    assert not gui.app._has_unsaved_changes()
    return path, gui.app._profile


def test_applied_mapping_remains_unsaved_after_draft_is_cleared(gui, saved_profile):
    app, editor = gui.app, gui.app.command_editor
    path, baseline = saved_profile
    editor.trigger_var.set("!walk")
    editor.key_var.set("Arrow Up")
    assert editor.dirty
    assert app._has_unsaved_changes()
    assert app._profile == baseline

    assert editor.apply_pending()
    assert not editor.dirty
    assert not app._dirty
    assert app._profile.commands[0].trigger == "!walk"
    assert app._profile.commands[0].key == "up"
    assert app.engine.profile == app._profile
    assert app._saved_profile == baseline
    assert app._has_unsaved_changes()

    editor.hold_var.set("200")
    editor.discard_pending()
    assert editor.trigger_var.get() == "!walk"
    assert app._has_unsaved_changes()
    assert app._save_profile()
    assert app._profile_path == path
    assert app._saved_profile == app._profile
    assert not app._has_unsaved_changes()


def test_cancel_save_as_preserves_applied_profile_and_exact_draft(gui, saved_profile, monkeypatch):
    app, editor = gui.app, gui.app.command_editor
    path, baseline = saved_profile
    app.profile_name.set("Unsaved game name")
    editor.trigger_var.set("!walk")
    editor.hold_var.set("220")
    monkeypatch.setattr(app_module.filedialog, "asksaveasfilename", lambda **kwargs: "")

    assert not app._save_profile(save_as=True)

    assert app._profile_path == path
    assert app._saved_profile == baseline
    assert app._profile == baseline
    assert app.profile_name.get() == "Unsaved game name"
    assert editor.trigger_var.get() == "!walk"
    assert editor.hold_var.get() == "220"
    assert editor.dirty and app._dirty
    assert app._has_unsaved_changes()


def test_failed_save_as_retains_edits_and_previous_saved_identity(gui, saved_profile, tmp_path, monkeypatch):
    app, editor = gui.app, gui.app.command_editor
    path, baseline = saved_profile
    original_bytes = path.read_bytes()
    destination = tmp_path / "different.json"
    app.profile_name.set("Edited game")
    editor.trigger_var.set("!walk")
    editor.hold_var.set("220")
    monkeypatch.setattr(app_module.filedialog, "asksaveasfilename", lambda **kwargs: str(destination))

    def fail_save(profile, target):
        assert target == destination
        assert profile.name == "Edited game"
        assert profile.commands[0].trigger == "!walk"
        raise OSError("synthetic-private-filesystem-detail")

    monkeypatch.setattr(app_module, "save_profile", fail_save)
    assert not app._save_profile(save_as=True)

    assert app._profile_path == path
    assert app._saved_profile == baseline
    assert path.read_bytes() == original_bytes
    assert not destination.exists()
    assert app._profile.name == "Edited game"
    assert editor.trigger_var.get() == "!walk"
    assert editor.hold_var.get() == "220"
    assert app._has_unsaved_changes()
    assert gui.messages[-1][0] == "Could not save profile"
    assert "synthetic-private-filesystem-detail" not in str(gui.messages)


@pytest.mark.parametrize("operation", ["open", "new"])
def test_cancel_profile_replacement_keeps_applied_and_draft_changes(
    gui, saved_profile, tmp_path, monkeypatch, operation,
):
    app, editor = gui.app, gui.app.command_editor
    path, baseline = saved_profile
    editor.key_var.set("R")
    assert editor.apply_pending()
    applied = app._profile
    editor.trigger_var.set("!walk")
    app.profile_name.set("Pending name")
    replacement = tmp_path / "replacement.json"
    save_profile(replace(Profile.default(), name="Other game"), replacement)
    monkeypatch.setattr(app_module.filedialog, "askopenfilename", lambda **kwargs: str(replacement))
    monkeypatch.setattr(app_module.messagebox, "askyesnocancel", lambda *args, **kwargs: None)

    if operation == "open":
        app._import_profile()
    else:
        app.template_var.set("Blank")
        app._new_profile()

    assert app._profile_path == path
    assert app._saved_profile == baseline
    assert app._profile == applied
    assert app.engine.profile == applied
    assert app.profile_name.get() == "Pending name"
    assert editor.trigger_var.get() == "!walk"
    assert editor.key_var.get() == "R"
    assert editor.dirty and app._dirty
    assert app._has_unsaved_changes()
    assert app.engine.snapshot().state == "disarmed"


def test_invalid_open_validates_before_discard_prompt(gui, saved_profile, tmp_path, monkeypatch):
    app, editor = gui.app, gui.app.command_editor
    path, baseline = saved_profile
    editor.key_var.set("R")
    assert editor.apply_pending()
    applied = app._profile
    editor.trigger_var.set("!walk")
    app.profile_name.set("Pending name")
    invalid = tmp_path / "invalid.json"
    invalid.write_text('{"unexpected": "not a command profile"}', encoding="utf-8")
    monkeypatch.setattr(app_module.filedialog, "askopenfilename", lambda **kwargs: str(invalid))
    monkeypatch.setattr(
        app_module.messagebox, "askyesnocancel",
        lambda *args, **kwargs: pytest.fail("An invalid file must not offer to discard the current profile"),
    )

    app._import_profile()

    assert app._profile == applied
    assert app.engine.profile == applied
    assert app._saved_profile == baseline
    assert app._profile_path == path
    assert editor.trigger_var.get() == "!walk"
    assert editor.dirty
    assert app.profile_name.get() == "Pending name"
    assert gui.messages[-1][0] == "Profile rejected"


def test_cancel_close_releases_active_and_queued_input_but_keeps_session(gui, monkeypatch):
    app = gui.app
    client = test_app_flow.active_and_queued(gui)
    assert app._has_unsaved_changes()

    def cancel_close(*args, **kwargs):
        assert app.engine.snapshot().state == "disarmed"
        assert app.engine.snapshot().queue_depth == 0
        assert not gui.backend.pressed
        return None

    monkeypatch.setattr(app_module.messagebox, "askyesnocancel", cancel_close)
    app.close()

    assert not app._closed
    assert not app._stop.is_set()
    assert app.root.winfo_exists()
    assert not gui.backend.closed
    assert client.running
    assert app.engine.snapshot().state == "disarmed"
    accepted = app.engine.snapshot().accepted
    client.on_chat(test_app_flow.chat(gui, "after-canceled-close"))
    app.engine.tick()
    assert app.engine.snapshot().accepted == accepted
    assert not gui.backend.pressed


def test_save_canceled_from_close_prompt_keeps_draft_open(gui, monkeypatch):
    app, editor = gui.app, gui.app.command_editor
    baseline = app._profile
    editor.trigger_var.set("!walk")
    monkeypatch.setattr(app_module.messagebox, "askyesnocancel", lambda *args, **kwargs: True)
    monkeypatch.setattr(app_module.filedialog, "asksaveasfilename", lambda **kwargs: "")

    app.close()

    assert not app._closed
    assert app._profile == baseline
    assert app._profile_path is None
    assert editor.dirty
    assert editor.trigger_var.get() == "!walk"
    assert app.engine.snapshot().state == "disarmed"


def test_pending_invalid_command_blocks_arm_without_using_old_mapping(gui):
    app, editor = gui.app, gui.app.command_editor
    original = app._profile
    editor.hold_var.set("451")
    assert editor.dirty

    app._arm()

    assert app.engine.snapshot().state == "disarmed"
    assert app.engine.profile == original
    assert app._profile == original
    assert editor.hold_var.get() == "451"
    assert editor.dirty
    assert "450" in editor.error_var.get()
    assert not any(event[0] in {"down", "up"} for event in gui.backend.events)


def test_native_emergency_stop_cancels_capture_without_tk_keypress(gui):
    app, editor = gui.app, gui.app.command_editor
    editor.start_capture()
    assert editor.capture_active

    # The registered global hotkey can consume F8 before Tk receives it.
    app.engine.emergency_stop()
    assert editor.capture_active
    app._pump()

    assert not editor.capture_active
    assert app.engine.snapshot().state == "disarmed"
    assert app.engine.snapshot().reason == "emergency_stop"


def test_mapping_and_safety_drafts_apply_atomically(gui):
    app, editor = gui.app, gui.app.command_editor
    original = app._profile
    app.limit_vars["global_cooldown_ms"].set("not-a-number")
    editor.trigger_var.set("!walk")
    editor.key_var.set("R")

    assert not editor.apply_pending()
    assert app._profile == original
    assert app.engine.profile == original
    assert editor.dirty
    assert editor.trigger_var.get() == "!walk"
    assert "global cooldown" in gui.messages[-1][1]

    app.limit_vars["global_cooldown_ms"].set("500")
    assert editor.apply_pending()
    assert app._profile.global_cooldown_ms == 500
    assert app._profile.commands[0].trigger == "!walk"
    assert app._profile.commands[0].key == "r"
    assert editor.selected_trigger == "!walk"
    assert app.engine.profile == app._profile
    assert not editor.dirty
    assert not app._dirty
    assert app._has_unsaved_changes()


@pytest.mark.parametrize("role,expected", [("moderator", "Allowed as moderator"), ("viewer", "audience restricted")])
def test_mapping_preview_isolates_connected_chat_and_native_input(gui, role, expected):
    app = gui.app
    client = test_app_flow.arm_live(gui)
    app.sim_role.set(role)
    gui.backend.events.clear()
    accepted = app.engine.snapshot().accepted

    app.command_editor.preview_command()
    app._pump()

    assert expected in app.command_editor.preview_var.get()
    assert app.mode_var.get() == "preview"
    assert not app.ack_var.get()
    assert app.engine.snapshot().state == "disarmed"
    assert app.state_var.get() == "DISARMED · PREVIEW"
    assert app.engine.snapshot().accepted == accepted
    assert client.running
    client.on_chat(test_app_flow.chat(gui, "during-isolated-preview"))
    app.engine.tick()
    assert app.engine.snapshot().accepted == accepted
    assert app.engine.snapshot().queue_depth == 0
    assert not gui.backend.pressed
    assert not any(event[0] in {"down", "up"} for event in gui.backend.events)


def test_saved_json_contains_only_strict_profile_fields(gui, tmp_path, monkeypatch):
    app = gui.app
    app.client_id_var.set("synthetic-session-only-client-id")
    app.channel_var.set("synthetic_session_channel")
    app.profile_name.set("My GUI profile")
    app.command_editor.trigger_var.set("!walk")
    app.command_editor.key_var.set("Arrow Up")
    path = tmp_path / "saved.json"
    monkeypatch.setattr(app_module.filedialog, "asksaveasfilename", lambda **kwargs: str(path))

    assert app._save_profile()

    raw = path.read_text(encoding="utf-8")
    data = json.loads(raw)
    assert data == app._profile.to_dict()
    assert set(data) == {
        "schema_version", "name", "audience", "global_cooldown_ms", "viewer_cooldown_ms",
        "max_actions_per_minute", "queue_size", "queue_ttl_ms", "allowed_user_ids",
        "blocked_user_ids", "commands",
    }
    assert all(set(command) == {"trigger", "key", "hold_ms", "cooldown_ms", "enabled"}
               for command in data["commands"])
    assert "synthetic-session-only-client-id" not in raw
    assert "synthetic_session_channel" not in raw
    assert app._profile_path == path
    assert app._saved_profile == app._profile
    assert not app._has_unsaved_changes()
