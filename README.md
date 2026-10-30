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

1. Start on **Session** with **Preview · no keyboard input** selected. Click **Start preview** in the bottom bar, choose `!jump` under **Try a chat command**, and click **Try command**. No Twitch account or game is needed.
2. Use **Edit commands** to make the starter controls your own. In **Controls & commands**, choose a **Blank**, **WASD movement**, or **Arrow-key movement** starting profile, or edit the current mappings. Click **Apply command**, then **Save profile**.
3. Use **Who can play?** to review **Safety & access**. The default is **Moderators + you (recommended)**. Start with trusted participants and a comfortable command pace.
4. When ready, select **Live game control · Windows** on **Session**. Twitch and game setup appear. Supply your own Public Client ID and channel, then select **Connect Twitch**. See [Twitch setup](docs/TWITCH_SETUP.md); no client secret is needed. Keep the temporary sign-in code off your stream.
5. Open a game as a regular user, choose **Refresh windows**, and select its exact window. Read and check **I understand when to pause**. Click **Arm game control · 3 s** in the bottom bar, then focus the game before the countdown ends.
6. Use **Pause** or **F8** to stop. Losing game focus, disconnecting, or changing settings also stops control. Starting again always requires a local action.

The bottom bar stays visible on every tab. It explains what is ready or missing and offers a link to the next setup step. **STOP ALL · F8** remains available at the top. To test actual chat without game input, stay in Preview and choose **Connect Twitch to test real chat...**.

## Safety boundaries

- Only exact configured commands are accepted. Chat cannot add commands, arm the app, change settings, run programs, access files, type arbitrary text, or provide key combinations.
- Commands are one key at a time, with a hard duration limit and release watchdog. Mouse input, Enter, Tab, Escape, modifiers, function keys, and the Windows key are unavailable to chat.
- Every action checks the foreground window and its bound process identity. Browsers, terminals, shell windows, elevated targets, and this app are rejected.
- Per-viewer, per-command, global, and per-minute limits constrain floods. Queues and identity caches are bounded; stale and duplicate events are dropped.
- Streamers control audience roles and optional Twitch user-ID allow/block lists. Verified Twitch metadata supplies roles, not chat text or display names. Shared-chat events from another channel are ignored.
- No credentials ship in source or builds. No token files, environment-file loading, account database, network listener, telemetry, or automatic update system.

**Windows input is not a sandbox.** `SendInput` targets the current desktop input stream. There is a small foreground-switch race, and this app cannot distinguish gameplay from a text box or console inside the game. Pause before opening chat, console, overlays, purchases, or sensitive menus. A deliberate viewer can still take harmful actions *within the allowed game controls*. Use a disposable game/save for initial testing. Some games and anti-cheat systems reject synthetic input; this app does not bypass them. Read the full [security model](docs/SECURITY.md).

## Profiles and everyday controls

Configure mappings in **Controls & commands**; no JSON editing is required. **New command**, **Duplicate**, **Remove**, and **Enable this command** manage the list. Choose a **Game key** or use **Press a key** while the app has focus. Capture accepts only an unmodified letter, digit, arrow, or Space; **Cancel** or Escape ends capture and F8 stops control.

**Apply command** updates the current profile in memory. **Save profile** writes it to disk; **Save a copy...** chooses another file and **Open profile...** loads one. The app keeps applied changes separate from unsaved drafts and prompts before replacing or closing an edited profile. Invalid fields remain available for correction. Credentials, authorization, selected windows, and armed state are never saved.

Use **Ctrl+S** to save, **Ctrl+Shift+S** to save a copy, **Ctrl+O** to open, and **Ctrl+1–4** or **Ctrl+Tab** to change tabs. Keyboard focus scrolls fields into view. No shortcut arms game input. Scrolling over a setting moves the page without accidentally changing its value; the command list and Activity scroll independently.

**Activity** shows the latest 200 approved events in plain language. New events do not pull you back to the bottom while you read older entries. Status updates and command-list refreshes avoid repainting unchanged content.

The editor and imported JSON use the same strict validation. See the [GUI profile guide](docs/PROFILES.md) for templates, keyboard navigation, preview behavior, and field limits.

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
