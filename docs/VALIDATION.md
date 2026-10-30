# Validation and first live acceptance

This project is a **development preview**. Automated tests and a successful Windows build do not establish game compatibility, anti-cheat compatibility, or perfect desktop isolation. No test uses real Twitch credentials or injects keys into a user's desktop.

## Automated coverage

- Strict profile parsing, unknown and duplicate fields, unsafe keys, bounded durations and IDs.
- Real Tk command-editor interactions with fake callbacks: immediate disarming on edits, invalid or duplicate drafts, canceled selection changes, safe duplication, empty profiles, filtering, rejected profile changes, and preview routing. Local key capture tests cover approved keys, blocked modifiers/shortcuts, focus loss, Escape cancellation, and F8 emergency-stop dispatch.
- Deterministic command policy with fake clocks: permissions, cooldowns, floods, stale/replayed messages, queue expiry, focus loss, modifier keys, lost connection, emergency stop and release failure.
- Native Win32 boundary with a fake API: input structure ABI, process identity/privilege, window handle reuse, foreground and desktop checks, hotkey failure, existing physical keys, independent hold deadline and release retry.
- Twitch transport with synthetic HTTP/WebSocket responses: device grant, minimal scope, account ownership, refresh rotation, cancellation, fixed TLS endpoints, redirects, revocation, session identity, Shared Chat, stale timestamps and replay filtering.
- Source/distribution checks for credential-shaped literals, unsafe bundled files and changes to the explicit packaging boundary. This check is deliberately scoped; it is not proof that every possible secret format can be recognized.
- Windows CI: Python tests, Tk GUI construction and frozen executable startup in `--smoke-test`. This flag never connects to Twitch or injects keys.

## Streamer acceptance checklist

Use a test game/save, no stream capture of the authorization dialog, and a moderator-only profile initially.

### Configure and verify controls offline

1. Start the app normally, without administrator rights. It must say **DISARMED · PREVIEW**.
2. In **Controls & commands**, create a **Blank** profile and add a command with **New**. Set its game key, hold, and cooldown; click **Apply**. Try a duplicate trigger, an invalid duration, and an invalid command format. Each must show an error and preserve the last applied mapping.
3. Use **Press a key**. Confirm a permitted key updates only the draft, modifier combinations are rejected, Escape and focus loss cancel capture, and F8 cancels capture and invokes emergency stop. Repeat with the allowed-key picker.
4. Verify **Duplicate**, **Disable / enable**, **Remove**, and **Revert**. Begin editing, select another row, and choose **Cancel** in the draft prompt; the original selection and edits must remain. Test **Find** without losing the draft.
5. **Save profile**, then use **Open...** to reopen it and compare the mappings and safety settings. Test the save/discard/cancel prompt when creating a new profile or closing. Cancel must retain edits; an invalid profile file must not replace them. Create **WASD movement** and **Arrow-key movement** profiles and confirm their default audience remains moderators plus broadcaster.
6. Select an enabled mapping and click its **Preview** button. The main session must remain disarmed in Preview mode and the result must say no key was sent. Set the role beside **Preview command** on **Session** to **viewer**, then preview the mapping again; the moderator-only profile must reject it. Restore **moderator** afterward.
7. On **Session**, click **Arm preview** and try an enabled command as moderator using **Try command**. Activity should show a preview action and no actual keyboard input. Try extra arguments and an unlisted command; both must be rejected. Repeat a valid command quickly to check cooldowns. Use this session simulator for rate-limit checks: each editor **Preview** uses a fresh isolated policy instance and does not model the real session's elapsed cooldown usage, queue, or rolling action budget.

### Verify live control in a test game

1. Connect with your own Public Client ID and broadcaster account. Cancel once and confirm the app is disarmed. Reconnect and authorize successfully.
2. Select your game, choose live input and acknowledge the focus limitation. Arm and focus the game within three seconds. Send one command from an allowed account. Confirm only the expected action occurs.
3. Press F8 while a longer permitted tap is active. The key should release, the queue should clear, and further chat must do nothing until local arming.
4. Arm again, send a command and switch away from the game. Confirm immediate release/disarming and no automatic resume when you return.
5. Verify holding Ctrl, Alt, Shift or the Windows key prevents or stops chat input. Verify a key already physically held is not taken over by the app.
6. Disconnect Twitch and edit, open, or create a profile while idle and while armed. Each must clear the queue, release inputs, and leave the app disarmed. Saving must not rearm control. A mapping preview must switch to Preview and leave the main session disarmed.
7. Close and reopen the app. No Twitch authorization, selected window or armed state should be restored. Reopen the saved profile explicitly to restore its mappings and settings.

If any safety behavior fails, stop live use and keep testing in Preview until corrected. Do not raise application privileges, install bypass drivers, or disable anti-cheat to make inputs work.

## Known operational limits

- Game focus can change between a native check and `SendInput`. Release calls are still global key-up events and may reach a different window.
- A game can accept allowed keys in its own chat, console, overlays or menus. The app cannot identify those states.
- A full process/OS stall can also stop both watchdog threads. This is not an out-of-process hardware safety controller.
- Twitch authorization is validated about every 50 minutes and refreshed before expiry. This version disarms during those checks and requires local rearming afterward. Reconnect is also manual.
- The system clock must be accurate. Event age is preserved across transport and policy boundaries; commands delayed beyond the configured 100–750 ms expiry are dropped.
- Denied executable names and window classes are defense in depth, not verification that a selected executable is a game. A renamed/custom application may not be recognized.
- Windows portable builds are unsigned. Signing and a public release channel require a separately secured release process; no signing secrets belong in this repository.
