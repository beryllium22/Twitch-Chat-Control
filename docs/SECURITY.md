# Security model and safe operation

Twitch Chat Control treats **every chat message as untrusted input**. The streamer chooses a small list of exact chat commands and the bounded keyboard or mouse action for each command. Chat cannot create commands, choose action parameters, change the selected game, enable live control, change permissions, run a program, execute code, browse files, use the clipboard, or supply text to type.

This design reduces the risk of letting viewers affect a game. **It does not turn Windows input injection into a sandbox, and it cannot guarantee that chat will never affect another application.** Preview mode is the starting point. Live use requires a streamer who can stop the session and a game whose controls have been reviewed.

## Trust boundaries

| Boundary | Enforcement |
| --- | --- |
| Twitch identity | Read the streamer's own authorized channel. Trust account IDs and role metadata from Twitch, never role claims inside message text. |
| Chat to action | Exact command matching, validated profiles, finite key/button allowlists and bounded relative movement/wheel values; no chat-supplied arguments, arbitrary text, scripts, macros, shell commands, absolute screen coordinates, or key combinations. |
| Permission to act | Local arming, preview by default, configured audience restrictions, blocked account IDs, cooldowns, bounded work, and stale-message rejection. Chat cannot arm or resume input. |
| Game to desktop | An explicitly selected visible window, identity checks, foreground checks before input, and an independent monitor. Mouse input adds client-area, window-under-pointer, and GUI routing checks. Known shells, browsers, editors, messaging apps, and Windows UI processes are excluded from selection as defense in depth. |
| Credential to distribution | Each streamer supplies their own Twitch application Client ID and authorizes in Twitch. Access and refresh tokens are held in memory; profile JSON and builds must not contain them. No client secret is required. |

The local account, the app's Python code and dependencies, Windows, Twitch, and the selected game are trusted. This app does not protect against malware already running in the streamer's account, a modified app binary, or a malicious game. The executable-name exclusion list prevents common mistakes; it cannot prove that a selected executable is a game.

## Native input restrictions

The backend accepts one bounded action at a time. Keyboard actions use letters `a`–`z`, digits `0`–`9`, arrow keys, or space. Mouse actions use left, right, middle, or either side button; relative movement from −100 to 100 input units per axis; or vertical wheel movement of 1–3 steps in either direction. A movement must have at least one nonzero axis. Keys and buttons may be held for 10–450 ms; movement and wheel events are instantaneous actions with the same rate limits.

It does not expose Enter, Tab, Escape, Windows, Ctrl, Alt, Shift, function keys, punctuation, Unicode text injection, absolute coordinates, arbitrary scan codes, combined key/button actions, persistent button-down commands, drag macros, or scripts. Each action shape and its limits are enforced again at the native boundary. The engine does not execute another action while a key or button is held and inserts a release gap after a hold.

Live input is refused if this app is elevated, the target's privilege level cannot be verified, the target is elevated, or the F8 emergency hotkey cannot be registered. The app does not request administrator privileges and does not attempt to bypass Windows input restrictions, game anti-cheat protections, or access controls.

Selecting a game records its window handle, process ID, process creation time, executable path, window class, and owning thread. A unique temporary window property is also attached to the selected window. Input requires the same identity and property, which protects against common window-handle and process-ID reuse mistakes. Window titles are allowed to change as the game changes levels; the title is not the security identity. No method tries to bring the game to the foreground automatically.

Before input, the app checks that the selected window is still foreground and the desktop is accessible in an active Windows session. Ctrl, Alt, Shift, or either Windows key being down pauses live input. The app also refuses to begin a key action whose key is already held by the streamer, and refuses a mouse action while a physical mouse button is held. These checks reduce accidental shortcuts and drags; they are not an atomic lock on physical input state.

