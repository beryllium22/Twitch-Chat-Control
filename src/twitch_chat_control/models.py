"""Strict, credential-free profiles for bounded, single-key game actions."""

from __future__ import annotations

import re
import string
from dataclasses import dataclass, field
from typing import Any, Mapping

SAFE_KEYS = frozenset(string.ascii_lowercase + string.digits) | frozenset(
    {"left", "right", "up", "down", "space"}
)
AUDIENCES = ("everyone", "subscriber", "vip", "moderator", "broadcaster")
MAX_PROFILE_BYTES = 65_536
MAX_COMMANDS = 64
MAX_USER_IDS = 512
_TRIGGER = re.compile(r"![a-z][a-z0-9_-]{0,23}\Z", re.ASCII)
_USER_ID = re.compile(r"[1-9][0-9]{0,31}\Z", re.ASCII)


class ProfileError(ValueError):
    """A profile is invalid; the error never embeds configuration values."""


def _integer(value: Any, low: int, high: int, field_name: str) -> int:
    if type(value) is not int or not low <= value <= high:
        raise ProfileError(f"{field_name} must be an integer from {low} to {high}.")
    return value


def _object(value: Any, allowed: set[str], required: set[str]) -> Mapping[str, Any]:
    if type(value) is not dict:
        raise ProfileError("Expected a JSON object.")
    if not all(type(key) is str for key in value):
        raise ProfileError("Object keys must be strings.")
    if set(value) - allowed:
        raise ProfileError("Unknown profile fields are not allowed.")
    if required - set(value):
        raise ProfileError("Required profile fields are missing.")
    return value


def _ids(value: Any, field_name: str) -> frozenset[str]:
    if type(value) is not list or len(value) > MAX_USER_IDS:
        raise ProfileError(f"{field_name} must be a list of at most {MAX_USER_IDS} user IDs.")
    if any(type(item) is not str or not _USER_ID.fullmatch(item) for item in value):
        raise ProfileError(f"{field_name} must contain numeric Twitch user IDs.")
    if len(set(value)) != len(value):
        raise ProfileError(f"{field_name} contains duplicate user IDs.")
    return frozenset(value)


@dataclass(frozen=True, slots=True)
class Command:
    trigger: str
    key: str
    hold_ms: int = 100
    cooldown_ms: int = 500
    enabled: bool = True

    def __post_init__(self) -> None:
        if type(self.trigger) is not str or not _TRIGGER.fullmatch(self.trigger):
            raise ProfileError("Triggers must be ! followed by 1–24 lowercase ASCII letters, digits, _ or -; start with a letter.")
        if type(self.key) is not str or self.key not in SAFE_KEYS:
            raise ProfileError("Only a–z, 0–9, arrow keys and space are allowed.")
        # Leave scheduling margin before the native backend's independent
        # 500 ms hard-stop deadline.
        _integer(self.hold_ms, 10, 450, "hold_ms")
        _integer(self.cooldown_ms, 0, 60_000, "cooldown_ms")
        if type(self.enabled) is not bool:
            raise ProfileError("enabled must be true or false.")

    @classmethod
    def from_dict(cls, value: Any) -> Command:
        obj = _object(value, {"trigger", "key", "hold_ms", "cooldown_ms", "enabled"}, {"trigger", "key"})
        return cls(**obj)

    def to_dict(self) -> dict[str, Any]:
        return {"trigger": self.trigger, "key": self.key, "hold_ms": self.hold_ms,
                "cooldown_ms": self.cooldown_ms, "enabled": self.enabled}


_DEFAULT_COMMANDS = (
    Command("!forward", "w", 150, 300),
    Command("!back", "s", 150, 300),
    Command("!left", "a", 150, 300),
    Command("!right", "d", 150, 300),
    Command("!jump", "space", 100, 1_000),
)


