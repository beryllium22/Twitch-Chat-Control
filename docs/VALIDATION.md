# Validation and first live acceptance

This project is a **development preview**. Automated tests and a successful Windows build do not establish game compatibility, anti-cheat compatibility, or perfect desktop isolation. No test uses real Twitch credentials or injects keys into a user's desktop.

## Automated coverage

- Strict profile parsing, unknown and duplicate fields, unsafe keys, bounded durations and IDs.
- Real Tk command-editor interactions with fake callbacks: immediate disarming on edits, invalid or duplicate drafts, canceled selection changes, safe duplication, empty profiles, filtering, rejected profile changes, and preview routing. Local key capture tests cover approved keys, blocked modifiers/shortcuts, focus loss, visible Cancel and Escape cancellation, and both local and native F8 emergency-stop dispatch.
- Profile lifecycle and guided GUI flow: separate draft/applied/saved states, lossless canceled or failed operations, readable audience choices, readiness guidance, no automatic arming, focused validation errors, and shortcuts that manage profiles or navigation without enabling input.
- Presentation behavior with real Tk widgets: focused fields scroll into view; wheel routing preserves field values and nested scrolling; activity updates are bounded and batched without forcing a reader back to the bottom; unchanged command rows and status values are reused.
- Deterministic command policy with fake clocks: permissions, cooldowns, floods, stale/replayed messages, queue expiry, focus loss, modifier keys, lost connection, emergency stop and release failure.
- Native Win32 boundary with a fake API: input structure ABI, process identity/privilege, window handle reuse, foreground and desktop checks, hotkey failure, existing physical keys, independent hold deadline and release retry.
- Twitch transport with synthetic HTTP/WebSocket responses: device grant, minimal scope, account ownership, refresh rotation, cancellation, fixed TLS endpoints, redirects, revocation, session identity, Shared Chat, stale timestamps and replay filtering.
- Source/distribution checks for credential-shaped literals, unsafe bundled files and changes to the explicit packaging boundary. This check is deliberately scoped; it is not proof that every possible secret format can be recognized.
- Windows CI: Python tests, Tk GUI construction and frozen executable startup in `--smoke-test`. This flag never connects to Twitch or injects keys.

## Streamer acceptance checklist

Use a test game/save, no stream capture of the authorization dialog, and a moderator-only profile initially.

### Configure and verify controls offline

1. Start the app normally, without administrator rights. It must show **Paused · Preview**, with **Preview · no keyboard input** selected. Twitch and game setup should not obscure the first offline test.
2. Click **Start preview** in the bottom bar. Choose `!jump` in **Try a chat command** and click **Try command** as **Moderator**. The result must explain acceptance and no actual keyboard input should occur. Use **Pause**, then change tabs; the bottom controls and top **STOP ALL · F8** must remain visible.
3. In **Controls & commands**, create a **Blank** profile and add a command with **New command**. Set its game key, hold time, and cooldown; click **Apply command**. Try a duplicate trigger, an invalid duration, and an invalid command format. Each must explain the error, focus the field when applied, and preserve the last applied mapping.
4. Use **Press a key**. Confirm a permitted key updates only the draft, modifier combinations are rejected, **Cancel**, Escape, and focus loss end capture, and F8 ends capture and stops control. App shortcuts must not open/save profiles or navigate while capture is listening. Repeat with the allowed-key picker.
5. Verify **Duplicate**, **Enable this command**, **Remove**, and **Revert**. Begin editing, select another row, and choose **Cancel** in the draft prompt; the original selection and edits must remain. Test **Search** with no matches, then **Clear**, without losing the draft.
6. **Save profile**, then use **Open profile...** to reopen it and compare the mappings and safety settings. Test **Save a copy...** and save/discard/cancel prompts when creating a new profile or closing. Cancel must retain edits; an invalid profile must not replace them. Movement templates must default to **Moderators + you (recommended)**.
7. In **Safety & access**, verify the readable audience choices and pace controls. Set a user-ID restriction in **Show advanced settings**, apply it, and collapse the section. The applied restriction counts and queue settings must remain visible and effective. Invalid advanced fields must open the section and receive focus when validation fails.
8. Select an enabled mapping and click its **Preview** button. The main session must remain paused in Preview mode and the result must say no key was sent. Change **Try as** on **Session** to **Viewer** and preview the mapping again; the moderator-only profile must reject it. Restore **Moderator** afterward.
9. On **Session**, click **Start preview** and use **Try command** repeatedly. Check extra arguments, unknown commands, disallowed roles, and cooldown rejections. Use this session simulator for rate-limit checks: each editor **Preview** starts with fresh history and does not model the real session's previous cooldown usage, queue, or rolling action budget.
10. Test Ctrl+S, Ctrl+Shift+S, Ctrl+O, Ctrl+1–4, and Ctrl+Tab. Test Enter from the command list and an edited command field. None must arm input. At the minimum window size, Tab through the forms; focused fields and correction messages should scroll into view. Scrolling over settings must not alter them, while lists and Activity retain their own scrolling.
11. Generate enough preview activity to scroll up and read older entries. New entries must not force the view back to the bottom. Activity must retain at most 200 events, and its messages must not expose raw chat, credentials, or internal exception details.

### Verify live control in a test game

1. Select **Live game control · Windows**. Twitch and game setup must appear; the bottom readiness message must explain missing setup. Supply your own Public Client ID and broadcaster account, then click **Connect Twitch**. Cancel once and confirm controls stay paused. Reconnect and authorize successfully.
2. Refresh and select your game, then read and check **I understand when to pause**. Once setup is ready, click **Arm game control · 3 s** and focus the game within three seconds. Send one command from an allowed account. Confirm only the expected action occurs.
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
