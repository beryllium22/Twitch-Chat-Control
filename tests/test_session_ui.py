"""Session guidance stays independent from credentials and native input."""

from dataclasses import FrozenInstanceError

import pytest

from twitch_chat_control.session_ui import reason_text, session_readiness


def readiness(**changes):
    values = dict(mode="preview", state="disarmed", connected=False,
                  available=False, safety_ready=False, target_selected=False,
                  faulted=False, worker_stopped=False, enabled_commands=5,
                  command_draft=False, safety_draft=False, acknowledged=False)
    values.update(changes)
    return session_readiness(**values)


def live_readiness(**changes):
    values = dict(mode="live", connected=True, available=True, safety_ready=True,
                  target_selected=True, acknowledged=True)
    values.update(changes)
    return readiness(**values)


def test_preview_needs_no_native_backend_connection_or_game():
    result = readiness(faulted=True)
    assert result.ready
    assert result.action is None
    assert "No Twitch account or game is needed" in result.detail
    assert "no game input is sent" in result.detail


@pytest.mark.parametrize("mode", ["preview", "live"])
def test_stopped_worker_requires_restart_even_for_preview(mode):
    result = live_readiness(mode=mode, worker_stopped=True)
    assert not result.ready
    assert result.action == "restart"
    assert "safety worker" in result.detail


@pytest.mark.parametrize("mode", ["", "LIVE", "other", "preview\nlive"])
def test_unknown_mode_cannot_enable_start(mode):
    assert not live_readiness(mode=mode).ready


@pytest.mark.parametrize("state", ["armed", "pending"])
@pytest.mark.parametrize("mode", ["preview", "live"])
def test_running_session_cannot_be_rearmed_from_readiness(state, mode):
    result = live_readiness(state=state, mode=mode)
    assert not result.ready
    assert result.action is None


def test_unknown_state_requires_restart():
    result = live_readiness(state="unexpected-state")
    assert not result.ready
    assert result.action == "restart"


@pytest.mark.parametrize("change,action,word", [
    ({"faulted": True}, "restart", "safety check"),
    ({"available": False}, None, "Windows"),
    ({"safety_ready": False}, "restart", "F8"),
    ({"connected": False}, "connect", "Twitch"),
    ({"target_selected": False}, "game", "game"),
    ({"acknowledged": False}, "ack", "pause"),
])
def test_each_missing_live_requirement_has_an_actionable_explanation(change, action, word):
    result = live_readiness(**change)
    assert not result.ready
    assert result.action == action
    assert word.lower() in (result.headline + result.detail).lower()


def test_restart_required_fault_takes_priority_over_missing_connection():
    result = live_readiness(faulted=True, safety_ready=False, connected=False, enabled_commands=0)
    assert result.action == "restart"
    assert "safety check" in result.detail


def test_live_ready_gives_time_to_focus_game_instead_of_requiring_foreground():
    result = live_readiness()
    assert result.ready
    assert "within 3 seconds" in result.detail
    assert result.action is None


@pytest.mark.parametrize("mode", ["preview", "live"])
def test_empty_profile_guides_to_commands_without_starting(mode):
    result = live_readiness(mode=mode, enabled_commands=0)
    assert not result.ready
    assert result.action == "commands"
    assert "Add or enable" in result.headline


@pytest.mark.parametrize("flags,action", [
    ({"command_draft": True}, "commands"),
    ({"safety_draft": True}, "safety"),
    ({"command_draft": True, "safety_draft": True}, "commands"),
])
def test_pending_drafts_allow_existing_validate_on_start_flow(flags, action):
    result = live_readiness(**flags)
    assert result.ready
    assert result.action == action
    assert "checked and applied before starting" in result.detail


def test_command_draft_may_add_the_first_enabled_command():
    result = readiness(enabled_commands=0, command_draft=True)
    assert result.ready
    assert result.action == "commands"


def test_safety_draft_alone_does_not_make_an_empty_profile_usable():
    result = readiness(enabled_commands=0, safety_draft=True)
    assert not result.ready
    assert result.action == "commands"


def test_pending_draft_does_not_bypass_live_prerequisites():
    result = live_readiness(command_draft=True, connected=False)
    assert not result.ready
    assert result.action == "connect"


def test_readiness_result_is_immutable():
    with pytest.raises(FrozenInstanceError):
        readiness().ready = False


@pytest.mark.parametrize("reason,word", [
    ("emergency_stop", "Emergency stop"),
    ("target_not_foreground", "lost focus"),
    ("viewer_cooldown", "wait"),
    ("audience_restricted", "role"),
    ("input_release_failed_restart_required", "restart"),
    ("would_tap", "No keyboard or mouse input"),
    ("not_an_exact_command", "exact configured command"),
    ("the_selected_game_lost_focus_or_changed_identity_", "changed identity"),
    ("a_modifier_key_was_pressed__live_input_was_paused_", "modifier key"),
    ("the_independent_key_release_watchdog_paused_live_input_", "safeguard"),
    ("f8_is_unavailable__close_the_app_using_it__then_restart_this_app", "F8"),
    ("the_input_safety_monitor_stopped__live_control_is_disabled_", "Restart"),
])
def test_known_reasons_explain_the_event_in_plain_language(reason, word):
    result = reason_text(reason)
    assert word.lower() in result.lower()
    assert "_" not in result


@pytest.mark.parametrize("untrusted", [
    "private runtime value", "not_connected\nprivate runtime value", "a" * 1000,
    "Not_Connected", "", None, [], {},
])
def test_unknown_reason_never_echoes_untrusted_values(untrusted):
    assert reason_text(untrusted) == "No further details are available for this event."