@dataclass(frozen=True, slots=True)
class Profile:
    name: str = "Starter"
    commands: tuple[Command, ...] = _DEFAULT_COMMANDS
    audience: str = "moderator"
    global_cooldown_ms: int = 250
    viewer_cooldown_ms: int = 1_500
    max_actions_per_minute: int = 90
    queue_size: int = 8
    queue_ttl_ms: int = 500
    allowed_user_ids: frozenset[str] = field(default_factory=frozenset)
    blocked_user_ids: frozenset[str] = field(default_factory=frozenset)
    schema_version: int = 1

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version != 1:
            raise ProfileError("Unsupported profile schema version.")
        if type(self.name) is not str or not 1 <= len(self.name) <= 64 or not self.name.isprintable():
            raise ProfileError("Profile name must contain 1–64 printable characters.")
        if type(self.commands) is not tuple or len(self.commands) > MAX_COMMANDS:
            raise ProfileError(f"A profile may contain at most {MAX_COMMANDS} commands.")
        if any(type(command) is not Command for command in self.commands):
            raise ProfileError("Commands must be validated Command objects.")
        if len({command.trigger for command in self.commands}) != len(self.commands):
            raise ProfileError("Command triggers must be unique.")
        if type(self.audience) is not str or self.audience not in AUDIENCES:
            raise ProfileError("Unknown audience setting.")
        _integer(self.global_cooldown_ms, 100, 60_000, "global_cooldown_ms")
        _integer(self.viewer_cooldown_ms, 250, 60_000, "viewer_cooldown_ms")
        _integer(self.max_actions_per_minute, 1, 300, "max_actions_per_minute")
        _integer(self.queue_size, 1, 8, "queue_size")
        _integer(self.queue_ttl_ms, 100, 750, "queue_ttl_ms")
        for field_name in ("allowed_user_ids", "blocked_user_ids"):
            values = getattr(self, field_name)
            if type(values) is not frozenset or len(values) > MAX_USER_IDS:
                raise ProfileError(f"{field_name} must be an immutable set of at most {MAX_USER_IDS} user IDs.")
            if any(type(item) is not str or not _USER_ID.fullmatch(item) for item in values):
                raise ProfileError(f"{field_name} must contain numeric Twitch user IDs.")

    @classmethod
    def default(cls) -> Profile:
        return cls()

    @classmethod
    def from_dict(cls, value: Any) -> Profile:
        fields = {"schema_version", "name", "commands", "audience", "global_cooldown_ms",
                  "viewer_cooldown_ms", "max_actions_per_minute", "queue_size", "queue_ttl_ms",
                  "allowed_user_ids", "blocked_user_ids"}
        obj = dict(_object(value, fields, {"schema_version", "commands"}))
        if type(obj["commands"]) is not list or len(obj["commands"]) > MAX_COMMANDS:
            raise ProfileError(f"commands must be a list of at most {MAX_COMMANDS} entries.")
        obj["commands"] = tuple(Command.from_dict(item) for item in obj["commands"])
        for field_name in ("allowed_user_ids", "blocked_user_ids"):
            if field_name in obj:
                obj[field_name] = _ids(obj[field_name], field_name)
        return cls(**obj)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "name": self.name,
            "audience": self.audience,
            "global_cooldown_ms": self.global_cooldown_ms,
            "viewer_cooldown_ms": self.viewer_cooldown_ms,
            "max_actions_per_minute": self.max_actions_per_minute,
            "queue_size": self.queue_size,
            "queue_ttl_ms": self.queue_ttl_ms,
            "allowed_user_ids": sorted(self.allowed_user_ids),
            "blocked_user_ids": sorted(self.blocked_user_ids),
            "commands": [command.to_dict() for command in self.commands],
        }


def normalize_command(text: Any) -> str | None:
    """Normalize case only; do not trim, tokenize, interpolate or interpret chat."""
    if type(text) is not str or not 2 <= len(text) <= 25 or not text.isascii():
        return None
    lowered = text.lower()
    return lowered if _TRIGGER.fullmatch(lowered) else None
