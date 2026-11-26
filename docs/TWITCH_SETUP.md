# Connect your own Twitch channel

The app requires a **Client ID from your own public Twitch application**. It does
not ship with anyone's Client ID, client secret, access token or refresh token.
The authorization screen opens on Twitch; you never enter your Twitch password
into this app. A Client ID identifies an application and is not an account password.

## 1. Create your Twitch application

1. Sign in to the [Twitch developer console](https://dev.twitch.tv/console/apps)
   using the broadcaster account whose chat should control the game.
2. If required, verify the account email and enable two-factor authentication.
3. Register an application with a unique name, such as your channel name followed
   by “Chat Control.” Choose the appropriate chat application category.
4. Select **Public** as the client type. The app uses Twitch's device authorization
   flow, which supports public desktop applications without a client secret.
5. If the registration form requires an OAuth redirect URL, enter
   `http://localhost`. The device flow used here does not use a redirect, open a
   local server, or send credentials to that URL.
6. Copy the application's **Client ID** into Twitch Chat Control. Do not generate
   or enter a client secret; the app has no client-secret field.

Twitch's registration page describes the general registration flow; its token
documentation specifically recommends public clients for open platforms such as
Windows. If you already have a confidential application, create a separate public
application for this app instead of sharing its client credentials with another
application.

## 2. Authorize the broadcaster

1. Enter your channel login, or its `https://www.twitch.tv/your_channel` URL.
2. Select **Connect**. The app requests only `user:read:chat`.
3. Visit the Twitch activation address shown in the app and enter the temporary
   device code. The app passes the code separately, so it is not placed in a
   browser-launch URL or command line.
4. Check that Twitch shows the expected application and the broadcaster account
   matching the channel you entered. Approve read-chat access.
5. Return to the app. The app verifies the token's Client ID, account login, user ID,
   expiry and exact read-chat permission before creating the chat subscription.

The initial version intentionally reads **the authorized broadcaster's own
channel**. Authorizing a different viewer or bot account is rejected. A Twitch
connection does not enable game control; review commands in preview mode and arm
control locally when ready.

Keep the temporary device code off your stream. Authorize before making the app's
connection view visible to viewers. The app does not log the code or place it in a
profile or build.

## Session behavior

- Authorization tokens are kept in process memory only. Closing or disconnecting
  the app discards its references to them. Python cannot guarantee that every
  in-memory copy is overwritten; no token file or credential cache is created.
- The app validates authorization at connection time and at least every 50
  minutes, earlier than Twitch's required hourly interval. It refreshes expiring
  tokens using the public-client refresh flow and validates the replacement.
  Scheduled validation and refresh disarm game control before contacting Twitch;
  after a successful check, rearm locally when ready. This avoids accepting game
  actions while authorization validity is being checked.
- Chat uses a TLS EventSub WebSocket and a read-only `channel.chat.message`
  subscription. There is no outgoing-chat permission or local network control
  endpoint.
- Network loss, missed keepalives, authorization revocation, or a Twitch server
  reconnect request ends the connection. Click **Connect** again and explicitly
  rearm locally. It does not automatically restore control after an interruption.
- Event validation checks the subscription, socket session, broadcaster and
  chatter IDs. Commands relayed from another channel through Shared Chat are
  ignored. Moderator/subscriber/VIP status comes from Twitch's badges; broadcaster
  status is checked against the immutable broadcaster user ID.
- Events more than five seconds old, events over two seconds in the future,
  malformed events and duplicate deliveries are ignored. Keep the Windows clock
  synchronized. Replay tracking and incoming buffers are bounded; overload drops
  input instead of building a long delayed command queue.
  The safety engine also counts the event's transport age against its shorter
  command expiry and arming time; receiving buffered data never makes it fresh.
- Connection errors shown in the app use sanitized messages. Server error bodies,
  authorization headers, token values and WebSocket debug frames are not logged.
- The app connects directly to fixed Twitch HTTPS/WSS endpoints and does not
  honor environment-variable proxies or follow HTTP redirects. Networks that
  require a custom proxy are not supported by this first version.

Disconnecting locally ends this app's session but does not remove your consent at
Twitch. To revoke the authorization, open
[Twitch Connections](https://www.twitch.tv/settings/connections), find your
application under **Other Connections**, and disconnect it there.

## Troubleshooting

| Symptom | What to check |
| --- | --- |
| Client ID rejected | Copy the Client ID, without quotes or spaces, from your own public application's Manage page. |
| Authorization rejected | Use the selected channel's broadcaster account; confirm the application is Public and the requested permission is read chat. |
| Unexpected permissions | This app accepts only `user:read:chat`. Revoke any old authorization for this application and authorize again, or use a dedicated new public application. |
| Code expired | Click Connect for a new temporary code. |
| Connected, but no game actions | Connection never arms control. Check preview/armed state, configured commands, viewer permissions, cooldowns and selected foreground game. |
| Chat unexpectedly disconnects | Check Internet access, firewall rules and account authorization, then reconnect. The app deliberately requires local rearming after interruptions. |
| Recent messages ignored | Check Windows clock synchronization and ensure the messages originate in this channel rather than another Shared Chat channel. |

## Official implementation references

Reviewed 2026-10-30:

- [Registering your application](https://dev.twitch.tv/docs/authentication/register-app/)
- [Device code grant flow and public clients](https://dev.twitch.tv/docs/authentication/getting-tokens-oauth/#device-code-grant-flow)
- [Validating tokens](https://dev.twitch.tv/docs/authentication/validate-tokens/)
- [EventSub WebSocket lifecycle, keepalives, revocation and reconnect](https://dev.twitch.tv/docs/eventsub/handling-websocket-events/)
- [Channel chat message permissions and payload](https://dev.twitch.tv/docs/eventsub/eventsub-subscription-types/#channelchatmessage)

Offline tests validate protocol and authorization boundaries with synthetic data.
An end-to-end live Twitch authorization requires the streamer's own Client ID and
account consent; no account credentials are included in the project or test suite.
