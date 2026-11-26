# Command profiles

Profiles contain only gameplay commands and participation/rate settings. They never contain credentials, Twitch authorization, a selected process/window, or a live/armed flag. Importing or editing a profile disarms control and requires local review and rearming.

## Build controls in the GUI

Open **Controls & commands**, or choose **Edit commands** from **Session**. The **Your commands** list stays beside the selected mapping. **Search** filters by command or action; **Clear** restores the list without losing a pending draft. Empty lists and searches show what to do next. All profile settings can be configured in the GUI; editing JSON is optional.

1. Choose a starting point under **Start from**, then click **New profile**. **Blank** starts with no commands, **WASD movement** supplies W/A/S/D movement and Space to jump, and **Arrow-key movement** supplies arrows and Space to jump. **Keyboard & mouse** adds four look directions, two mouse buttons, and wheel up/down to the five WASD commands, for 13 mappings. All four start with the conservative moderator-and-broadcaster audience and default safety limits. Enter a **Profile name**.
2. Click **New command** to add a command, or select an existing row to edit it. Set **Chat command** to an exact trigger such as `!jump`. Choose **Game action**: **Keyboard key**, **Mouse button**, **Mouse movement**, or **Mouse wheel**. The editor shows only the fields relevant to that action.
3. Choose the key or button and **Hold time**, or set movement or wheel values as described below. Set **Cooldown** to 0–60,000 ms. The cooldown is specific to that command; the global, per-viewer, and per-minute limits still apply across all action types. Use **Enable this command** to include or disable the mapping. Review the short summary and any inline validation message.
4. Click **Apply command** to update the current profile in memory. **Revert** discards the current command draft and restores the applied mapping; it does not reload the profile from disk. Invalid fields or a duplicate trigger leave the applied profile unchanged. The message explains the correction, and applying an invalid draft focuses the problem field. The editor distinguishes **Unapplied changes** from **Applied to this profile**; applied does not mean saved to disk.
5. Review **Safety & access** and click **Apply safety settings**. Use **Save profile** to keep the profile for another session. Saving also validates and applies any pending command and safety edits; invalid settings prevent the save.

**Duplicate** copies the selected mapping into a new draft with an unused command name. Edit it and click **Apply command**. The **Enable this command** checkbox changes the draft; click **Apply command** to apply that change. **Remove** deletes a mapping after confirmation. These operations remain unsaved until **Save profile**. A profile may contain 0–64 commands, so an empty profile is valid.

For a quick starting point, choose **Start with an action**, then **Add command**. Starters include jump, forward movement, four look directions, left/right mouse buttons, and wheel up/down. Adding one fills an unapplied draft, chooses an unused trigger, and brings the fields into view. You can change every field before **Apply command**. It never sends input or starts a session; replacing a pending draft uses the usual apply/discard/cancel prompt.

Changing the selected mapping while a draft is pending offers **Yes** to apply, **No** to discard that draft, or **Cancel** to keep editing. It does not silently throw away the draft. Editing a command or safety field immediately disarms controls; applying changes does not rearm them.

### Bind mouse controls

Mouse bindings are available in v0.4.0 and later. Choose the action from **Game action**, then configure it without moving or clicking the real pointer:

| Action | GUI fields | Example |
| --- | --- | --- |
| Mouse button | **Left mouse**, **Right mouse**, **Middle mouse**, **Side button 1**, or **Side button 2**; **Hold time** 10–450 ms | `!fire` → Left mouse, 100 ms |
| Mouse movement | **Horizontal · units** and **Vertical · units**, each −100 to 100; at least one must be nonzero | `!lookleft` → horizontal −25, vertical 0 |
| Mouse wheel | **Up** or **Down**, and **Steps** 1–3 | `!next` → Down, 1 step |

For movement, positive horizontal values move right and negative values move left; positive vertical values move down and negative values move up. Diagonal movement is allowed by setting both axes. These are relative input units, not fixed pixels or camera angles: Windows pointer settings and the game's sensitivity affect the result. Start with small values and test your game. Movement and wheel actions happen once per admitted command and have no hold-time setting.

A button command presses and releases one button for the selected short duration. The app serializes actions, so a movement command cannot run during its button hold. There is no separate button-down latch, drag sequence, absolute screen-coordinate action, or macro editor. All four action types share the same permissions, cooldowns, queue, and action budget; mouse commands do not create a second rate allowance.

