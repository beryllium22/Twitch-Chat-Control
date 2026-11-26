"""Shareable controls and pause guidance stay bounded and free of session data."""

from dataclasses import replace

import pytest

from twitch_chat_control.models import AUDIENCES, MAX_COMMANDS, Command, Profile
from twitch_chat_control.session_tools import MAX_REFERENCE_CHARACTERS, command_reference, paused_guidance


def test_reference_contains_enabled_keyboard_and_mouse_actions_and_correct_limits():
    profile = Profile(
        commands=(
            Command("!jump", "space", hold_ms=100, cooldown_ms=1000),
            Command("!fire", action_type="mouse_button", button="x2", hold_ms=250),
            Command("!look", action_type="mouse_move", dx=-25, dy=10),
            Command("!next", action_type="mouse_wheel", wheel=-2, cooldown_ms=0),
            Command("!disabled", "w", enabled=False),
        ),
        global_cooldown_ms=250,
        viewer_cooldown_ms=1500,
        max_actions_per_minute=45,
    )
    reference = command_reference(profile)
    assert reference.startswith("Chat controls\nWho can play: Moderators and the streamer")
    for command in profile.commands[:-1]:
        assert f"{command.trigger} — {command.action_summary}" in reference
        assert reference.count(command.trigger) == 1
    assert "!disabled" not in reference
    assert "Viewer cooldown: 1.5 s" in reference
    assert "shared cooldown: 250 ms" in reference
    assert "limit: 45 actions per minute" in reference
    assert "command cooldown 1 s" in reference
    assert "shared limits apply" in reference


def test_reference_omits_local_freeform_names_and_user_ids():
    profile = Profile(
        name="PRIVATE_VALUE_NOT_FOR_CHAT",
        allowed_user_ids=frozenset({"123456789012345"}),
        blocked_user_ids=frozenset({"987654321098765"}),
    )
    reference = command_reference(profile)
    assert "PRIVATE_VALUE" not in reference
    assert "123456789012345" not in reference
    assert "987654321098765" not in reference
    assert "Additional local allow/block restrictions apply." in reference
    assert "allow/block" not in command_reference(Profile.default())


@pytest.mark.parametrize("audience,word", [
    ("everyone", "Everyone in chat"),
    ("subscriber", "Subscribers, moderators, and the streamer"),
    ("vip", "VIPs, moderators, and the streamer"),
    ("moderator", "Moderators and the streamer"),
    ("broadcaster", "The streamer only"),
])
def test_reference_explains_every_supported_audience(audience, word):
    assert audience in AUDIENCES
    assert "Who can play: " + word in command_reference(Profile(audience=audience))


@pytest.mark.parametrize("commands", [(), (Command("!disabled", "w", enabled=False),)])
def test_empty_reference_does_not_advertise_disabled_controls(commands):
    result = command_reference(Profile(commands=commands))
    assert "No commands are enabled" in result
    assert "!disabled" not in result


def test_reference_stays_complete_and_bounded_at_largest_profile():
    commands = tuple(Command("!" + "a" * 21 + f"{index:02}", action_type="mouse_move", dx=-100, dy=100,
                             cooldown_ms=60_000) for index in range(MAX_COMMANDS))
    profile = Profile(commands=commands, global_cooldown_ms=60_000, viewer_cooldown_ms=60_000,
                      max_actions_per_minute=300)
    reference = command_reference(profile)
    assert len(reference) <= MAX_REFERENCE_CHARACTERS
    assert len([line for line in reference.splitlines() if line.startswith("!")]) == MAX_COMMANDS
    assert all(command.trigger in reference for command in commands)
    assert "60 s" in reference


@pytest.mark.parametrize("unvalidated", [{"commands": []}, None, "private-value", ()])
def test_reference_requires_a_validated_model_without_echoing_input(unvalidated):
    with pytest.raises(TypeError, match="A validated Profile is required"):
        command_reference(unvalidated)


def test_reference_only_uses_the_model_supplied_by_the_caller():
    applied = Profile(commands=(Command("!jump", "space"),))
    draft = replace(applied, commands=(Command("!other", action_type="mouse_wheel", wheel=1),))
    assert "!other" not in command_reference(applied)
    assert "!jump" not in command_reference(draft)


@pytest.mark.parametrize("reason,phrase", [
    ("emergency_stop", "Emergency stop"),
    ("mouse_move_outside_target", "reduce the movement"),
    ("input_release_failed_restart_required", "restart"),
    ("the_independent_mouse_release_watchdog_paused_live_input_", "safeguard"),
])
def test_safety_pause_has_fixed_plain_language_and_correction(reason, phrase):
    result = paused_guidance(reason)
    assert result is not None
    headline, detail = result
    assert headline and detail and phrase in detail
    assert "_" not in headline + detail
    assert len(headline) < 90 and len(detail) < 220


@pytest.mark.parametrize("reason", [
    "start_disarmed", "command_editing", "connection_lost", "connected_controls_remain_disarmed",
])
def test_normal_edits_and_connection_prerequisites_remain_with_readiness(reason):
    assert paused_guidance(reason) is None


@pytest.mark.parametrize("value", [
    "PRIVATE_NATIVE_ERROR", "mouse_move_outside_target\nPRIVATE_NATIVE_ERROR", "x" * 65,
    [],
])
def test_unknown_pause_reason_is_not_echoed_or_interpreted(value):
    assert paused_guidance(value) is None
