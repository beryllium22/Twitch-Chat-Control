"""Read-only Twitch chat over EventSub; all authorization lives in memory.

There is no general-purpose URL input, local HTTP listener, outgoing chat API,
client secret, or token persistence in this module. Callbacks run on the worker
thread and must be short and thread-safe. A disconnect always requires a fresh
local Connect action; it never restores game control.
"""

from __future__ import annotations

import json
import logging
import re
import ssl
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urlsplit
from urllib.request import (
    HTTPRedirectHandler,
    HTTPSHandler,
    ProxyHandler,
    Request,
    build_opener,
)

SCOPE = "user:read:chat"
DEVICE_URL = "https://id.twitch.tv/oauth2/device"
TOKEN_URL = "https://id.twitch.tv/oauth2/token"
VALIDATE_URL = "https://id.twitch.tv/oauth2/validate"
SUBSCRIBE_URL = "https://api.twitch.tv/helix/eventsub/subscriptions"
WEBSOCKET_URL = "wss://eventsub.wss.twitch.tv/ws"
ACTIVATE_URL = "https://www.twitch.tv/activate?public=true"
MAX_FRAME_BYTES = 65_536
MAX_EVENT_AGE_SECONDS = 5.0
VALIDATE_EVERY_SECONDS = 3_000.0  # Always sooner than Twitch's one-hour requirement.
_LOGIN = re.compile(r"[a-z0-9_]{1,25}\Z", re.ASCII)
_ID = re.compile(r"[1-9][0-9]{0,31}\Z", re.ASCII)
_OPAQUE = re.compile(r"[A-Za-z0-9_-]{1,128}\Z", re.ASCII)


@dataclass(frozen=True, slots=True)
class ChatMessage:
    message_id: str
    user_id: str
    login: str
    text: str = field(repr=False)
    roles: frozenset[str]
    channel_id: str
    received_at: float


class TwitchError(Exception):
    """An intentionally sanitized, user-presentable failure."""


class _Cancelled(Exception):
    pass