An independent thread owns the F8 hotkey and checks focus, modifiers, desktop availability, and held-input deadlines. It does not depend on the GUI event loop. Each injected key or mouse button has an independent 500 ms maximum hold deadline; normal profile presses are shorter. A safety fault disables native live input and attempts to release all app-owned keys and buttons **before** notifying the controller or GUI. Release events are still attempted after focus changes, because suppressing an up event would risk leaving an input held. Failed releases retain their tracked state for retries; input API failures disable further live input until the app restarts. Ordinary mouse guard rejections, such as the pointer leaving the game, pause control and require local rearming after correction.

### Mouse targeting and movement limits

Before mouse input, the native backend checks that the pointer is inside the selected window's client area and that the window at that point belongs to the selected game's root window. The client check excludes the outer title bar and borders. It also checks the selected GUI thread for active/focused windows, rejects Windows menu and move/resize loops, and rejects mouse capture directed to a different root window. Failure to obtain required information blocks input.

Relative movement is checked against a conservative envelope that allows for the documented fourfold Windows acceleration effect, including window checks around that envelope. The app checks pointer safety again after sending a move. After a mouse action starts, the independent monitor continues checking mouse safety until the session is paused. The app never confines the pointer, steals mouse capture, changes Windows pointer settings, or teleports the cursor back into the game. A failed check stops control instead.

These checks reduce accidental input outside the game; they do not guarantee confinement or event routing. Windows pointer speed and acceleration affect relative movement, and a game's camera may use its own sensitivity or recentering. A configured unit is not a promised pixel or camera angle. Physical pointer motion, game cursor warping, capture changes, window movement, overlays, and focus changes can occur between a check and input processing. `WindowFromPoint` also has documented exclusions for hidden and disabled windows. Point samples do not prove that every intermediate location is unobstructed. Start with small movement amounts, keep the pointer away from borders, and stop before opening overlays or menus.

## Important limits

### A foreground check is not a sandbox

Windows `SendInput` inserts events into the global keyboard or mouse input stream. It does not address a particular window. A focus, pointer, or capture change can occur after a check and before an event is processed. A Windows shortcut can also be formed if a physical modifier changes at that same moment. The app checks immediately before input and polls in the independent monitor, but it cannot make those operations atomic. Mouse-button release is also global and can complete an interaction after the pointer has moved.

Use a separate Windows account, disposable environment, or dedicated streaming/game PC when the consequences of an unintended action would be serious. Close sensitive applications before live sessions. Do not interpret the blocked-application list as a security boundary against arbitrary local software.

### Games contain menus, consoles, purchases, and chat boxes

A game may give an allowed action a powerful meaning. For example, a letter may open chat, a click may confirm a purchase, a side button may navigate an embedded browser, or a control may delete an item or activate a mod command. The app cannot identify all in-game text fields, overlays, menus, or mod interfaces. Native Windows menu checks do not recognize menus drawn by the game itself. Review the game's bindings and choose commands accordingly. Remove or rebind controls that open text input or dangerous menus. Press **F8 before opening any console, chat field, overlay, store, account screen, or other sensitive interface**. Test the exact game and profile in a harmless scene first.

Although no free-text action exists, repeated letter-key commands could still type a sequence if a text field opens. Banning Enter and command punctuation helps, but it does not make such a field safe.

### Watchdogs are not hard real-time guarantees

The 500 ms hold limit is an in-process deadline. A frozen or killed process, operating-system stall, machine crash, or denied input API can prevent timely key or button release. The monitor runs independently of the GUI, but it is not a separate privileged service or kernel driver. If the app or game becomes unresponsive, stop the session and physically press/release any affected key or button after regaining control.

The app avoids starting an action on an already held streamer key or mouse button. It cannot distinguish all cases where the streamer physically presses the same key or button during a synthetic hold, so releasing that action may briefly affect the streamer's simultaneous input. Physically moving the mouse during a button hold can still create a short drag within the game, even though chat has no drag command. Short presses reduce this interference.

### Compatibility and anti-cheat

