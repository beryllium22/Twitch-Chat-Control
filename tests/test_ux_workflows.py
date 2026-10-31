"""Guided desktop workflows with real Tk and isolated clipboard/input boundaries."""

from dataclasses import replace

import pytest
import test_app_flow as desktop

from twitch_chat_control import app as app_module
from twitch_chat_control.models import Command, Profile
from twitch_chat_control.session_ui import reason_text

gui = desktop.gui


@pytest.mark.parametrize("geometry", ["940x640", "960x700"])
def test_enlarged_footer_rewraps_after_mode_and_setup_label_changes(geometry):
    # A fixed-size footer used to keep stale wrap estimates when the longer
    # Live action label changed the space allocated to its guidance text.
    try:
        root = desktop.tk.Tk()
    except desktop.tk.TclError:
        pytest.skip("Desktop integration requires an available Tk display")
    scaling = root.tk.call("tk", "scaling")
    app = None
    try:
        root.tk.call("tk", "scaling", 2.0)
        app = app_module.App(root, backend=desktop.FakeBackend(), start_workers=False)
        root.geometry(geometry)
        root.update()

        def guidance_fits():
            root.update()
            for label in (app.readiness_heading, app.readiness_label):
                assert label.winfo_ismapped()
                assert label.winfo_reqwidth() <= label.winfo_width()
                assert label.winfo_reqheight() <= label.winfo_height()
                assert int(label.cget("wraplength")) < label.winfo_width()
                assert label.winfo_rootx() + label.winfo_width() <= root.winfo_rootx() + root.winfo_width()
            assert app.readiness_label.winfo_rooty() >= app.arm_button.winfo_rooty() + app.arm_button.winfo_height()
            assert app.pause_button.winfo_rootx() + app.pause_button.winfo_width() <= root.winfo_rootx() + root.winfo_width()
            assert app.notebook.winfo_height() > 0
            assert app.engine.snapshot().state == "disarmed"

        guidance_fits()
        app.mode_var.set("live")
        app._mode_changed()
        guidance_fits()
        for step in ("connected", "target", "acknowledged", "safety_stop"):
            if step == "connected":
                app.engine.set_connected(True)
            elif step == "target":
                app.backend.select_target(desktop.FakeWindow())
            elif step == "acknowledged":
                app.ack_var.set(True)
            else:
                app.engine.disarm("mouse_pointer_outside_target")
            app._refresh_session_ui()
            guidance_fits()
        app.mode_var.set("preview")
        app._mode_changed()
        guidance_fits()
    finally:
        for callback in root.tk.call("after", "info"):
            root.after_cancel(callback)
        root.tk.call("tk", "scaling", scaling)
        if app is not None and not app._closed:
            app.close(force=True)
        else:
            root.destroy()


def apply_profile(gui, profile):
    gui.app.engine.set_profile(profile)
    gui.app._show_profile(profile)


def movement_profile(**settings):
    return Profile(commands=(Command("!look", action_type="mouse_move", dx=25, dy=-10),), **settings)


@pytest.fixture
def local_clipboard(gui, monkeypatch):
    """Observe explicit copy requests without changing the machine's clipboard."""
    state = {"text": "Existing local clipboard", "clear_calls": 0}

    def clear():
        state["clear_calls"] += 1
        state["text"] = ""

    def append(text):
        state["text"] += text

    monkeypatch.setattr(gui.app.root, "clipboard_clear", clear)
    monkeypatch.setattr(gui.app.root, "clipboard_append", append)
    return state


@pytest.mark.parametrize("expires", [False, True], ids=["runs", "expires"])
def test_practice_pad_waits_for_executed_preview(gui, expires):
    app = gui.app
    apply_profile(gui, movement_profile(queue_ttl_ms=100))
    app._arm()
    center = app.preview_pad.virtual_position
    original_summary = app.preview_pad.summary_var.get()
    app.sim_var.set("!look")
    app._simulate()
    app._pump()

    assert app.engine.snapshot().accepted == 1
    assert app.engine.snapshot().queue_depth == 1
    assert app.preview_pad.virtual_position == center
    assert app.preview_pad.summary_var.get() == original_summary
    assert "queued in Preview" in app.sim_result_var.get()

    if expires:
        gui.clock.now += 0.11
    app.engine.tick()
    app._pump()

    assert app.engine.snapshot().queue_depth == 0
    if expires:
        assert app.preview_pad.virtual_position == center
        assert reason_text("queue_expired") in app.preview_pad.summary_var.get()
        assert app.sim_result_var.get() == reason_text("queue_expired")
    else:
        assert app.preview_pad.virtual_position == (center[0] + 25, center[1] - 10)
        assert "!look" in app.preview_pad.summary_var.get()
        assert "Preview ran:" in app.sim_result_var.get()
    assert app.notice_var.get() == app.sim_result_var.get()
    assert "queued in Preview" not in app.notice_var.get()
    assert all(event[0] == "release_all" for event in gui.backend.events)
    assert not gui.clients