Live mouse input requires the pointer over the selected game's client area, with the game foreground and its identity checks passing. Put the pointer in the play area during the arming countdown. Mouse actions are refused on window borders, outside the game, or over another detected window; movement too near an edge can also be refused. Release physical mouse buttons before using a mouse command. A refusal pauses control, so correct the condition and arm again locally. The app does not confine the pointer or automatically move it back into the game. Review the [mouse safety limits](SECURITY.md#mouse-targeting-and-movement-limits) before live use.

### Capture a key locally

Click **Press a key**, then press the desired game key while the capture field in the app has focus. Capture accepts only one unmodified letter, digit, arrow, or Space and updates the draft. Click **Apply command** afterward.

Capture is a local GUI interaction, not a global keyboard recorder. It stops when focus leaves the field, when you change tabs, or when you choose **Cancel** or press Escape. Shift, Ctrl, Alt, Windows/Meta keys and combinations are rejected, as are keys outside the allowed list. F8 remains the emergency stop while capture is active and also cancels capture. The key picker is available if you prefer not to capture a physical key. App keyboard shortcuts cannot perform their usual actions during capture; cancel capture first.

### Preview a mapping

Select a mapping and click **Preview**. Valid pending edits are applied first. The app switches the main session to **Preview · no game input** and pauses it while checking the selected mapping separately. This check cannot press keys, move the pointer, click buttons, or scroll, and connected Twitch messages cannot join it. A result beside the editor reports the permitted action or rejection reason; the **Practice pad** on **Session** illustrates an action that ran. No operating-system input is sent.

The simulated participant's role comes from **Try as** in the **Try a chat command** card on **Session**. For the default audience, **Moderator** should be accepted and **Viewer** rejected. Preview uses a synthetic user, so a Twitch user-ID allow/block list can also affect the result.

Each editor preview starts with fresh command history. It checks the mapping and participation rules but does not reproduce cooldowns, queue occupancy, or the rolling action budget accumulated during a real session. To test repeated commands and cooldown behavior, use **Session**, select **Preview · no game input**, click **Start preview**, and use **Try command** repeatedly. The session simulator remembers recent commands, displays an acceptance or rejection explanation beside the form, and still sends no input. Its command dropdown lists enabled mappings. You can also stay in Preview and choose **Connect Twitch to test real chat...** to receive your channel's messages without game input. Neither preview confirms actual game bindings, pointer placement, movement distance, or compatibility.

The **Practice pad** shows the selected key, mouse button, wheel direction, or movement of a virtual pointer. Movement stays within the illustration; reaching its edge does not predict what the real game will do. **Reset pad** recenters the illustration and clears its recent trail without resetting session cooldowns, counts, or rate limits. A queued command is illustrated only after Preview actually runs it. Old events from a previous profile or session and live game input cannot animate the pad.

### Share the enabled commands

Choose **Copy chat commands** on **Session** to copy a readable reference for a channel panel or stream notes. It includes enabled triggers and their actions, the allowed audience, command cooldowns, and shared pace limits. It notes when additional local allow/block restrictions apply without including Twitch user IDs. The reference also omits the freeform profile name, sign-in details, selected window, and session state.

Copying validates and applies pending command and safety edits first. Invalid drafts keep their correction messages and leave the clipboard unchanged. With no enabled commands, the app asks you to add or enable a mapping instead of copying an empty reference. Copying does not reset policy history or authorize game input. It does not save the profile, send chat, or post to your channel; paste the result yourself wherever viewers should read it. Large profiles produce a multiline list intended for notes or a panel, not a single Twitch chat message.

### Save and reopen profiles

**Save profile** writes the current profile to its selected file, asking for a location the first time. **Save a copy...** chooses another file; **Open profile...** reads an existing profile. The status line shows **Unsaved changes** or **Saved** and the selected filename. Only command mappings and safety settings are written.

Creating a new profile, opening another, or closing the app prompts when changes are unsaved. **Yes** saves them, **No** discards them, and **Cancel** keeps the current profile open. Canceling a save, a validation error, or a write failure prevents the requested replacement or close. A rejected profile file leaves the current profile and draft intact. Reopen a saved profile explicitly after restarting; authorization, selected windows, and armed state are never restored from it.

Existing schema-1 keyboard profiles load unchanged. Adding a mouse binding automatically upgrades the profile to schema 2; it stays at version 2 even if you later remove the mouse bindings. Schema-2 profiles can contain both keyboard and mouse commands and require v0.4.0 or later. Older versions reject the new format instead of silently treating mouse actions as keys. Save a copy if you need to retain a schema-1 file for an older app.

