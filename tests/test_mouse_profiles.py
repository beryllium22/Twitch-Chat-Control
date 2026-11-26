"""Strict mixed-input profiles, including legacy and credential boundaries."""

from dataclasses import FrozenInstanceError, replace

import pytest

from twitch_chat_control.models import (
    MAX_MOUSE_MOVE,
    MAX_MOUSE_WHEEL,
    MOUSE_BUTTONS,
    Command,
    Profile,
    ProfileError,
)
from twitch_chat_control.profile_io import load_profile, save_profile

MOUSE_COMMANDS = (
    Command("!fire", action_type="mouse_button", button="left", hold_ms=50),
    Command("!look", action_type="mouse_move", dx=25, dy=-10),
    Command("!scroll", action_type="mouse_wheel", wheel=-1),
)


def test_legacy_key_profile_remains_byte_shape_compatible():
    profile = Profile.default()
    document = profile.to_dict()
    assert document["schema_version"] == 1
    assert set(document["commands"][0]) == {"trigger", "key", "hold_ms", "cooldown_ms", "enabled"}
    assert Profile.from_dict(document) == profile


def test_first_mouse_edit_promotes_profile_and_mixed_profile_round_trips(tmp_path):
    profile = replace(Profile.default(), commands=Profile.default().commands + MOUSE_COMMANDS)
    assert profile.schema_version == 2
    document = profile.to_dict()
    assert Profile.from_dict(document) == profile
    path = tmp_path / "mixed.json"
    save_profile(profile, path)
    assert load_profile(path) == profile
    assert replace(profile, commands=Profile.default().commands).schema_version == 2
    assert not ({"client_id", "client_secret", "access_token", "refresh_token", "target", "armed"} & document.keys())


@pytest.mark.parametrize("command", MOUSE_COMMANDS)
def test_legacy_schema_cannot_smuggle_mouse_action(command):
    document = {"schema_version": 1, "commands": [command.to_dict()]}
    with pytest.raises(ProfileError):
        Profile.from_dict(document)


@pytest.mark.parametrize("field,value", [("action_type", "key"), ("button", ""), ("dx", 0), ("dy", 0), ("wheel", 0)])
def test_legacy_schema_rejects_even_default_new_fields(field, value):
    command = Command("!jump", "space").to_dict()
    command[field] = value
    with pytest.raises(ProfileError):
        Profile.from_dict({"schema_version": 1, "commands": [command]})


@pytest.mark.parametrize("button", MOUSE_BUTTONS)
@pytest.mark.parametrize("hold", [10, 450])
def test_bounded_mouse_buttons_round_trip(button, hold):
    command = Command("!fire", action_type="mouse_button", button=button, hold_ms=hold)
    assert command.is_mouse and command.is_held
    assert Command.from_dict(command.to_dict()) == command
    assert str(hold) in command.action_summary


@pytest.mark.parametrize("dx,dy", [(-100, 100), (100, -100), (0, 1), (-1, 0)])
def test_bounded_relative_movement_round_trips(dx, dy):
    command = Command("!look", action_type="mouse_move", dx=dx, dy=dy)
    assert command.is_mouse and not command.is_held
    assert Command.from_dict(command.to_dict()) == command
    assert "input units" in command.action_summary
    assert not ({"key", "button", "hold_ms", "wheel"} & command.to_dict().keys())


@pytest.mark.parametrize("steps", [-MAX_MOUSE_WHEEL, -1, 1, MAX_MOUSE_WHEEL])
def test_wheel_notches_round_trip(steps):
    command = Command("!scroll", action_type="mouse_wheel", wheel=steps)
    assert command.is_mouse and not command.is_held
    assert ("up" if steps > 0 else "down") in command.action_label
    assert "notch" in command.action_summary
    assert Command.from_dict(command.to_dict()) == command


@pytest.mark.parametrize("action,fields", [
    ("mouse_button", {"button": "left", "key": "w"}),
    ("mouse_button", {"button": "left", "dx": 1}),
    ("mouse_button", {"button": "left", "wheel": 1}),
    ("mouse_button", {"button": "left", "hold_ms": 451}),
    ("mouse_button", {"button": "LEFT"}),
    ("mouse_button", {"button": "left+right"}),
    ("mouse_button", {"button": "x3"}),
    ("mouse_button", {"button": True}),
    ("mouse_move", {"dx": 0, "dy": 0}),
    ("mouse_move", {"dx": MAX_MOUSE_MOVE + 1}),
    ("mouse_move", {"dy": -MAX_MOUSE_MOVE - 1}),
    ("mouse_move", {"dx": True}),
    ("mouse_move", {"dx": 1.0}),
    ("mouse_move", {"dx": "10"}),
    ("mouse_move", {"dx": 1, "dy": False}),
    ("mouse_move", {"dx": 1, "button": "left"}),
    ("mouse_move", {"dx": 1, "hold_ms": 101}),
    ("mouse_move", {"dx": 1, "wheel": 1}),
    ("mouse_wheel", {"wheel": 0}),
    ("mouse_wheel", {"wheel": MAX_MOUSE_WHEEL + 1}),
    ("mouse_wheel", {"wheel": -MAX_MOUSE_WHEEL - 1}),
    ("mouse_wheel", {"wheel": True}),
    ("mouse_wheel", {"wheel": 1.0}),
    ("mouse_wheel", {"wheel": "1"}),
    ("mouse_wheel", {"wheel": 1, "dx": 1}),
    ("mouse_wheel", {"wheel": 1, "button": "middle"}),
    ("mouse_wheel", {"wheel": 1, "hold_ms": 200}),
    ("key", {"key": "w", "wheel": 1}),
    ("key", {"key": "w", "button": "left"}),
    ("key", {"key": "w", "dx": False}),
    ("shell", {"key": "w"}),
    (None, {"key": "w"}),
])
def test_native_action_model_rejects_ambiguous_and_out_of_bound_values(action, fields):
    with pytest.raises(ProfileError):
        Command("!action", action_type=action, **fields)


@pytest.mark.parametrize("command,field,value", [
    (MOUSE_COMMANDS[0], "dx", 0), (MOUSE_COMMANDS[0], "key", ""),
    (MOUSE_COMMANDS[1], "hold_ms", 100), (MOUSE_COMMANDS[1], "wheel", 0),
    (MOUSE_COMMANDS[2], "button", ""), (MOUSE_COMMANDS[2], "dy", 0),
    (Command("!jump", "space"), "button", ""),
])
def test_json_action_union_rejects_even_default_irrelevant_fields(command, field, value):
    document = command.to_dict()
    document[field] = value
    with pytest.raises(ProfileError):
        Command.from_dict(document)


@pytest.mark.parametrize("field", ["client_secret", "access_token", "absolute", "x", "y", "script", "sequence", "drag", "repeat"])
@pytest.mark.parametrize("command", MOUSE_COMMANDS)
def test_mouse_action_does_not_expand_credential_or_arbitrary_action_surface(field, command):
    document = command.to_dict()
    document[field] = "UNTRUSTED_VALUE_MUST_NOT_BE_ECHOED"
    with pytest.raises(ProfileError) as error:
        Command.from_dict(document)
    assert "UNTRUSTED_VALUE" not in str(error.value)


def test_mouse_actions_are_immutable_and_bool_schema_cannot_be_promoted():
    with pytest.raises(FrozenInstanceError):
        MOUSE_COMMANDS[0].button = "right"
    with pytest.raises(ProfileError):
        Profile(schema_version=True, commands=MOUSE_COMMANDS)
