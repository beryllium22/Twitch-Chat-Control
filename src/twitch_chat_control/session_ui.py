"""Plain-language session guidance; input authority stays in the safety engine."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Readiness:
    ready: bool
    headline: str
    detail: str
    action: str | None


def session_readiness(
    *,
    mode: str,
    state: str,
    connected: bool,
    available: bool,
    safety_ready: bool,
    target_selected: bool,
    faulted: bool,
    worker_stopped: bool,
    enabled_commands: int,
    command_draft: bool,
    safety_draft: bool,
    acknowledged: bool,
) -> Readiness:
    """Describe the next local step without touching Tk, credentials, or input.

    This is guidance, not an authorization decision. Starting still validates
    drafts and passes through the existing engine. Foreground and physical-key
    checks belong to the end of the live countdown, not this GUI readiness view.
    """
    if worker_stopped:
        return Readiness(False, "Restart the app to continue",
                         "The safety worker stopped. Close and reopen the app before starting a session.",
                         "restart")
    if mode not in {"preview", "live"}:
        return Readiness(False, "Choose how to try your commands",
                         "Select Preview for a test without game input, or Live input for your game.", None)
    if mode == "live" and faulted:
        return Readiness(False, "Restart before using live input",
                         "A native input safety check failed. Close and reopen the app. Preview remains available.",
                         "restart")
    if state == "pending":
        return Readiness(False, "Switch to your game now",
                         "The countdown is running. Input starts only if the selected game is ready and focused.",
                         None)
    if state == "armed":
        if mode == "preview":
            return Readiness(False, "Preview is running",
                             "Try a command on Session. This session sends no keyboard or mouse input. Pause when finished.",
                             None)
        return Readiness(False, "Chat control is active",
                         "Keep your game focused. Use Pause or F8 before opening game chat, menus, or overlays.",
                         None)
    if state != "disarmed":
        return Readiness(False, "Restart the app to continue",
                         "The session state could not be confirmed. Close and reopen the app before starting.",
                         "restart")
    if enabled_commands <= 0 and not command_draft:
        return Readiness(False, "Add or enable a command",
                         "Choose at least one chat command and game action in Controls & commands before starting.",
                         "commands")
    if mode == "live":
        if not available:
            return Readiness(False, "Live input needs Windows",
                             "Use the Windows app to control a game. You can still build profiles and try Preview here.",
                             None)
        if not safety_ready:
            return Readiness(False, "The emergency stop is not ready",
                             "Free F8 in other apps and restart without administrator rights. Preview remains available.",
                             "restart")
        if not connected:
            return Readiness(False, "Connect your Twitch channel",
                             "Enter your channel and Public Client ID, then connect with your own Twitch account.",
                             "connect")
        if not target_selected:
            return Readiness(False, "Choose your game window",
                             "Open your game, refresh the window list, and select the exact window you want chat to control.",
                             "game")
        if not acknowledged:
            return Readiness(False, "Review when to pause",
                             "Confirm that you will pause before opening game chat, consoles, overlays, or sensitive menus.",
                             "ack")
    if command_draft or safety_draft:
        if command_draft and safety_draft:
            detail = "Your pending command and safety edits will be checked and applied before starting."
        elif command_draft:
            detail = "Your pending command edits will be checked and applied before starting."
        else:
            detail = "Your pending safety edits will be checked and applied before starting."
        return Readiness(True, "Ready to check your changes", detail,
                         "commands" if command_draft else "safety")
    if mode == "preview":
        return Readiness(True, "Try your commands safely",
                         "Start Preview, then try a command on Session. No Twitch account or game is needed; no game input is sent.",
                         None)
    return Readiness(True, "Ready for your game",
                     "Start live input, then switch to your selected game within 3 seconds. F8 stops control at any time.",
                     None)


_REASONS = {
    "start_disarmed": "Ready to set up. No game input is active.",
    "local_pause": "Controls paused locally.",
    "stopped_locally": "Controls paused locally.",
    "emergency_stop": "Emergency stop: queued actions cleared. Start again locally when you are ready.",
    "connection_lost": "Twitch disconnected. Reconnect before starting live control again.",
    "connected_controls_remain_disarmed": "Twitch connected. Controls stay paused until you start them locally.",
    "not_connected": "Connect your Twitch channel before starting live input.",
    "backend_fault_restart_required": "A native safety check failed. Restart the app before using live input.",
    "input_release_failed_restart_required": "A key or mouse button release could not be confirmed. Stop live use and restart the app.",
    "input_failed_restart_required": "Game input failed. Restart the app before using live input again.",
    "emergency_stop_unavailable": "F8 is unavailable. Free the hotkey and restart the app before using live input.",
    "target_not_selected": "Choose a game window before starting live input.",
    "target_not_foreground": "The selected game lost focus. Return to it and start control again locally.",
    "physical_modifier_held": "A modifier key is held. Release it, then start control again locally.",
    "rearming": "Preparing a new session; previous queued actions have been cleared.",
    "preview_armed": "Preview started. Commands are simulated without sending keyboard or mouse input.",
    "focus_game_countdown": "Switch to your selected game before the countdown ends.",
    "live_armed": "Live chat control started. F8 stops control.",
    "invalid_message": "The chat event could not be verified and was ignored.",
    "invalid_message_id": "The chat event could not be verified and was ignored.",
    "invalid_identity": "The viewer identity could not be verified and the command was ignored.",
    "invalid_roles": "The viewer role could not be verified and the command was ignored.",
    "invalid_timestamp": "The chat event time could not be verified and the command was ignored.",
    "not_an_exact_command": "Use one exact configured command, without spaces or extra arguments.",
    "unknown_or_disabled_command": "This command is not configured or is disabled. Check Controls & commands.",
    "not_armed": "Controls are paused. Start Preview or live input locally before trying commands.",
    "stale_message": "This command arrived too late and was ignored.",
    "duplicate_message": "This chat event was already handled and was ignored.",
    "viewer_blocked": "This viewer is on the profile's block list.",
    "viewer_not_allowed": "This viewer is not on the profile's allowed-user list.",
    "audience_restricted": "This viewer's role is not included in the selected audience.",
    "global_cooldown": "Commands are arriving too quickly. Wait for the shared cooldown.",
    "viewer_cooldown": "This viewer needs to wait before sending another command.",
    "command_cooldown": "This command needs to wait before it can run again.",
    "minute_budget": "The action limit for the last minute has been reached. Wait before trying again.",
    "queue_full": "The command queue is full. This command was not added.",
    "preview_queued": "Command accepted for Preview. No keyboard or mouse input will be sent.",
    "live_queued": "Command accepted and queued for the selected game.",
    "worker_watchdog_delay": "Controls paused because the safety worker was delayed. Review the session before restarting.",
    "tap_finished": "The game key was released.",
    "queue_expired": "A queued command became too old and was dropped.",
    "would_tap": "Preview checked the mapped key press. No keyboard or mouse input was sent.",
    "would_press_mouse_button": "Preview checked the mapped mouse button press. No keyboard or mouse input was sent.",
    "would_move_mouse": "Preview checked the mapped mouse movement. No keyboard or mouse input was sent.",
    "would_scroll_mouse": "Preview checked the mapped mouse wheel scroll. No keyboard or mouse input was sent.",
    "key_down": "The mapped game key was pressed.",
    "mouse_button_down": "The mapped mouse button was pressed over the selected game.",
    "mouse_button_released": "The mouse button was released.",
    "mouse_moved": "The mapped relative mouse movement was sent.",
    "mouse_scrolled": "The mapped mouse wheel scroll was sent.",
    "mouse_pointer_outside_target": "Mouse control paused. Put the pointer over the selected game's play area, then start again locally.",
    "mouse_move_outside_target": "Mouse control paused near the game edge. Move the pointer inward or reduce the movement, then start again locally.",
    "physical_mouse_button_held": "Mouse control paused because a mouse button is held. Release it, then start again locally.",
    "mouse_capture_unsafe": "Mouse control paused because a menu or another window may receive it. Close menus and return to gameplay before starting again.",
    "profile_changed": "Profile updated. Controls remain paused.",
    "interface_unresponsive": "Controls paused because the interface stopped responding.",
    "policy_worker_failed": "The safety worker failed. Restart the app before continuing.",
    "authorization_prompt": "Controls paused while you authorize Twitch.",
    "window_list_refreshed": "Game windows refreshed. Choose a window before using live input.",
    "target_changed": "Game window changed. Controls remain paused.",
    "mode_changed": "Control mode changed. Start the next session locally when ready.",
    "acknowledgment_cleared": "Live controls paused until the game-focus reminder is confirmed.",
    "profile_editing": "Controls paused while you edit profile settings.",
    "command_editing": "Controls paused while you edit a command.",
    "profile_apply": "Safety settings applied. Controls remain paused.",
    "commands_changed": "Command mappings updated. Controls remain paused.",
    "invalid_command_draft": "Fix or discard the command draft before starting a session.",
    "command_preview": "Checking the selected mapping without sending keyboard or mouse input.",
    "preview_complete": "Mapping preview finished. No keyboard or mouse input was sent.",
    "profile_navigation": "Controls paused while you manage profiles.",
    "new_profile": "Creating a new profile. Controls remain paused.",
    "profile_open": "Opening a profile. Controls remain paused.",
    "profile_save": "Saving the profile. Controls remain paused.",
    "shutdown_requested": "Controls stopped before closing the app.",
    "the_selected_game_lost_focus_or_changed_identity_": "The selected game lost focus or changed identity. Review the game selection and start again locally.",
    "a_modifier_key_was_pressed__live_input_was_paused_": "A modifier key was pressed. Release it, then start control again locally.",
    "the_independent_key_release_watchdog_paused_live_input_": "The key-release safeguard paused live input. Review the session before starting again.",
    "the_independent_mouse_release_watchdog_paused_live_input_": "The mouse-button release safeguard paused live input. Review the session before starting again.",
    "f8_is_unavailable__close_the_app_using_it__then_restart_this_app": "F8 is unavailable. Close the app using it, then restart Twitch Chat Control.",
    "the_input_safety_monitor_stopped__live_control_is_disabled_": "The input safety monitor stopped. Restart the app before using live input.",
}
_UNKNOWN_REASON = "No further details are available for this event."


def reason_text(reason: str) -> str:
    """Translate approved internal codes without echoing untrusted values."""
    if type(reason) is not str or len(reason) > 64:
        return _UNKNOWN_REASON
    return _REASONS.get(reason, _UNKNOWN_REASON)