@pytest.mark.parametrize("transition", ["replace_profile", "restart_preview", "start_live"])
def test_delayed_preview_never_animates_a_new_session(gui, transition):
    app = gui.app
    profile = movement_profile()
    apply_profile(gui, profile)
    app._arm()
    app.sim_var.set("!look")
    app._simulate()
    app.engine.tick()  # The completed Preview event is still waiting for Tk.
    previous_generation = app.engine.snapshot().generation

    if transition == "replace_profile":
        changed = replace(profile, commands=(replace(profile.commands[0], dx=-80),))
        apply_profile(gui, changed)
        app._arm()
    elif transition == "restart_preview":
        app.engine.disarm()
        app._arm()
    else:
        client = desktop.arm_live(gui)
        client.on_chat(desktop.chat(gui, "live-after-preview"))
        app.engine.tick()
        assert gui.backend.pressed == {"w"}

    assert app.engine.snapshot().generation != previous_generation
    position = app.preview_pad.virtual_position
    summary = app.preview_pad.summary_var.get()
    app._pump()
    assert app.preview_pad.virtual_position == position
    assert app.preview_pad.summary_var.get() == summary
    if transition == "start_live":
        assert not app.engine.snapshot().preview
        assert "Game input" in app.log.get("1.0", "end")


def test_copy_uses_enabled_commands_and_omits_private_local_data(gui, local_clipboard):
    app = gui.app
    profile = Profile(
        name="PRIVATE_LOCAL_PROFILE_LABEL",
        commands=(Command("!jump", "space"),
                  Command("!look", action_type="mouse_move", dx=-25),
                  Command("!disabled", "w", enabled=False)),
        allowed_user_ids=frozenset({"9876543210"}), blocked_user_ids=frozenset({"9876543211"}),
    )
    apply_profile(gui, profile)
    app.client_id_var.set("PRIVATE_PUBLIC_CLIENT_ID")
    app.channel_var.set("private_channel")
    app.window_var.set("PRIVATE_SELECTED_WINDOW")
    app._device_value = desktop.tk.StringVar(app.root, value="PRIVATE_SIGN_IN_VALUE")
    app._arm()
    generation = app.engine.snapshot().generation
    assert local_clipboard["clear_calls"] == 0

    assert app._copy_chat_commands()
    copied = local_clipboard["text"]
    assert [line.split(" — ")[0] for line in copied.splitlines() if line.startswith("!")] == ["!jump", "!look"]
    assert "Who can play: Moderators and the streamer" in copied
    assert "Additional local allow/block restrictions apply." in copied
    for private_value in (profile.name, *profile.allowed_user_ids, *profile.blocked_user_ids,
                          app.client_id_var.get(), app.channel_var.get(), app.window_var.get(),
                          app._device_value.get(), "!disabled"):
        assert private_value not in copied
    assert local_clipboard["clear_calls"] == 1
    assert "Copied 2 enabled chat commands" in app.notice_var.get()
    assert app.engine.snapshot().state == "armed"
    assert app.engine.snapshot().generation == generation
    assert app._profile_path is None
    assert not gui.clients


@pytest.mark.parametrize("problem", ["invalid_command", "invalid_safety", "no_enabled_commands"])
def test_unsuitable_profile_does_not_change_clipboard(gui, local_clipboard, problem):
    app = gui.app
    if problem == "invalid_command":
        app.command_editor.trigger_var.set("!two words")
    elif problem == "invalid_safety":
        app.limit_vars["global_cooldown_ms"].set("invalid")
    else:
        apply_profile(gui, Profile(commands=(Command("!disabled", "w", enabled=False),)))
    before = app._profile

    assert not app._copy_chat_commands()
    assert local_clipboard == {"text": "Existing local clipboard", "clear_calls": 0}
    assert app._profile == before
    assert app.engine.snapshot().state == "disarmed"
    if problem == "invalid_command":
        assert app.command_editor.dirty
        assert app.command_editor.trigger_var.get() == "!two words"
    elif problem == "invalid_safety":
        assert app._dirty
        assert app.limit_vars["global_cooldown_ms"].get() == "invalid"


