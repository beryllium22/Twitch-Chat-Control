# Command profiles

Profiles contain only gameplay commands and participation/rate settings. They never contain credentials, Twitch authorization, a selected process/window, or a live/armed flag. Importing or editing a profile disarms control and requires local review and rearming.

## Build controls in the GUI

Open **Controls & commands**, or choose **Edit commands** from **Session**. The **Your commands** list stays beside the selected mapping. **Search** filters by command or key; **Clear** restores the list without losing a pending draft. Empty lists and searches show what to do next. All profile settings can be configured in the GUI; editing JSON is optional.

1. Choose a starting point under **Start from**, then click **New profile**. **Blank** starts with no commands, **WASD movement** supplies W/A/S/D movement and Space to jump, and **Arrow-key movement** supplies arrows and Space to jump. All three start with the conservative moderator-and-broadcaster audience and default safety limits. Enter a **Profile name**.
2. Click **New command** to add a command, or select an existing row to edit it. Set **Chat command** to an exact trigger such as `!jump`. Choose **Game key** from the allowed-key list, which uses readable names such as **Space** and **Arrow Up**.
3. Set **Hold time** to 10–450 ms and **Cooldown** to 0–60,000 ms. The cooldown is specific to that command; the global, per-viewer, and per-minute limits still apply. Use **Enable this command** to include or disable the mapping. Review the short summary and any inline validation message.
4. Click **Apply command** to update the current profile in memory. **Revert** discards the current command draft and restores the applied mapping; it does not reload the profile from disk. Invalid fields or a duplicate trigger leave the applied profile unchanged. The message explains the correction, and applying an invalid draft focuses the problem field. The editor distinguishes **Unapplied changes** from **Applied to this profile**; applied does not mean saved to disk.
5. Review **Safety & access** and click **Apply safety settings**. Use **Save profile** to keep the profile for another session. Saving also validates and applies any pending command and safety edits; invalid settings prevent the save.

**Duplicate** copies the selected mapping into a new draft with an unused command name. Edit it and click **Apply command**. The **Enable this command** checkbox changes the draft; click **Apply command** to apply that change. **Remove** deletes a mapping after confirmation. These operations remain unsaved until **Save profile**. A profile may contain 0–64 commands, so an empty profile is valid.

Changing the selected mapping while a draft is pending offers **Yes** to apply, **No** to discard that draft, or **Cancel** to keep editing. It does not silently throw away the draft. Editing a command or safety field immediately disarms controls; applying changes does not rearm them.

### Capture a key locally

Click **Press a key**, then press the desired game key while the capture field in the app has focus. Capture accepts only one unmodified letter, digit, arrow, or Space and updates the draft. Click **Apply command** afterward.

Capture is a local GUI interaction, not a global keyboard recorder. It stops when focus leaves the field, when you change tabs, or when you choose **Cancel** or press Escape. Shift, Ctrl, Alt, Windows/Meta keys and combinations are rejected, as are keys outside the allowed list. F8 remains the emergency stop while capture is active and also cancels capture. The key picker is available if you prefer not to capture a physical key. App keyboard shortcuts cannot perform their usual actions during capture; cancel capture first.

### Preview a mapping

Select a mapping and click **Preview**. Valid pending edits are applied first. The app switches the main session to **Preview · no keyboard input** and pauses it while checking the selected mapping separately. This check cannot send keyboard input, and connected Twitch messages cannot join it. A result beside the editor reports the permitted action or rejection reason; no game key is sent.

The simulated participant's role comes from **Try as** in the **Try a chat command** card on **Session**. For the default audience, **Moderator** should be accepted and **Viewer** rejected. Preview uses a synthetic user, so a Twitch user-ID allow/block list can also affect the result.

Each editor preview starts with fresh command history. It checks the mapping and participation rules but does not reproduce cooldowns, queue occupancy, or the rolling action budget accumulated during a real session. To test repeated commands and cooldown behavior, use **Session**, select **Preview · no keyboard input**, click **Start preview**, and use **Try command** repeatedly. The session simulator remembers recent commands, displays an acceptance or rejection explanation beside the form, and still sends no keys. Its command dropdown lists enabled mappings. You can also stay in Preview and choose **Connect Twitch to test real chat...** to receive your channel's messages without game input. Neither preview confirms actual game bindings or compatibility.

### Save and reopen profiles

**Save profile** writes the current profile to its selected file, asking for a location the first time. **Save a copy...** chooses another file; **Open profile...** reads an existing profile. The status line shows **Unsaved changes** or **Saved** and the selected filename. Only command mappings and safety settings are written.

Creating a new profile, opening another, or closing the app prompts when changes are unsaved. **Yes** saves them, **No** discards them, and **Cancel** keeps the current profile open. Canceling a save, a validation error, or a write failure prevents the requested replacement or close. A rejected profile file leaves the current profile and draft intact. Reopen a saved profile explicitly after restarting; authorization, selected windows, and armed state are never restored from it.

## Navigate and review your session

