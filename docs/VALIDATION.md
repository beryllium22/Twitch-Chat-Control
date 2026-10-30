# Validation and first live acceptance

This project is a **development preview**. Automated tests and a successful Windows build do not establish game compatibility, anti-cheat compatibility, or perfect desktop isolation. No test uses real Twitch credentials or injects keys into a user's desktop.

## Automated coverage

- Strict profile parsing, unknown and duplicate fields, unsafe keys, bounded durations and IDs.
- Deterministic command policy with fake clocks: permissions, cooldowns, floods, stale/replayed messages, queue expiry, focus loss, modifier keys, lost connection, emergency stop and release failure.
- Native Win32 boundary with a fake API: input structure ABI, process identity/privilege, window handle reuse, foreground and desktop checks, hotkey failure, existing physical keys, independent hold deadline and release retry.
- Twitch transport with synthetic HTTP/WebSocket responses: device grant, minimal scope, account ownership, refresh rotation, cancellation, fixed TLS endpoints, redirects, revocation, session identity, Shared Chat, stale timestamps and replay filtering.
- Source/distribution checks for credential-shaped literals, unsafe bundled files and changes to the explicit packaging boundary. This check is deliberately scoped; it is not proof that every possible secret format can be recognized.
- Windows CI: Python tests, Tk GUI construction and frozen executable startup in `--smoke-test`. This flag never connects to Twitch or injects keys.

## Streamer acceptance checklist

Use a test game/save, no stream capture of the authorization dialog, and a moderator-only profile initially.

1. Start the app normally, without administrator rights. It must say **DISARMED · PREVIEW**.
2. Arm Preview and try `!jump` as moderator. Activity should show a preview action and no actual keyboard input. Try it as viewer; the starter profile must reject it.
3. Try extra arguments and an unlisted command. Both must be rejected. Check that repeated commands hit cooldowns.
4. Connect with your own Public Client ID and broadcaster account. Cancel once and confirm the app is disarmed. Reconnect and authorize successfully.
5. Select your game, choose live input and acknowledge the focus limitation. Arm and focus the game within three seconds. Send one command from an allowed account. Confirm only the expected action occurs.
6. Press F8 while a longer permitted tap is active. The key should release, the queue should clear, and further chat must do nothing until local arming.
7. Arm again, send a command and switch away from the game. Confirm immediate release/disarming and no automatic resume when you return.
8. Verify holding Ctrl, Alt, Shift or the Windows key prevents or stops chat input. Verify a key already physically held is not taken over by the app.
9. Disconnect Twitch and change/import a profile while idle and while armed. Each must clear the queue, release inputs, and leave the app disarmed.
10. Close and reopen the app. No Twitch authorization, selected window or armed state should be restored.

If any safety behavior fails, stop live use and keep testing in Preview until corrected. Do not raise application privileges, install bypass drivers, or disable anti-cheat to make inputs work.

## Known operational limits

- Game focus can change between a native check and `SendInput`. Release calls are still global key-up events and may reach a different window.
- A game can accept allowed keys in its own chat, console, overlays or menus. The app cannot identify those states.
- A full process/OS stall can also stop both watchdog threads. This is not an out-of-process hardware safety controller.
- Twitch authorization is validated about every 50 minutes and refreshed before expiry. This version disarms during those checks and requires local rearming afterward. Reconnect is also manual.
- The system clock must be accurate. Event age is preserved across transport and policy boundaries; commands delayed beyond the configured 100–750 ms expiry are dropped.
- Denied executable names and window classes are defense in depth, not verification that a selected executable is a game. A renamed/custom application may not be recognized.
- Windows portable builds are unsigned. Signing and a public release channel require a separately secured release process; no signing secrets belong in this repository.