## Navigate and review your session

The bottom bar stays visible on every tab. **Start preview** starts a session without keyboard or mouse input; in Live mode it becomes **Arm game control · 3 s**. **Pause** stops the current session. The readiness message explains missing setup and provides links such as **Connect Twitch**, **Choose game**, or **Review reminder**. A safety stop keeps its cause and recovery advice visible, including focus loss, unsafe pointer placement, held modifiers, or a backend fault. Setup checks still govern whether Start is available, and correcting the cause always requires a local start or arm action. No keyboard shortcut starts or arms control, and a setup link never starts it automatically.

| Shortcut | Action |
| --- | --- |
| Ctrl+S | Save profile |
| Ctrl+Shift+S | Save a copy |
| Ctrl+O | Open a profile |
| Ctrl+1 / Ctrl+2 / Ctrl+3 / Ctrl+4 | Session / Controls & commands / Safety & access / Activity |
| Ctrl+Tab | Move to the next tab |
| Enter in the command list | Focus the selected command for editing |
| Enter in a command-name or numeric field | Apply the local command draft |
| F8 | Stop control and cancel key capture |

Tab navigation scrolls focused controls into view. Scrolling over a numeric field or closed dropdown moves its page without changing the setting. The command list and Activity keep their own scrolling. During key capture, use **Cancel** or Escape before using the app shortcuts.

In **Safety & access**, **Who can play?** uses readable audience names, including **Moderators + you (recommended)** and **Only you**. **Set a comfortable pace** contains the shared delay, each viewer's delay, and the per-minute action limit. Open **Show advanced settings** to edit queue size, input expiry, and user-ID lists. Collapsing that section does not disable its restrictions: the applied list counts and queue settings remain visible beneath the audience selector. Pending edits must be applied and saved as usual.

**Activity** keeps the latest 200 approved events in memory. Events explain actions and rejection reasons without raw chat or sign-in details. New entries follow the bottom only when you are already reading there. Older entries eventually leave this bounded history; it is not a permanent audit file.

**Session** and **Activity** also show **Preview + live since app opened** totals for accepted and blocked commands, combining the main Preview and live sessions. Accepted means the command entered the queue; it does not confirm game input, and a queued command can expire. Ordinary chat and unconfigured commands are ignored and do not increase these totals. **Queued now** is the current waiting count, while **Held input** reports an active held key or button. Isolated editor previews use a separate engine and do not contribute to these totals. **Clear activity**, **Reset pad**, pausing, and changing profiles do not reset the main engine's totals or policy history.

## Movement templates

| WASD command / key | Arrow-key command / key | Hold | Per-command cooldown |
| --- | --- | --- | --- |
| `!forward` / W | `!up` / Arrow Up | 150 ms | 300 ms |
| `!back` / S | `!down` / Arrow Down | 150 ms | 300 ms |
| `!left` / A | `!left` / Arrow Left | 150 ms | 300 ms |
| `!right` / D | `!right` / Arrow Right | 150 ms | 300 ms |
| `!jump` / Space | `!jump` / Space | 100 ms | 1,000 ms |

The **Keyboard & mouse** template includes those five WASD mappings plus:

| Command | Mouse action | Per-command cooldown |
| --- | --- | --- |
| `!lookleft` / `!lookright` | Horizontal movement −25 / +25 units | 500 ms |
| `!lookup` / `!lookdown` | Vertical movement −25 / +25 units | 500 ms |
| `!primary` / `!secondary` | Left / right button held for 100 ms | 1,000 ms |
| `!wheelup` / `!wheeldown` | One wheel step up / down | 500 ms |

The built-in starter profile uses the WASD mappings. The default audience is **moderators and the broadcaster**. Select **Everyone in chat** under **Who can play?** only for open chat participation. Windows resolves each configured key through its keyboard mapping and sends a scan-code input. Verify your keyboard layout and game bindings in a controlled live test after reviewing the profile in Preview.

## Exact commands

Triggers begin with `!`, then a lowercase ASCII letter, then optional ASCII letters, digits, underscores, or hyphens. At most 24 characters follow `!`. Incoming commands are case-insensitive but otherwise exact: `!JUMP` is accepted for `!jump`; `!jump 500`, leading/trailing spaces, multiline text, Unicode lookalikes, and appended instructions are rejected.