Some games ignore `SendInput`, use a different input path, reserve F8, or prohibit automation. No game compatibility is implied by unit tests. Use games and modes that permit this interaction. Do not run the app as administrator to work around input failures or disable anti-cheat protections. If F8 cannot be registered, live control remains disabled until the conflict is resolved and the app is restarted.

## Streamer checklist

1. Start in preview and verify the channel and audience settings.
2. Configure only the commands needed for the game. Begin with low action rates and short taps.
3. Review those keyboard and mouse bindings in the game, including mods, menus, chat, and overlays.
4. Close sensitive applications and select the actual game window from the refreshed window list.
5. Confirm the app reports that **F8 emergency stop is ready**.
6. Arm locally, focus the game during the countdown, position the pointer safely inside its play area for mouse commands, and try a harmless command.
7. Press F8 and verify that commands stop. Rearm locally only when ready.
8. Pause before switching apps or opening a sensitive in-game interface. A fault or disconnect should require a new local arm.

## Credentials and logs

Do not paste client secrets, access tokens, refresh tokens, passwords, or Twitch device authorization codes into issues or screenshots. The connection flow never needs a client secret. A Client ID identifies the streamer's own registered Twitch application and is not an OAuth secret, but this project still ships without anyone's Client ID prefilled.

The app's action log should contain bounded decision metadata, not raw chat bodies or HTTP response dumps. Tokens are not intentionally written to disk. Memory-only storage does not prevent a privileged debugger, malware, an operating-system crash dump, or swap/pagefile capture from reading process memory. Disconnect and close the app when finished; Twitch's connection settings can revoke authorization.

## Validation status

Automated tests cover strict keyboard/mouse action shapes and bounds, shared rate limits, privileged execution, sensitive-window selection, process identity changes, marker loss, modifiers, already held inputs, mouse targeting/routing checks, F8 failure and panic handling, focus loss, desktop unavailability, failed input and release, watchdog deadlines, and Windows input structures using fake native APIs. They do not inject real inputs or prove behavior in a Windows game. Before a public release, perform the Windows manual checks and per-game compatibility checks described above, and record the OS, Python/build architecture, game version, and result.

## Primary API references

The implementation follows Microsoft's documented API behavior. The Microsoft documentation reviewed for the native input path is linked here.

- [SendInput](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-sendinput): global input, exact `INPUT` size, existing-key state, failure reporting, and UIPI restrictions.
- [INPUT](https://learn.microsoft.com/en-us/windows/win32/api/winuser/ns-winuser-input): the complete keyboard, mouse, and hardware union is required for the correct ABI size.
- [MOUSEINPUT](https://learn.microsoft.com/en-us/windows/win32/api/winuser/ns-winuser-mouseinput): relative motion, button transitions, wheel increments, and pointer acceleration.
- [GetClientRect](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-getclientrect) and [GetCursorPos](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-getcursorpos): client-area bounds, exclusive right/bottom edges, screen coordinates, and input-desktop access.
- [WindowFromPoint](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-windowfrompoint): window lookup and hidden/disabled-window exclusions.
- [GetCapture](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-getcapture), [GetGUIThreadInfo](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-getguithreadinfo), and [GUITHREADINFO](https://learn.microsoft.com/en-us/windows/win32/api/winuser/ns-winuser-guithreadinfo): cross-thread capture/routing inspection and native menu/move states.
- [GetAsyncKeyState](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-getasynckeystate): current-state high bit and failure behavior on inaccessible desktops.
- [RegisterHotKey](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-registerhotkey): thread-owned hotkey registration, message delivery, and conflicts.
- [SetPropW](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-setpropw): window properties and UIPI restrictions.
- [OpenInputDesktop](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-openinputdesktop): input-desktop access and the disconnected-session caveat.
- [WTS_CONNECTSTATE_CLASS](https://learn.microsoft.com/en-us/windows/win32/api/wtsapi32/ne-wtsapi32-wts_connectstate_class): active and disconnected Windows session states.