class _HttpFailure(TwitchError):
    def __init__(self, status: int, code: str = "request_failed") -> None:
        super().__init__("Twitch could not complete the request. Reconnect to try again.")
        self.status = status
        self.code = code


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class _Http:
    """Only fixed Twitch TLS endpoints; secrets cannot cross a redirect."""

    def __init__(self) -> None:
        self._opener = build_opener(
            ProxyHandler({}), _NoRedirect(), HTTPSHandler(context=ssl.create_default_context())
        )

    def request(
        self,
        url: str,
        *,
        form: dict[str, str] | None = None,
        body: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        if url not in {DEVICE_URL, TOKEN_URL, VALIDATE_URL, SUBSCRIBE_URL}:
            raise TwitchError("The Twitch request destination was rejected.")
        if form is not None and body is not None:
            raise TwitchError("The Twitch request format was rejected.")
        safe_headers = dict(headers or {})
        data = None
        if form is not None:
            data = urlencode(form).encode("ascii")
            safe_headers["Content-Type"] = "application/x-www-form-urlencoded"
        if body is not None:
            data = json.dumps(body, separators=(",", ":")).encode("utf-8")
            safe_headers["Content-Type"] = "application/json"
        request = Request(url, data=data, headers=safe_headers)
        try:
            with self._opener.open(request, timeout=5) as response:
                raw = response.read(MAX_FRAME_BYTES + 1)
        except HTTPError as error:
            # Never propagate HTTP bodies, headers, tokens or exception reprs.
            code = "request_failed"
            status = error.code
            try:
                raw_error = error.read(MAX_FRAME_BYTES + 1)
                if len(raw_error) <= MAX_FRAME_BYTES:
                    decoded = json.loads(raw_error)
                    candidate = decoded.get("message", decoded.get("error"))
                    if candidate in {"authorization_pending", "slow_down", "access_denied", "expired_token"}:
                        code = candidate
            except (ValueError, TypeError, AttributeError):
                pass
            finally:
                error.close()
            raise _HttpFailure(status, code) from None
        except (URLError, OSError, TimeoutError, ValueError):
            raise TwitchError("Cannot reach Twitch securely. Check the connection and try again.") from None
        if len(raw) > MAX_FRAME_BYTES:
            raise TwitchError("Twitch returned an oversized response. Connection stopped.")
        try:
            result = json.loads(raw)
        except (ValueError, TypeError):
            raise TwitchError("Twitch returned an invalid response. Connection stopped.") from None
        if not isinstance(result, dict):
            raise TwitchError("Twitch returned an invalid response. Connection stopped.")
        return result


def normalize_channel(value: str) -> str:
    """Accept a login or a plain twitch.tv channel URL, never an arbitrary URL."""
    value = value.strip()
    if "://" in value:
        parsed = urlsplit(value)
        if (
            parsed.scheme != "https"
            or parsed.netloc not in {"twitch.tv", "www.twitch.tv"}
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("Enter your Twitch channel name or its https://www.twitch.tv/ URL.")
        value = parsed.path.strip("/")
    value = value.removeprefix("#").lower()
    if not _LOGIN.fullmatch(value):
        raise ValueError("A Twitch channel name must contain 1–25 letters, numbers or underscores.")
    return value


def _positive_seconds(value: Any, *, maximum: int, name: str) -> int:
    if type(value) is not int or not 1 <= value <= maximum:
        raise TwitchError(f"Twitch returned an invalid {name}. Connection stopped.")
    return value


def _opaque(value: Any) -> bool:
    return isinstance(value, str) and _OPAQUE.fullmatch(value) is not None


def _device_prompt(data: dict[str, Any]) -> tuple[str, str]:
    """Validate Twitch's activation URL but strip its code before handing to UI."""
    code = data.get("user_code")
    uri = data.get("verification_uri")
    if not isinstance(code, str) or not re.fullmatch(r"[A-Z0-9-]{4,32}", code, re.ASCII):
        raise TwitchError("Twitch returned an invalid activation code.")
    if not isinstance(uri, str):
        raise TwitchError("Twitch returned an invalid activation address.")
    parsed = urlsplit(uri)
    query = parse_qs(parsed.query, keep_blank_values=True)
    if (
        parsed.scheme != "https"
        or parsed.netloc not in {"www.twitch.tv", "twitch.tv"}
        or parsed.path != "/activate"
        or parsed.fragment
        or set(query) - {"public", "device-code"}
        or ("public" in query and query["public"] != ["true"])
        or ("device-code" in query and query["device-code"] != [code])
    ):
        raise TwitchError("Twitch returned an untrusted activation address.")
    return ACTIVATE_URL, code


class EventParser:
    """Validate identity/transport/age and suppress duplicate delivered events."""

    def __init__(self, channel_id: str, session_id: str, subscription_id: str) -> None:
        self.channel_id = channel_id
        self.session_id = session_id
        self.subscription_id = subscription_id
        self._seen: OrderedDict[str, float] = OrderedDict()

    def parse(
        self,
        data: dict[str, Any],
        *,
        now_wall: float | None = None,
        now_mono: float | None = None,
    ) -> ChatMessage | None:
        now_wall = time.time() if now_wall is None else now_wall
        now_mono = time.monotonic() if now_mono is None else now_mono
        try:
            metadata = data["metadata"]
            payload = data["payload"]
            subscription = payload["subscription"]
            event = payload["event"]
            condition = subscription["condition"]
            transport = subscription["transport"]
            if (
                metadata["message_type"] != "notification"
                or metadata["subscription_type"] != "channel.chat.message"
                or metadata["subscription_version"] != "1"
                or subscription["id"] != self.subscription_id
                or subscription["type"] != "channel.chat.message"
                or subscription["version"] != "1"
                or subscription["status"] != "enabled"
                or condition["broadcaster_user_id"] != self.channel_id
                or condition["user_id"] != self.channel_id
                or transport["method"] != "websocket"
                or transport["session_id"] != self.session_id
                or event["broadcaster_user_id"] != self.channel_id
                or event.get("source_broadcaster_user_id") not in (None, self.channel_id)
            ):
                return None
            timestamp = metadata["message_timestamp"]
            if not isinstance(timestamp, str) or len(timestamp) > 40:
                return None
            sent = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
            if sent.tzinfo is None:
                return None
            event_age = now_wall - sent.timestamp()
            if not -2.0 <= event_age <= MAX_EVENT_AGE_SECONDS:
                return None
            envelope_id = metadata["message_id"]
            message_id = event["message_id"]
            user_id = event["chatter_user_id"]
            login = event["chatter_user_login"]
            message = event["message"]["text"]
            if (
                not _opaque(envelope_id)
                or not _opaque(message_id)
                or not isinstance(user_id, str)
                or not _ID.fullmatch(user_id)
                or not isinstance(login, str)
                or not _LOGIN.fullmatch(login)
                or not isinstance(message, str)
                or not 1 <= len(message) <= 500
                or any(ord(c) < 32 or ord(c) == 127 for c in message)
            ):
                return None
            badges = event.get("badges", [])
            if not isinstance(badges, list) or len(badges) > 64:
                return None
            roles: set[str] = set()
            for badge in badges:
                if not isinstance(badge, dict):
                    return None
                badge_role = badge.get("set_id")
                if badge_role in {"moderator", "subscriber", "vip"}:
                    roles.add(badge_role)
                if badge_role == "founder":
                    roles.add("subscriber")
            # A display name or broadcaster badge cannot confer owner privilege.
            if user_id == self.channel_id:
                roles.add("broadcaster")
            while self._seen and next(iter(self._seen.values())) < now_mono - 10.0:
                self._seen.popitem(last=False)
            keys = ("envelope:" + envelope_id, "chat:" + message_id)
            if any(key in self._seen for key in keys):
                return None
            # On overload drop new input instead of evicting fresh replay guards.
            if len(self._seen) + 2 > 8192:
                return None
            for key in keys:
                self._seen[key] = now_mono
            return ChatMessage(
                message_id,
                user_id,
                login,
                message,
                frozenset(roles),
                self.channel_id,
                # Retain transport age when crossing into the monotonic safety
                # engine; buffered events must not become fresh on receipt.
                now_mono - max(0.0, event_age),
            )
        except (KeyError, TypeError, ValueError, AttributeError, OverflowError):
            return None


class TwitchClient:
    """One broadcaster-owned chat session; call start and stop from the local UI."""

    def __init__(
        self,
        client_id: str,
        channel: str,
        on_chat: Callable[[ChatMessage], None],
        on_status: Callable[[str, str], None],
        on_device: Callable[[str, str], None],
    ) -> None:
        client_id = client_id.strip()
        if not re.fullmatch(r"[A-Za-z0-9]{10,100}", client_id, re.ASCII):
            raise ValueError("Enter the Client ID from your own public Twitch application.")
        self.client_id = client_id
        self.channel = normalize_channel(channel)
        self._on_chat = on_chat
        self._on_status = on_status
        self._on_device = on_device
        self._http = _Http()
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._socket: Any = None
        self._access = ""
        self._refresh = ""
        self._expires_at = 0.0
        self._validated_at = 0.0
        self._user_id = ""

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        with self._lock:
            if self.running:
                raise RuntimeError("This Twitch connection is already running.")
            self._stop.clear()
            self._thread = threading.Thread(target=self._run, name="twitch-chat", daemon=True)
            self._thread.start()

    def stop(self) -> None:
        """Mark disconnected immediately; the worker exits within bounded I/O time."""
        with self._lock:
            self._stop.set()
            self._access = ""
            self._refresh = ""
        self._on_status("disconnected", "Disconnected. Game control must be armed locally again.")

    def _check_cancelled(self) -> None:
        if self._stop.is_set():
            raise _Cancelled()

    def _request(self, url: str, **kwargs: Any) -> dict[str, Any]:
        self._check_cancelled()
        result = self._http.request(url, **kwargs)
        self._check_cancelled()
        return result

    def _set_tokens(self, data: dict[str, Any]) -> None:
        access = data.get("access_token")
        refresh = data.get("refresh_token")
        if (
            not isinstance(access, str)
            or not re.fullmatch(r"[A-Za-z0-9_-]{10,512}", access, re.ASCII)
            or not isinstance(refresh, str)
            or not re.fullmatch(r"[A-Za-z0-9_-]{10,512}", refresh, re.ASCII)
            or not isinstance(data.get("token_type"), str)
            or data["token_type"].lower() != "bearer"
            or data.get("scope") != [SCOPE]
        ):
            raise TwitchError("Twitch returned credentials with unexpected permissions or format.")
        expires = _positive_seconds(data.get("expires_in"), maximum=86_400, name="token lifetime")
        with self._lock:
            self._check_cancelled()
            self._access, self._refresh = access, refresh
            self._expires_at = time.monotonic() + expires

    def _authorize(self) -> None:
        self._on_status("authorizing", "Authorize your own Twitch channel using the temporary device code.")
        data = self._request(DEVICE_URL, form={"client_id": self.client_id, "scopes": SCOPE})
        uri, code = _device_prompt(data)
        device_code = data.get("device_code")
        if not isinstance(device_code, str) or not re.fullmatch(
            r"[A-Za-z0-9_-]{10,512}", device_code, re.ASCII
        ):
            raise TwitchError("Twitch returned an invalid device authorization.")
        expires = _positive_seconds(data.get("expires_in"), maximum=3600, name="device lifetime")
        interval = _positive_seconds(data.get("interval"), maximum=60, name="poll interval")
        deadline = time.monotonic() + expires
        self._on_device(uri, code)
        while time.monotonic() < deadline:
            if self._stop.wait(min(interval, max(0.0, deadline - time.monotonic()))):
                raise _Cancelled()
            if time.monotonic() >= deadline:
                break
            try:
                result = self._request(
                    TOKEN_URL,
                    form={
                        "client_id": self.client_id,
                        "scopes": SCOPE,
                        "device_code": device_code,
                        "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                    },
                )
            except _HttpFailure as error:
                if error.status == 400 and error.code == "authorization_pending":
                    continue
                if error.status in {400, 429} and error.code == "slow_down":
                    interval = min(60, interval + 5)
                    continue
                raise TwitchError(
                    "Twitch authorization was declined, expired or unavailable. Connect again."
                ) from None
            self._set_tokens(result)
            self._validate()
            return
        raise TwitchError("The Twitch device code expired. Connect again for a new code.")

    def _validate(self) -> None:
        data = self._request(VALIDATE_URL, headers={"Authorization": "OAuth " + self._access})
        user_id = data.get("user_id")
        if (
            data.get("client_id") != self.client_id
            or data.get("login") != self.channel
            or data.get("scopes") != [SCOPE]
            or not isinstance(user_id, str)
            or not _ID.fullmatch(user_id)
            or (self._user_id and self._user_id != user_id)
        ):
            raise TwitchError(
                "Authorize the selected channel's broadcaster account with read-chat permission only."
            )
        expires = _positive_seconds(data.get("expires_in"), maximum=86_400, name="validated token lifetime")
        self._user_id = user_id
        self._expires_at = min(self._expires_at, time.monotonic() + expires)
        self._validated_at = time.monotonic()

    def _maintain_authorization(self) -> None:
        now = time.monotonic()
        if now >= self._expires_at - 60:
            self._on_status("connecting", "Refreshing Twitch authorization. Game control has been disarmed.")
            data = self._request(
                TOKEN_URL,
                form={
                    "client_id": self.client_id,
                    "grant_type": "refresh_token",
                    "refresh_token": self._refresh,
                },
            )
            self._set_tokens(data)
            self._validate()
            self._on_status("connected", "Chat authorization renewed. Rearm game control locally when ready.")
        elif now >= self._validated_at + VALIDATE_EVERY_SECONDS:
            self._on_status("connecting", "Checking Twitch authorization. Game control has been disarmed.")
            self._validate()
            self._on_status(
                "connected", "Chat authorization verified. Rearm game control locally when ready."
            )

    @staticmethod
    def _frame(raw: Any) -> dict[str, Any]:
        if not isinstance(raw, str) or len(raw.encode("utf-8")) > MAX_FRAME_BYTES:
            raise TwitchError("Twitch sent an unsupported event. Connection stopped.")
        try:
            value = json.loads(raw)
            if (
                not isinstance(value, dict)
                or not isinstance(value.get("metadata"), dict)
                or not isinstance(value.get("payload"), dict)
            ):
                raise ValueError
            return value
        except (ValueError, TypeError):
            raise TwitchError("Twitch sent an invalid event. Connection stopped.") from None

    def _subscribe(self, session_id: str) -> str:
        result = self._request(
            SUBSCRIBE_URL,
            headers={"Authorization": "Bearer " + self._access, "Client-Id": self.client_id},
            body={
                "type": "channel.chat.message",
                "version": "1",
                "condition": {"broadcaster_user_id": self._user_id, "user_id": self._user_id},
                "transport": {"method": "websocket", "session_id": session_id},
            },
        )
        try:
            items = result["data"]
            item = items[0]
            if len(items) != 1 or not _opaque(item["id"]) or item["status"] != "enabled":
                raise ValueError
            return item["id"]
        except (KeyError, IndexError, TypeError, ValueError):
            raise TwitchError("Twitch did not enable the chat subscription. Connection stopped.") from None

    def _listen(self) -> None:
        from websockets.sync.client import connect

        # Do not allow websocket debug logging to dump incoming chat text.
        quiet_logger = logging.Logger("twitch_chat_control.websocket", level=logging.CRITICAL + 1)
        quiet_logger.addHandler(logging.NullHandler())
        self._on_status("connecting", "Connecting to Twitch chat. Game control remains disarmed.")
        with connect(
            WEBSOCKET_URL,
            ssl=ssl.create_default_context(),
            proxy=None,
            compression=None,
            open_timeout=5,
            close_timeout=1,
            ping_interval=None,
            max_size=MAX_FRAME_BYTES,
            max_queue=8,
            logger=quiet_logger,
        ) as socket:
            self._socket = socket
            self._check_cancelled()
            welcome = self._frame(socket.recv(timeout=5))
            try:
                session = welcome["payload"]["session"]
                session_id = session["id"]
                if (
                    welcome["metadata"]["message_type"] != "session_welcome"
                    or not _opaque(session_id)
                    or session["status"] != "connected"
                ):
                    raise ValueError
                keepalive = _positive_seconds(
                    session["keepalive_timeout_seconds"], maximum=600, name="keepalive interval"
                )
            except (KeyError, TypeError, ValueError):
                raise TwitchError("Twitch did not establish a valid session. Connection stopped.") from None
            subscription_id = self._subscribe(session_id)
            parser = EventParser(self._user_id, session_id, subscription_id)
            last_healthy = time.monotonic()
            self._check_cancelled()
            self._on_status(
                "connected", "Chat connected. Preview first; arm game control locally when ready."
            )
            while not self._stop.is_set():
                self._maintain_authorization()
                now = time.monotonic()
                if now - last_healthy > keepalive + 1.0:
                    raise TwitchError("Twitch chat stopped responding. Game control has been disconnected.")
                try:
                    raw = socket.recv(timeout=min(0.25, max(0.01, keepalive + 1.0 - (now - last_healthy))))
                except TimeoutError:
                    continue
                self._check_cancelled()
                data = self._frame(raw)
                kind = data["metadata"].get("message_type")
                if kind == "session_keepalive":
                    last_healthy = time.monotonic()
                elif kind == "notification":
                    last_healthy = time.monotonic()
                    chat = parser.parse(data)
                    if chat is not None:
                        self._check_cancelled()
                        self._on_chat(chat)
                elif kind == "session_reconnect":
                    raise TwitchError("Twitch requested a new connection. Connect again, then rearm locally.")
                elif kind == "revocation":
                    raise TwitchError("Twitch revoked chat authorization. Connect again to authorize access.")
                else:
                    raise TwitchError("Twitch sent an unexpected session event. Connection stopped.")

    def _run(self) -> None:
        try:
            self._authorize()
            self._check_cancelled()
            self._listen()
        except _Cancelled:
            pass
        except TwitchError as error:
            if not self._stop.is_set():
                self._on_status("error", str(error))
        except Exception:
            # Deliberately do not interpolate/log exception details or tracebacks:
            # HTTP and websocket exceptions can contain authorization material.
            if not self._stop.is_set():
                self._on_status(
                    "error", "The Twitch connection ended unexpectedly. Check your connection and reconnect."
                )
        finally:
            self._socket = None
            self._access = ""
            self._refresh = ""
            self._user_id = ""
            self._on_status("disconnected", "Disconnected. Game control must be armed locally again.")