Keyboard bindings allow only letters A–Z, digits 0–9, arrows, and Space. Mouse bindings allow only the bounded button, movement, and vertical-wheel actions above. An imported profile cannot enable Enter, Tab, Escape, function keys, modifiers, Windows keys, arbitrary text, chords, macros, absolute coordinates, or program execution. Only one key or mouse button may be held at once; an additional release gap prevents consecutive presses becoming an indefinite hold. Chat supplies the exact trigger only, never a direction, distance, duration, button, or other argument.

## Settings

| Field | Default | Allowed values |
| --- | --- | --- |
| `schema_version` | 1 | 1 for legacy keyboard profiles; 2 supports keyboard and mouse |
| `name` | Starter | 1–64 printable characters |
| `audience` | moderator | everyone, subscriber, vip, moderator, broadcaster |
| `global_cooldown_ms` | 250 | 100–60,000 |
| `viewer_cooldown_ms` | 1,500 | 250–60,000 |
| `max_actions_per_minute` | 90 | 1–300 |
| `queue_size` | 8 | 1–8 |
| `queue_ttl_ms` | 500 | 100–750 |
| `allowed_user_ids` | empty | Up to 512 unique numeric Twitch ID strings |
| `blocked_user_ids` | empty | Up to 512 unique numeric Twitch ID strings |
| `commands` | Five starter commands | 0–64 unique command entries |
| Command `hold_ms` | 100 | 10–450 for keys and mouse buttons; omitted for movement/wheel |
| Command `cooldown_ms` | 500 | 0–60,000; global/viewer limits still apply |
| Command `enabled` | true | Boolean |

Schema-2 mouse entries use `action_type` to select one strict shape. Fields for another action are rejected, even when their value would be zero or empty. Keyboard entries keep the legacy shape with `key` and no `action_type` field in exported files.

| `action_type` | Action fields | Bounds |
| --- | --- | --- |
| `mouse_button` | `button` | `left`, `right`, `middle`, `x1`, or `x2`; optional `hold_ms` 10–450 |
| `mouse_move` | `dx`, `dy` | Integer −100 to 100 per axis; omitted axes default to 0; not both zero |
| `mouse_wheel` | `wheel` | Integer −3 to 3 excluding zero; positive up, negative down |

Every entry requires `trigger`; `cooldown_ms` and `enabled` are optional with the defaults above. Schema 1 accepts keyboard fields only. Numeric booleans, fractional numbers, unknown fields, and mixed-action fields are rejected.

Subscriber audience includes subscribers (including founder badges), moderators, and the broadcaster. VIP audience includes VIPs, moderators, and the broadcaster. Moderator audience includes moderators and the broadcaster. Broadcaster audience permits only the verified channel owner's numeric ID. An allow list *also* requires the selected audience role; a block list takes precedence over all roles, including the broadcaster. Use Twitch numeric user IDs, not display names.

All rate checks apply before queuing, so rejected input cannot reserve execution slots. The rolling minute budget counts admitted actions, including those that later expire. In the main session engine, cooldowns and replay history survive disarming and profile changes, including in Preview mode. The editor's isolated one-shot preview has separate, fresh policy history on each click. No queue survives disarming, disconnecting, editing, or emergency stop.

## File format example (optional)

Use the GUI to create and edit profiles. This example documents the exported format for people who also want to inspect files or keep them in version control.

```json
{
  "schema_version": 2,
  "name": "Keyboard and mouse test",
  "audience": "moderator",
  "global_cooldown_ms": 250,
  "viewer_cooldown_ms": 1500,
  "max_actions_per_minute": 90,
  "queue_size": 4,
  "queue_ttl_ms": 500,
  "allowed_user_ids": [],
  "blocked_user_ids": [],
  "commands": [
    {"trigger": "!forward", "key": "w", "hold_ms": 150, "cooldown_ms": 300, "enabled": true},
    {"trigger": "!fire", "action_type": "mouse_button", "button": "left", "hold_ms": 100, "cooldown_ms": 1000, "enabled": true},
    {"trigger": "!lookleft", "action_type": "mouse_move", "dx": -25, "dy": 0, "cooldown_ms": 500, "enabled": true},
    {"trigger": "!next", "action_type": "mouse_wheel", "wheel": -1, "cooldown_ms": 500, "enabled": true}
  ]
}
```

Files are limited to 64 KiB of UTF-8 JSON. Unknown fields, duplicate object keys, duplicate triggers, duplicate IDs, invalid types, unsupported versions, and unsafe/out-of-range values are rejected. Profile saving replaces the chosen file atomically after validation.
