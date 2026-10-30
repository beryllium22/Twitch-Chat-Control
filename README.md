# Twitch Chat Control

A Windows desktop app that lets a streamer connect their own Twitch channel and map exact chat commands to short, allowlisted game key presses.

**Development preview. Starts disarmed. Preview mode sends no input.** Live control requires an explicitly selected game window, a working local emergency hotkey, and a separate local arming action. Do not run as administrator.

## Run from source

Windows 10/11, 64-bit Python 3.11–3.14 with Tcl/Tk:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .
.\.venv\Scripts\python.exe -m twitch_chat_control
```

Linux and macOS support the offline preview and automated tests; native game input is Windows only.

## First session

1. Open **Controls & commands**. Edit the starter controls or choose **Blank**, **WASD movement**, or **Arrow-key movement** under **New from**, then click **New profile**. Configure your mappings and click **Apply**.
2. Select a mapping and click **Preview** to check it without sending keys. No Twitch account or game is needed. This leaves the connected-chat engine disarmed in Preview mode. Review **Safety & access**; new profiles allow moderators and the broadcaster by default.
3. Register your own Twitch **Public** application and enter its Client ID and your channel name in this app. See [Twitch setup](docs/TWITCH_SETUP.md). No client secret is needed or accepted.
4. Select **Connect**, complete Twitch's device sign-in in your browser, and keep the sign-in code off your stream. Authorization is kept in process memory only.
5. On **Session**, select the exact window of a game running as a regular user. Choose **Live game input · Windows**, acknowledge the game focus limitation, and click **Arm live · 3 seconds**. Focus the game before the countdown ends.
6. Press **F8** at any time to stop. Returning to another window, losing the connection, or changing the profile also disarms controls. You must arm again locally.

## Safety boundaries

- Only exact configured commands are accepted. Chat cannot add commands, arm the app, change settings, run programs, access files, type arbitrary text, or provide key combinations.
- Commands are one key at a time, with a hard duration limit and release watchdog. Mouse input, Enter, Tab, Escape, modifiers, function keys, and the Windows key are unavailable to chat.
- Every action checks the foreground window and its bound process identity. Browsers, terminals, shell windows, elevated targets, and this app are rejected.
- Per-viewer, per-command, global, and per-minute limits constrain floods. Queues and identity caches are bounded; stale and duplicate events are dropped.
- Streamers control audience roles and optional Twitch user-ID allow/block lists. Verified Twitch metadata supplies roles, not chat text or display names. Shared-chat events from another channel are ignored.
- No credentials ship in source or builds. No token files, environment-file loading, account database, network listener, telemetry, or automatic update system.

**Windows input is not a sandbox.** `SendInput` targets the current desktop input stream. There is a small foreground-switch race, and this app cannot distinguish gameplay from a text box or console inside the game. Pause before opening chat, console, overlays, purchases, or sensitive menus. A deliberate viewer can still take harmful actions *within the allowed game controls*. Use a disposable game/save for initial testing. Some games and anti-cheat systems reject synthetic input; this app does not bypass them. Read the full [security model](docs/SECURITY.md).

## Profiles

Configure command mappings in **Controls & commands**; no JSON editing is required. Select a row to edit its exact chat command, game key, hold duration, cooldown, and enabled state. **New**, **Duplicate**, **Remove**, and **Disable / enable** manage mappings. Choose a key from the allowed-key picker or use **Press a key** while the app has focus. Capture accepts only an unmodified letter, digit, arrow, or Space; Escape cancels and F8 still stops control.

**Apply** updates the current profile in memory. **Save profile** writes commands and safety settings to a profile file; **Save as...** creates a separate file and **Open...** loads one. The app prompts before replacing or closing a profile with unsaved changes. Draft changes disarm controls immediately. Credentials, authorization, selected windows, and armed state are never saved.

The editor and imported JSON use the same strict validation. See the [GUI profile guide](docs/PROFILES.md) for templates, preview behavior, and field limits.

## Development and Windows build

```powershell
.\.venv\Scripts\python.exe -m pip install -e '.[dev,build]'
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe scripts/check_distribution.py
.\.venv\Scripts\python.exe -m PyInstaller packaging/TwitchChatControl.spec --noconfirm --clean
```

The Windows CI workflow runs tests, performs a GUI construction smoke check, scans the distribution, and uploads a portable ZIP artifact. It builds from an explicit entry point and explicit public documentation; local profiles and credential files are never bundled. No signing certificate is bundled. Build artifacts are unsigned development builds until a separately managed signing/release process exists.

See [validation and manual acceptance](docs/VALIDATION.md) before streaming with live input.