The bottom bar stays visible on every tab. **Start preview** starts a session without keyboard input; in Live mode it becomes **Arm game control · 3 s**. **Pause** stops the current session. The readiness message explains missing setup and provides links such as **Connect Twitch**, **Choose game**, or **Review reminder**. No keyboard shortcut starts or arms control, and a setup link never starts it automatically.

| Shortcut | Action |
| --- | --- |
| Ctrl+S | Save profile |
| Ctrl+Shift+S | Save a copy |
| Ctrl+O | Open a profile |
| Ctrl+1 / Ctrl+2 / Ctrl+3 / Ctrl+4 | Session / Controls & commands / Safety & access / Activity |
| Ctrl+Tab | Move to the next tab |
| Enter in the command list | Focus the selected command for editing |
| Enter in a command, hold, or cooldown field | Apply the local command draft |
| F8 | Stop control and cancel key capture |

Tab navigation scrolls focused controls into view. Scrolling over a numeric field or closed dropdown moves its page without changing the setting. The command list and Activity keep their own scrolling. During key capture, use **Cancel** or Escape before using the app shortcuts.

In **Safety & access**, **Who can play?** uses readable audience names, including **Moderators + you (recommended)** and **Only you**. **Set a comfortable pace** contains the shared delay, each viewer's delay, and the per-minute action limit. Open **Show advanced settings** to edit queue size, input expiry, and user-ID lists. Collapsing that section does not disable its restrictions: the applied list counts and queue settings remain visible beneath the audience selector. Pending edits must be applied and saved as usual.

**Activity** keeps the latest 200 approved events in memory. Events explain actions and rejection reasons without raw chat or sign-in details. New entries follow the bottom only when you are already reading there. Older entries eventually leave this bounded history; it is not a permanent audit file.

## Movement templates

| WASD command / key | Arrow-key command / key | Hold | Per-command cooldown |
| --- | --- | --- | --- |
| `!forward` / W | `!up` / Arrow Up | 150 ms | 300 ms |
| `!back` / S | `!down` / Arrow Down | 150 ms | 300 ms |
| `!left` / A | `!left` / Arrow Left | 150 ms | 300 ms |
| `!right` / D | `!right` / Arrow Right | 150 ms | 300 ms |
| `!jump` / Space | `!jump` / Space | 100 ms | 1,000 ms |

The built-in starter profile uses the WASD mappings. The default audience is **moderators and the broadcaster**. Select **Everyone in chat** under **Who can play?** only for open chat participation. Windows resolves each configured key through its keyboard mapping and sends a scan-code input. Verify your keyboard layout and game bindings in a controlled live test after reviewing the profile in Preview.

## Exact commands

Triggers begin with `!`, then a lowercase ASCII letter, then optional ASCII letters, digits, underscores, or hyphens. At most 24 characters follow `!`. Incoming commands are case-insensitive but otherwise exact: `!JUMP` is accepted for `!jump`; `!jump 500`, leading/trailing spaces, multiline text, Unicode lookalikes, and appended instructions are rejected.

Only letters A–Z, digits 0–9, arrows, and Space may be bound. An imported profile cannot enable Enter, Tab, Escape, function keys, modifiers, Windows keys, mouse movement, arbitrary text, chords, macros, or program execution. Only one key may be held at once; an additional release gap prevents consecutive actions becoming an indefinite hold.

## Settings

| Field | Default | Allowed values |
| --- | --- | --- |
| `schema_version` | 1 | Exactly 1 |
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
| Command `hold_ms` | 100 | 10–450 |
| Command `cooldown_ms` | 500 | 0–60,000; global/viewer limits still apply |
| Command `enabled` | true | Boolean |

Subscriber audience includes subscribers (including founder badges), moderators, and the broadcaster. VIP audience includes VIPs, moderators, and the broadcaster. Moderator audience includes moderators and the broadcaster. Broadcaster audience permits only the verified channel owner's numeric ID. An allow list *also* requires the selected audience role; a block list takes precedence over all roles, including the broadcaster. Use Twitch numeric user IDs, not display names.

All rate checks apply before queuing, so rejected input cannot reserve execution slots. The rolling minute budget counts admitted actions, including those that later expire. In the main session engine, cooldowns and replay history survive disarming and profile changes, including in Preview mode. The editor's isolated one-shot preview has separate, fresh policy history on each click. No queue survives disarming, disconnecting, editing, or emergency stop.

## File format example (optional)

Use the GUI to create and edit profiles. This example documents the exported format for people who also want to inspect files or keep them in version control.

```json
{
  "schema_version": 1,
  "name": "Movement test",
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
    {"trigger": "!jump", "key": "space", "hold_ms": 100, "cooldown_ms": 1000, "enabled": true}
  ]
}
```

Files are limited to 64 KiB of UTF-8 JSON. Unknown fields, duplicate object keys, duplicate triggers, duplicate IDs, invalid types, unsupported versions, and unsafe/out-of-range values are rejected. Profile saving replaces the chosen file atomically after validation.
