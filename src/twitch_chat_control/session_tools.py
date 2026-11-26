"""Pure presentation helpers for sharing controls and explaining safety pauses.

These helpers do not read credentials, windows, clipboard state, or native input.
Only the GUI's explicit copy action may put the returned reference on a clipboard.
"""

from __future__ import annotations

from .models import Profile
from .session_ui import reason_text

MAX_REFERENCE_CHARACTERS = 12_000

_AUDIENCE_TEXT = {
    "everyone": "Everyone in chat",
    "subscriber": "Subscribers, moderators, and the streamer",
    "vip": "VIPs, moderators, and the streamer",
    "moderator": "Moderators and the streamer",
    "broadcaster": "The streamer only",
}


def _duration(milliseconds: int) -> str:
    return f"{milliseconds / 1000:g} s" if milliseconds >= 1000 else f"{milliseconds} ms"


def command_reference(profile: Profile) -> str:
    """Describe enabled, applied controls without including free-form metadata.

    A profile name can contain arbitrary local text, so it is deliberately not
    part of the shareable reference. User-ID lists are described without IDs.
    Validated models bound both the number of lines and every action parameter.
    """
    if type(profile) is not Profile:
        raise TypeError("A validated Profile is required.")
    commands = tuple(command for command in profile.commands if command.enabled)
    if not commands:
        return "Chat controls\nNo commands are enabled. Add or enable a command before sharing."
    lines = [
        "Chat controls",
        f"Who can play: {_AUDIENCE_TEXT[profile.audience]}",
        "Send one exact command per message. Uppercase or lowercase both work.",
        f"Viewer cooldown: {_duration(profile.viewer_cooldown_ms)}; "
        f"shared cooldown: {_duration(profile.global_cooldown_ms)}; "
        f"limit: {profile.max_actions_per_minute} actions per minute.",
    ]
    if profile.allowed_user_ids or profile.blocked_user_ids:
        lines.append("Additional local allow/block restrictions apply.")
    lines.append("")
    for command in commands:
        cooldown = f"command cooldown {_duration(command.cooldown_ms)}" if command.cooldown_ms else "shared limits apply"
        lines.append(f"{command.trigger} — {command.action_summary} ({cooldown})")
    reference = "\n".join(lines)
    if len(reference) > MAX_REFERENCE_CHARACTERS:
        # Fail without copying an incomplete command list if future model
        # changes ever invalidate the current bounded format.
        raise ValueError("The command reference exceeds its display limit.")
    return reference


_PAUSE_HEADLINES = {
    "emergency_stop": "Stopped with F8 or Stop all",
    "target_not_foreground": "Paused because the game lost focus",
    "physical_modifier_held": "Paused while a modifier key is held",
    "mouse_pointer_outside_target": "Paused because the pointer left the game",
    "mouse_move_outside_target": "Paused near the game's edge",
    "physical_mouse_button_held": "Paused while a mouse button is held",
    "mouse_capture_unsafe": "Paused because mouse routing changed",
    "worker_watchdog_delay": "Paused because the safety worker was delayed",
    "interface_unresponsive": "Paused because the interface was delayed",
    "backend_fault_restart_required": "Stopped — restart before live input",
    "input_release_failed_restart_required": "Stopped — an input release was not confirmed",
    "input_failed_restart_required": "Stopped — restart before live input",
    "policy_worker_failed": "Stopped — restart the app",
    "emergency_stop_unavailable": "Stopped — the F8 hotkey is unavailable",
    "the_selected_game_lost_focus_or_changed_identity_": "Paused because the selected game changed",
    "a_modifier_key_was_pressed__live_input_was_paused_": "Paused while a modifier key is held",
    "the_independent_key_release_watchdog_paused_live_input_": "Paused by the key-release safeguard",
    "the_independent_mouse_release_watchdog_paused_live_input_": "Paused by the mouse-release safeguard",
    "f8_is_unavailable__close_the_app_using_it__then_restart_this_app": "Stopped — the F8 hotkey is unavailable",
    "the_input_safety_monitor_stopped__live_control_is_disabled_": "Stopped — restart the app",
}


def paused_guidance(reason: str) -> tuple[str, str] | None:
    """Return a fixed safety-stop explanation, without granting input authority.

    Normal edits, local pauses, and setup/connection prerequisites are handled
    by the existing readiness view. In particular, an old connection-lost
    reason must not tell an already reconnected/offline-preview user to log in.
    """
    if type(reason) is not str or len(reason) > 64:
        return None
    headline = _PAUSE_HEADLINES.get(reason)
    return (headline, reason_text(reason)) if headline is not None else None