def test_copy_applies_valid_pending_edits_without_saving_or_arming(gui, local_clipboard):
    app = gui.app
    apply_profile(gui, Profile(commands=(Command("!jump", "space"),)))
    app.command_editor.trigger_var.set("!hop")
    app.limit_vars["viewer_cooldown_ms"].set("2500")
    assert app.command_editor.dirty and app._dirty
    assert local_clipboard["clear_calls"] == 0

    assert app._copy_chat_commands()
    assert "!hop — Space" in local_clipboard["text"]
    assert "!jump" not in local_clipboard["text"]
    assert "Viewer cooldown: 2.5 s" in local_clipboard["text"]
    assert app.engine.profile == app._profile
    assert app._profile.commands[0].trigger == "!hop"
    assert app._profile.viewer_cooldown_ms == 2500
    assert not app.command_editor.dirty and not app._dirty
    assert app._has_unsaved_changes()
    assert app._profile_path is None
    assert app.engine.snapshot().state == "disarmed"


def test_counters_refresh_without_rebuilding_unchanged_session_ui(gui, monkeypatch):
    app = gui.app
    app._arm()
    app._refresh_session_ui()
    signature = app._ui_signature
    readiness = app._readiness

    def unexpected_full_refresh(**_kwargs):
        raise AssertionError("Counter changes should not rebuild unchanged setup guidance.")

    monkeypatch.setattr(app_module, "session_readiness", unexpected_full_refresh)
    app.sim_var.set("!jump")
    app._simulate()
    app._simulate()  # The shared cooldown blocks this second exact command.
    app._refresh_session_ui()
    assert app._ui_signature == signature
    assert app._readiness is readiness
    assert app.session_totals_var.get() == "Preview + live since app opened: 1 accepted · 1 blocked"
    assert app.queue_status_var.get() == "Queued now: 1 · Held input: None"

    app.engine.tick()
    app._refresh_session_ui()
    assert app.queue_status_var.get() == "Queued now: 0 · Held input: None"
    totals = app.session_totals_var.get()
    app._clear_log()
    app._refresh_session_ui()
    assert app.session_totals_var.get() == totals


def test_visible_safety_stop_does_not_bypass_live_readiness(gui):
    app = gui.app
    app.mode_var.set("live")
    app._mode_changed()
    app.engine.disarm("mouse_pointer_outside_target")

    for expected_action in ("connect", "game", "ack"):
        readiness = app._refresh_session_ui()
        assert not readiness.ready
        assert readiness.action == expected_action
        assert app.arm_button.instate(["disabled"])
        assert "pointer left the game" in app.readiness_var.get()
        assert "start again locally" in app.readiness_detail_var.get()
        assert readiness.detail in app.readiness_detail_var.get()
        assert app.engine.snapshot().state == "disarmed"
        if expected_action == "connect":
            app.engine.set_connected(True)
        elif expected_action == "game":
            gui.backend.select_target(desktop.FakeWindow())
        else:
            app.ack_var.set(True)

    assert app._refresh_session_ui().ready
    assert not app.arm_button.instate(["disabled"])
    assert "pointer left the game" in app.readiness_var.get()
    assert app.engine.snapshot().state == "disarmed"
    app._arm()
    assert app.engine.snapshot().state == "pending"


def test_reset_pad_preserves_session_rate_limits_and_counts(gui):
    app = gui.app
    apply_profile(gui, movement_profile())
    app._arm()
    center = app.preview_pad.virtual_position
    app.sim_var.set("!look")
    app._simulate()
    app.engine.tick()
    app._pump()
    assert app.preview_pad.virtual_position != center
    before = app.engine.snapshot()

    app.preview_pad.reset_button.invoke()
    assert app.preview_pad.virtual_position == center
    assert app.engine.snapshot() == before
    app._simulate()
    after = app.engine.snapshot()
    assert after.accepted == 1
    assert after.rejected == 1
    assert after.queue_depth == 0
    assert after.state == "armed"
    assert reason_text("global_cooldown") in app.sim_result_var.get()
    assert app.preview_pad.virtual_position == center


def test_editor_preview_illustrates_action_without_starting_main_session(gui):
    app = gui.app
    apply_profile(gui, movement_profile())
    center = app.preview_pad.virtual_position
    app.command_editor.preview_command()

    assert app.preview_pad.virtual_position == (center[0] + 25, center[1] - 10)
    assert "!look" in app.command_editor.preview_var.get()
    assert "no keyboard or mouse input sent" in app.command_editor.preview_var.get()
    snapshot = app.engine.snapshot()
    assert snapshot.state == "disarmed" and snapshot.preview
    assert snapshot.accepted == 0 and snapshot.rejected == 0
    assert snapshot.queue_depth == 0
    assert all(event[0] == "release_all" for event in gui.backend.events)
    assert not gui.clients
