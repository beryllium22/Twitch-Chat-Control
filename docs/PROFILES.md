# Command profiles

Profiles contain only gameplay commands and participation/rate settings. They never contain credentials, Twitch authorization, a selected process/window, or a live/armed flag. Importing or editing a profile disarms control and requires local review and rearming.

## Starter profile

| Chat command | Game key | Hold | Per-command cooldown |
| --- | --- | --- | --- |
| `!forward` | W | 150 ms | 300 ms |
| `!back` | S | 150 ms | 300 ms |
| `!left` | A | 150 ms | 300 ms |
| `!right` | D | 150 ms | 300 ms |
| `!jump` | Space | 100 ms | 1,000 ms |

The default audience is **moderators and the broadcaster**. Select Everyone locally for open chat participation. Windows resolves each configured key through its keyboard mapping and sends a scan-code input. Verify your keyboard layout and game bindings in a controlled live test after reviewing the profile in Preview.

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

All rate checks apply before queuing, so rejected input cannot reserve execution slots. The rolling minute budget counts admitted actions, including those that later expire. Cooldowns and replay history survive disarming and profile changes. Preview also follows these limits. No queue survives disarming, disconnecting, editing, or emergency stop.

## Example

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
