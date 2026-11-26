"""Small, editable starting profiles with conservative participation defaults."""

from dataclasses import replace
from types import MappingProxyType

from .models import Command, Profile

PROFILE_TEMPLATES = ("Blank", "WASD movement", "Arrow-key movement", "Keyboard & mouse")

# These are starting bindings, not verified controls for a particular game.
# Immutable values can be shared safely: every editor change uses a new Command.
ACTION_STARTERS = MappingProxyType({
    "Jump · Space": Command("!jump", "space", 100, 1_000),
    "Forward · W": Command("!forward", "w", 150, 500),
    "Look left": Command("!lookleft", action_type="mouse_move", dx=-25),
    "Look right": Command("!lookright", action_type="mouse_move", dx=25),
    "Look up": Command("!lookup", action_type="mouse_move", dy=-25),
    "Look down": Command("!lookdown", action_type="mouse_move", dy=25),
    "Left mouse button": Command("!primary", action_type="mouse_button", button="left", cooldown_ms=1_000),
    "Right mouse button": Command("!secondary", action_type="mouse_button", button="right", cooldown_ms=1_000),
    "Wheel up": Command("!wheelup", action_type="mouse_wheel", wheel=1),
    "Wheel down": Command("!wheeldown", action_type="mouse_wheel", wheel=-1),
})


def starter_command(label: str) -> Command:
    """Return a validated, immutable starting action for a local editor draft."""
    if type(label) is not str or label not in ACTION_STARTERS:
        raise ValueError("Choose a starting action from the list.")
    return ACTION_STARTERS[label]


def new_profile(template: str) -> Profile:
    """Create a new profile; never inherit authority, windows or authorization."""
    if template == "Blank":
        return replace(Profile.default(), name="My game", commands=())
    if template == "WASD movement":
        return replace(Profile.default(), name="WASD movement")
    if template == "Arrow-key movement":
        return replace(
            Profile.default(),
            name="Arrow-key movement",
            commands=(
                Command("!up", "up", 150, 300),
                Command("!down", "down", 150, 300),
                Command("!left", "left", 150, 300),
                Command("!right", "right", 150, 300),
                Command("!jump", "space", 100, 1000),
            ),
        )
    if template == "Keyboard & mouse":
        base = Profile.default()
        return replace(
            base,
            name="Keyboard & mouse",
            commands=base.commands + tuple(command for command in ACTION_STARTERS.values() if command.is_mouse),
        )
    raise ValueError("Choose a profile template from the list.")


class PreviewBackend:
    """An isolated policy-preview backend with no route to operating-system input."""

    target_selected = False
    safety_ready = False

    def bind_live_session(self, enabled: bool) -> None:
        if enabled:
            raise RuntimeError("Live input is unavailable in a profile preview.")

    def release_all(self) -> bool:
        return True

    def is_target_foreground(self) -> bool:
        return False

    def physical_modifiers_down(self) -> bool:
        return True

    def key_down(self, key: str) -> None:
        raise RuntimeError("Profile previews cannot send input.")

    def key_up(self, key: str) -> None:
        raise RuntimeError("Profile previews cannot send input.")

    def mouse_button_down(self, button: str) -> None:
        raise RuntimeError("Profile previews cannot send input.")

    def mouse_button_up(self, button: str) -> None:
        raise RuntimeError("Profile previews cannot send input.")

    def mouse_move(self, dx: int, dy: int) -> None:
        raise RuntimeError("Profile previews cannot send input.")

    def mouse_wheel(self, steps: int) -> None:
        raise RuntimeError("Profile previews cannot send input.")
