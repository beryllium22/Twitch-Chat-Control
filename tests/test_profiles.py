from dataclasses import FrozenInstanceError, replace

import pytest

from twitch_chat_control.models import SAFE_KEYS, Command, Profile, ProfileError, normalize_command


def test_default_is_restricted_and_credential_free():
    profile = Profile.default()
    assert profile.audience == "moderator"
    assert {item.trigger for item in profile.commands} == {"!forward", "!back", "!left", "!right", "!jump"}
    assert Profile.from_dict(profile.to_dict()) == profile
    assert not ({"token", "client_secret", "access_token", "armed", "target", "client_id"} & profile.to_dict().keys())


@pytest.mark.parametrize("key", ["enter", "return", "tab", "escape", "alt", "ctrl", "shift", "win", "f4", "f8", "delete", "backspace", "grave", "mouse1", "w+a", "W", "__import__"])
def test_unsafe_or_ambiguous_keys_are_rejected(key):
    with pytest.raises(ProfileError):
        Command("!move", key)


@pytest.mark.parametrize("key", sorted(SAFE_KEYS))
def test_only_documented_game_keys_are_supported(key):
    assert Command("!move", key).key == key


@pytest.mark.parametrize("text", ["!jump ", " !jump", "!jump\n", "!jump\r", "!jump\x00", "!jump;rm", "!jump !left", "!jump 2000", "！jump", "!jümp", "!jump\u200b", "!jump\t", "!", "jump", "!1", "!" + "a" * 25, 123, None])
def test_chat_parser_requires_exact_ascii_token(text):
    assert normalize_command(text) is None


def test_chat_case_is_normalized_without_interpretation():
    assert normalize_command("!JuMp") == "!jump"
    assert normalize_command("!move_left") == "!move_left"


@pytest.mark.parametrize("field,value", [
    ("hold_ms", 0), ("hold_ms", 451), ("hold_ms", True), ("hold_ms", 100.0),
    ("cooldown_ms", -1), ("cooldown_ms", 60_001), ("enabled", 1),
])
def test_command_limits_reject_wrong_types_and_out_of_bounds(field, value):
    with pytest.raises(ProfileError):
        Command.from_dict({"trigger": "!jump", "key": "space", field: value})


@pytest.mark.parametrize("field,value", [
    ("schema_version", 2), ("schema_version", True),
    ("global_cooldown_ms", 99), ("global_cooldown_ms", "100"),
    ("viewer_cooldown_ms", 249), ("viewer_cooldown_ms", False),
    ("max_actions_per_minute", 0), ("max_actions_per_minute", 301),
    ("queue_size", 0), ("queue_size", 9),
    ("queue_ttl_ms", 99), ("queue_ttl_ms", 751),
    ("audience", "admin"), ("name", "\nunsafe"), ("name", "a" * 65),
    ("allowed_user_ids", ["login_name"]), ("allowed_user_ids", ["1", "1"]),
    ("blocked_user_ids", [1]), ("blocked_user_ids", ["0"]),
])
def test_profile_schema_is_strict(field, value):
    document = Profile.default().to_dict()
    document[field] = value
    with pytest.raises(ProfileError):
        Profile.from_dict(document)


@pytest.mark.parametrize("field", ["client_secret", "access_token", "refresh_token", "shell", "script", "armed", "target", "extra"])
def test_unknown_fields_cannot_hide_credentials_or_actions(field):
    document = Profile.default().to_dict()
    document[field] = "DO_NOT_EXPOSE_THIS_VALUE"
    with pytest.raises(ProfileError) as raised:
        Profile.from_dict(document)
    assert "DO_NOT_EXPOSE_THIS_VALUE" not in str(raised.value)


def test_profile_rejects_duplicate_and_excessive_commands():
    command = Command("!jump", "space")
    with pytest.raises(ProfileError):
        Profile(commands=(command, command))
    document = Profile.default().to_dict()
    document["commands"] = [{"trigger": "!x", "key": "a"}] * 65
    with pytest.raises(ProfileError):
        Profile.from_dict(document)


def test_direct_dataclass_construction_has_same_validation_and_is_immutable():
    with pytest.raises(ProfileError):
        Profile(global_cooldown_ms=0)
    with pytest.raises(ProfileError):
        Profile(commands=[Command("!x", "a")])
    with pytest.raises(ProfileError):
        Profile(allowed_user_ids={"123"})
    with pytest.raises(ProfileError):
        replace(Command("!jump", "space"), key="enter")
    with pytest.raises(FrozenInstanceError):
        Profile.default().audience = "everyone"


def test_empty_profile_is_safe_and_custom_access_lists_round_trip():
    profile = Profile(commands=(), allowed_user_ids=frozenset({"123", "456"}), blocked_user_ids=frozenset({"456"}))
    assert Profile.from_dict(profile.to_dict()) == profile
