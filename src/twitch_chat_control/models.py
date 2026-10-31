"""Strict, credential-free profiles for bounded keyboard and mouse actions."""

from __future__ import annotations

import re
import string
from dataclasses import dataclass, field
from typing import Any, Mapping

SAFE_KEYS = frozenset(string.ascii_lowercase + string.digits) | frozenset(
    {"left", "right", "up", "down", "space"}
)
ACTION_TYPES = ("key", "mouse_button", "mouse_move", "mouse_wheel")
MOUSE_BUTTONS = ("left", "right", "middle", "x1", "x2")
MAX_MOUSE_MOVE = 100
MAX_MOUSE_WHEEL = 3
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
    key: str = ""
    hold_ms: int = 100
    cooldown_ms: int = 500
    enabled: bool = True
    action_type: str = "key"
    button: str = ""
    dx: int = 0
    dy: int = 0
    wheel: int = 0

    def __post_init__(self) -> None:
        if type(self.trigger) is not str or not _TRIGGER.fullmatch(self.trigger):
            raise ProfileError("Triggers must be ! followed by 1–24 lowercase ASCII letters, digits, _ or -; start with a letter.")
        if type(self.action_type) is not str or self.action_type not in ACTION_TYPES:
            raise ProfileError("Choose a supported keyboard or mouse action_type.")
        if type(self.key) is not str or type(self.button) is not str:
            raise ProfileError("key and button must be supported control names.")
        # Leave scheduling margin before the native backend's independent
        # 500 ms hard-stop deadline.
        _integer(self.hold_ms, 10, 450, "hold_ms")
        _integer(self.cooldown_ms, 0, 60_000, "cooldown_ms")
        if type(self.enabled) is not bool:
            raise ProfileError("enabled must be true or false.")
        _integer(self.dx, -MAX_MOUSE_MOVE, MAX_MOUSE_MOVE, "dx")
        _integer(self.dy, -MAX_MOUSE_MOVE, MAX_MOUSE_MOVE, "dy")
        _integer(self.wheel, -MAX_MOUSE_WHEEL, MAX_MOUSE_WHEEL, "wheel")
        if self.action_type == "key":
            if self.key not in SAFE_KEYS:
                raise ProfileError("Only a–z, 0–9, arrow keys and space are allowed.")
            if self.button or self.dx or self.dy or self.wheel:
                raise ProfileError("Keyboard actions cannot include mouse settings.")
        else:
            if self.key:
                raise ProfileError("Mouse actions cannot include a keyboard key.")
            if self.action_type == "mouse_button":
                if self.button not in MOUSE_BUTTONS:
                    raise ProfileError("Choose left, right, middle, x1 or x2 for button.")
                if self.dx or self.dy or self.wheel:
                    raise ProfileError("Mouse button actions cannot move or scroll.")
            else:
                if self.button or self.hold_ms != 100:
                    raise ProfileError("Movement and wheel actions cannot hold a button or key.")
                if self.action_type == "mouse_move":
                    if not (self.dx or self.dy) or self.wheel:
                        raise ProfileError("Set a nonzero dx or dy for movement, without wheel scrolling.")
                elif not self.wheel or self.dx or self.dy:
                    raise ProfileError("Set a nonzero wheel value without pointer movement.")

    @property
    def is_mouse(self) -> bool:
        return self.action_type != "key"

    @property
    def is_held(self) -> bool:
        return self.action_type in {"key", "mouse_button"}

    @property
    def action_label(self) -> str:
        if self.action_type == "key":
            return {"space": "Space", "left": "Arrow Left", "right": "Arrow Right",
                    "up": "Arrow Up", "down": "Arrow Down"}.get(self.key, self.key.upper())
        if self.action_type == "mouse_button":
            return {"left": "Left mouse", "right": "Right mouse", "middle": "Middle mouse",
                    "x1": "Side button 1", "x2": "Side button 2"}[self.button]
        if self.action_type == "mouse_move":
            parts = []
            if self.dx:
                parts.append(f"{'right' if self.dx > 0 else 'left'} {abs(self.dx)}")
            if self.dy:
                parts.append(f"{'down' if self.dy > 0 else 'up'} {abs(self.dy)}")
            return "Move " + " / ".join(parts)
        return f"Wheel {'up' if self.wheel > 0 else 'down'} {abs(self.wheel)}"

    @property
    def action_summary(self) -> str:
        if self.is_held:
            return f"{self.action_label} for {self.hold_ms} ms"
        if self.action_type == "mouse_move":
            return f"{self.action_label} input units"
        return f"{self.action_label} {'notch' if abs(self.wheel) == 1 else 'notches'}"

    @classmethod
    def from_dict(cls, value: Any, *, schema_version: int = 2) -> Command:
        if type(schema_version) is not int or schema_version not in {1, 2}:
            raise ProfileError("Unsupported profile schema version.")
        common = {"trigger", "cooldown_ms", "enabled"}
        if schema_version == 1:
            obj = _object(value, common | {"key", "hold_ms"}, {"trigger", "key"})
            return cls(**obj)
        obj = _object(value, common | {"action_type", "key", "hold_ms", "button", "dx", "dy", "wheel"}, {"trigger"})
        kind = obj.get("action_type", "key")
        if type(kind) is not str or kind not in ACTION_TYPES:
            raise ProfileError("Choose a supported keyboard or mouse action_type.")
        allowed = {
            "key": {"key", "hold_ms"},
            "mouse_button": {"button", "hold_ms"},
            "mouse_move": {"dx", "dy"},
            "mouse_wheel": {"wheel"},
        }[kind]
        required = {"key"} if kind == "key" else {"action_type"}
        if kind == "mouse_button":
            required.add("button")
        elif kind == "mouse_wheel":
            required.add("wheel")
        obj = _object(obj, common | allowed | {"action_type"}, {"trigger"} | required)
        return cls(**obj)

    def to_dict(self) -> dict[str, Any]:
        if self.action_type == "key":
            # Keep existing keyboard-only profile files compatible with v0.3.
            return {"trigger": self.trigger, "key": self.key, "hold_ms": self.hold_ms,
                    "cooldown_ms": self.cooldown_ms, "enabled": self.enabled}
        result = {"trigger": self.trigger, "action_type": self.action_type,
                  "cooldown_ms": self.cooldown_ms, "enabled": self.enabled}
        if self.action_type == "mouse_button":
            result.update(button=self.button, hold_ms=self.hold_ms)
        elif self.action_type == "mouse_move":
            result.update(dx=self.dx, dy=self.dy)
        else:
            result["wheel"] = self.wheel
        return result


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
        if type(self.schema_version) is not int or self.schema_version not in {1, 2}:
            raise ProfileError("Unsupported profile schema version.")
        if type(self.name) is not str or not 1 <= len(self.name) <= 64 or not self.name.isprintable():
            raise ProfileError("Profile name must contain 1–64 printable characters.")
        if type(self.commands) is not tuple or len(self.commands) > MAX_COMMANDS:
            raise ProfileError(f"A profile may contain at most {MAX_COMMANDS} commands.")
        if any(type(command) is not Command for command in self.commands):
            raise ProfileError("Commands must be validated Command objects.")
        if any(command.is_mouse for command in self.commands):
            # A local GUI edit can add mouse support to a legacy profile. Wire
            # imports still validate against their declared schema first.
            object.__setattr__(self, "schema_version", 2)
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
        if type(obj["schema_version"]) is not int or obj["schema_version"] not in {1, 2}:
            raise ProfileError("Unsupported profile schema version.")
        if type(obj["commands"]) is not list or len(obj["commands"]) > MAX_COMMANDS:
            raise ProfileError(f"commands must be a list of at most {MAX_COMMANDS} entries.")
        obj["commands"] = tuple(Command.from_dict(item, schema_version=obj["schema_version"]) for item in obj["commands"])
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
