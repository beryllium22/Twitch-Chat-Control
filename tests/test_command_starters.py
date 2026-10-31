"""Ready-to-edit actions keep the same bounded profile and credential model."""

from dataclasses import FrozenInstanceError, replace

import pytest

from twitch_chat_control.models import Command, Profile
from twitch_chat_control.presets import ACTION_STARTERS, PROFILE_TEMPLATES, new_profile, starter_command


@pytest.mark.parametrize("label", tuple(ACTION_STARTERS))
def test_every_starter_is_valid_immutable_and_round_trips(label):
    command = starter_command(label)
    assert type(command) is Command
    assert Command.from_dict(command.to_dict()) == command
    assert command.enabled
    assert command.cooldown_ms >= 500
    with pytest.raises(FrozenInstanceError):
        command.trigger = "!changed"
    assert starter_command(label) == command


def test_starters_cover_all_action_types_with_small_mouse_steps():
    assert {command.action_type for command in ACTION_STARTERS.values()} == {
        "key", "mouse_move", "mouse_button", "mouse_wheel",
    }
    assert len({command.trigger for command in ACTION_STARTERS.values()}) == len(ACTION_STARTERS)
    for command in ACTION_STARTERS.values():
        if command.action_type == "mouse_move":
            assert abs(command.dx) + abs(command.dy) == 25
        elif command.action_type == "mouse_wheel":
            assert abs(command.wheel) == 1
        elif command.is_held:
            assert command.hold_ms <= 150
    with pytest.raises(TypeError):
        ACTION_STARTERS["Unapproved"] = Command("!new", "w")


@pytest.mark.parametrize("label", ["", "Unknown secret value", "Look left ", None, 1, []])
def test_unknown_starters_fail_without_echoing_supplied_values(label):
    with pytest.raises(ValueError, match="Choose a starting action from the list") as error:
        starter_command(label)
    assert "secret" not in str(error.value)


@pytest.mark.parametrize("template", PROFILE_TEMPLATES)
def test_each_profile_template_uses_fresh_conservative_settings(template):
    profile = new_profile(template)
    defaults = Profile.default()
    assert profile.audience == defaults.audience == "moderator"
    for field in ("global_cooldown_ms", "viewer_cooldown_ms", "max_actions_per_minute",
                  "queue_size", "queue_ttl_ms", "allowed_user_ids", "blocked_user_ids"):
        assert getattr(profile, field) == getattr(defaults, field)
    assert Profile.from_dict(profile.to_dict()) == profile
    changed = replace(profile, audience="everyone")
    assert changed.audience == "everyone"
    assert new_profile(template).audience == "moderator"


def test_mixed_template_contains_usable_keyboard_and_mouse_starters_without_session_data():
    profile = new_profile("Keyboard & mouse")
    assert profile.name == "Keyboard & mouse" and profile.schema_version == 2
    assert profile.commands[:5] == Profile.default().commands
    assert {command.trigger for command in profile.commands} == {
        "!forward", "!back", "!left", "!right", "!jump",
        "!lookleft", "!lookright", "!lookup", "!lookdown",
        "!primary", "!secondary", "!wheelup", "!wheeldown",
    }
    assert not ({"client_id", "access_token", "refresh_token", "target", "armed"} & profile.to_dict().keys())
