"""Small, editable starting profiles with conservative participation defaults."""

from dataclasses import replace

from .models import Command, Profile

PROFILE_TEMPLATES = ("Blank", "WASD movement", "Arrow-key movement")


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
