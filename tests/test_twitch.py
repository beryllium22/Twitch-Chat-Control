"""Offline protocol-boundary tests. All authorization values are synthetic."""

import io
import json
import time
import unittest
from datetime import datetime, timezone
from unittest.mock import patch
from urllib.error import HTTPError

from twitch_chat_control.twitch import (
    ACTIVATE_URL,
    DEVICE_URL,
    MAX_FRAME_BYTES,
    SCOPE,
    SUBSCRIBE_URL,
    TOKEN_URL,
    VALIDATE_URL,
    WEBSOCKET_URL,
    EventParser,
    TwitchClient,
    TwitchError,
    _Cancelled,
    _device_prompt,
    _Http,
    _HttpFailure,
    _NoRedirect,
    normalize_channel,
)


def notification(wall=1_800_000_000.0):
    return {
        "metadata": {
            "message_id": "delivery-1",
            "message_type": "notification",
            "message_timestamp": datetime.fromtimestamp(wall, timezone.utc).isoformat(),
            "subscription_type": "channel.chat.message",
            "subscription_version": "1",
        },
        "payload": {
            "subscription": {
                "id": "subscription-1",
                "type": "channel.chat.message",
                "version": "1",
                "status": "enabled",
                "condition": {"broadcaster_user_id": "123", "user_id": "123"},
                "transport": {"method": "websocket", "session_id": "session-1"},
            },
            "event": {
                "broadcaster_user_id": "123",
                "chatter_user_id": "456",
                "chatter_user_login": "viewer",
                "message_id": "chat-1",
                "message": {"text": "!jump"},
                "badges": [{"set_id": "moderator", "id": "1"}],
                "source_broadcaster_user_id": None,
            },
        },
    }


def token_response():
    return {
        "access_token": "synthetic-test-access-value",
        "refresh_token": "synthetic-test-refresh-value",
        "expires_in": 14_400,
        "token_type": "bearer",
        "scope": [SCOPE],
    }


def validate_response():
    return {
        "client_id": "syntheticClientId123",
        "login": "streamer",
        "user_id": "123",
        "scopes": [SCOPE],
        "expires_in": 14_000,
    }


class FakeHttp:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def request(self, url, **kwargs):
        self.calls.append((url, kwargs))
        value = self.responses.pop(0)
        if isinstance(value, Exception):
            raise value
        return value


class FakeSocket:
    def __init__(self, frames):
        self.frames = list(frames)
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.closed = True

    def recv(self, timeout):
        value = self.frames.pop(0)
        if isinstance(value, Exception):
            raise value
        return json.dumps(value)


def welcome():
    return {
        "metadata": {"message_type": "session_welcome"},
        "payload": {"session": {"id": "session-1", "status": "connected", "keepalive_timeout_seconds": 10}},
    }


class ParserTests(unittest.TestCase):
    def setUp(self):
        self.parser = EventParser("123", "session-1", "subscription-1")
        self.wall = 1_800_000_000.0

    def parse(self, frame):
        return self.parser.parse(frame, now_wall=self.wall, now_mono=100.0)

    def test_authenticated_twitch_badges_and_ids(self):
        message = self.parse(notification())
        self.assertEqual(message.user_id, "456")
        self.assertEqual(message.roles, frozenset({"moderator"}))
        self.assertEqual(message.text, "!jump")
        self.assertNotIn("!jump", repr(message))

    def test_broadcaster_role_comes_from_id_not_badge_or_name(self):
        frame = notification()
        frame["payload"]["event"]["chatter_user_login"] = "streamer"
        frame["payload"]["event"]["badges"] = [{"set_id": "broadcaster"}]
        self.assertNotIn("broadcaster", self.parse(frame).roles)
        self.parser = EventParser("123", "session-1", "subscription-1")
        frame["payload"]["event"]["chatter_user_id"] = "123"
        self.assertIn("broadcaster", self.parse(frame).roles)

    def test_rejects_other_channel_and_shared_chat_sources(self):
        for field in ("broadcaster_user_id", "source_broadcaster_user_id"):
            with self.subTest(field=field):
                frame = notification()
                frame["payload"]["event"][field] = "999"
                self.assertIsNone(self.parse(frame))

    def test_normal_viewer_has_no_elevated_roles(self):
        frame = notification()
        frame["payload"]["event"]["badges"] = []
        self.assertEqual(self.parse(frame).roles, frozenset())

    def test_exact_subscription_transport_and_identity(self):
        paths = [
            ("metadata", "subscription_type"),
            ("metadata", "subscription_version"),
            ("payload", "subscription", "id"),
            ("payload", "subscription", "type"),
            ("payload", "subscription", "version"),
            ("payload", "subscription", "status"),
            ("payload", "subscription", "condition", "user_id"),
            ("payload", "subscription", "condition", "broadcaster_user_id"),
            ("payload", "subscription", "transport", "method"),
            ("payload", "subscription", "transport", "session_id"),
        ]
        for path in paths:
            with self.subTest(path=path):
                frame = notification()
                current = frame
                for key in path[:-1]:
                    current = current[key]
                current[path[-1]] = "wrong"
                self.assertIsNone(self.parse(frame))

    def test_stale_future_naive_and_invalid_timestamps(self):
        for timestamp in (
            datetime.fromtimestamp(self.wall - 6, timezone.utc).isoformat(),
            datetime.fromtimestamp(self.wall + 3, timezone.utc).isoformat(),
            "2027-01-15T08:00:00",
            "never",
            123,
            None,
        ):
            with self.subTest(timestamp=timestamp):
                frame = notification()
                frame["metadata"]["message_timestamp"] = timestamp
                self.assertIsNone(self.parse(frame))

    def test_deduplicates_event_and_chat_ids_independently(self):
        frame = notification()
        self.assertIsNotNone(self.parse(frame))
        self.assertIsNone(self.parse(frame))
        frame["metadata"]["message_id"] = "other-delivery"
        self.assertIsNone(self.parse(frame))
        frame["metadata"]["message_id"] = "delivery-1"
        frame["payload"]["event"]["message_id"] = "other-chat"
        self.assertIsNone(self.parse(frame))

    def test_transport_delay_is_preserved_for_engine_ttl_and_arm_epoch(self):
        frame = notification(self.wall - 0.4)
        message = self.parse(frame)
        self.assertAlmostEqual(message.received_at, 99.6, places=5)

    def test_overload_does_not_evict_live_replay_guards(self):
        frame = notification()
        self.assertIsNotNone(self.parse(frame))
        for index in range(8190):
            self.parser._seen[f"other:{index}"] = 100.0
        new_frame = notification()
        new_frame["metadata"]["message_id"] = "new-envelope"
        new_frame["payload"]["event"]["message_id"] = "new-chat"
        self.assertIsNone(self.parse(new_frame))
        self.assertIsNone(self.parse(frame))
        self.assertEqual(len(self.parser._seen), 8192)

    def test_malformed_or_control_character_chat_is_ignored(self):
        for content in ("", "!jump\n!run", "!jump\x00", "a" * 501, ["!jump"]):
            with self.subTest(content=str(content)[:10]):
                frame = notification()
                frame["payload"]["event"]["message"]["text"] = content
                self.assertIsNone(self.parse(frame))
        for frame in ({}, {"metadata": []}, {"payload": None}, [], None):
            self.assertIsNone(self.parse(frame))


class AuthorizationTests(unittest.TestCase):
    def make_client(self):
        self.statuses = []
        self.prompts = []
        return TwitchClient(
            "syntheticClientId123",
            "streamer",
            lambda _: None,
            lambda *a: self.statuses.append(a),
            lambda *a: self.prompts.append(a),
        )

    def test_normalizes_only_twitch_channel_urls(self):
        for value in (
            "Streamer",
            "#streamer",
            "https://www.twitch.tv/streamer/",
            "https://twitch.tv/streamer",
        ):
            self.assertEqual(normalize_channel(value), "streamer")
        for value in (
            "https://twitch.tv.attacker.test/streamer",
            "https://twitch.tv@attacker.test/streamer",
            "http://twitch.tv/streamer",
            "https://twitch.tv/streamer?token=x",
            "streamer/other",
            "",
            "💜",
        ):
            with self.subTest(value=value), self.assertRaises(ValueError):
                normalize_channel(value)

    def test_activation_url_code_is_separate_and_host_is_pinned(self):
        uri, code = _device_prompt(
            {
                "user_code": "TESTCODE",
                "verification_uri": "https://www.twitch.tv/activate?public=true&device-code=TESTCODE",
            }
        )
        self.assertEqual(uri, ACTIVATE_URL)
        self.assertEqual(code, "TESTCODE")
        self.assertNotIn(code, uri)
        for bad_uri in (
            "https://www.twitch.tv.attacker.test/activate",
            "http://www.twitch.tv/activate",
            "https://www.twitch.tv/activate?device-code=OTHER",
            "https://www.twitch.tv/activate?redirect_uri=https://attacker.test",
            "https://www.twitch.tv:443/activate",
            "https://user@www.twitch.tv/activate",
        ):
            with self.subTest(uri=bad_uri), self.assertRaises(TwitchError):
                _device_prompt({"user_code": "TESTCODE", "verification_uri": bad_uri})

    def test_rejects_other_account_client_and_extra_permissions(self):
        for field, value in (
            ("login", "someone_else"),
            ("client_id", "otherClient123"),
            ("user_id", "bad"),
            ("scopes", [SCOPE, "user:write:chat"]),
            ("expires_in", True),
        ):
            with self.subTest(field=field):
                client = self.make_client()
                data = validate_response()
                data[field] = value
                client._http = FakeHttp([data])
                client._set_tokens(token_response())
                with self.assertRaises(TwitchError):
                    client._validate()

    def test_device_flow_never_sends_a_client_secret(self):
        client = self.make_client()
        client._http = FakeHttp(
            [
                {
                    "user_code": "TESTCODE",
                    "device_code": "syntheticDeviceCode123",
                    "verification_uri": ACTIVATE_URL,
                    "interval": 1,
                    "expires_in": 100,
                },
                token_response(),
                validate_response(),
            ]
        )
        with patch.object(client._stop, "wait", return_value=False):
            client._authorize()
        calls = client._http.calls
        self.assertEqual([url for url, _ in calls], [DEVICE_URL, TOKEN_URL, VALIDATE_URL])
        self.assertEqual(calls[1][1]["form"]["scopes"], SCOPE)
        self.assertEqual(self.prompts, [(ACTIVATE_URL, "TESTCODE")])
        self.assertNotIn("client_secret", repr(calls))
        for url, _ in calls:
            self.assertNotIn("synthetic-test", url)

    def test_device_poll_pending_and_slow_down_are_respected(self):
        client = self.make_client()
        client._http = FakeHttp(
            [
                {
                    "user_code": "TESTCODE",
                    "device_code": "syntheticDeviceCode123",
                    "verification_uri": ACTIVATE_URL,
                    "interval": 2,
                    "expires_in": 100,
                },
                _HttpFailure(400, "authorization_pending"),
                _HttpFailure(400, "slow_down"),
                token_response(),
                validate_response(),
            ]
        )
        with patch.object(client._stop, "wait", return_value=False) as wait:
            client._authorize()
        self.assertEqual([call.args[0] for call in wait.call_args_list], [2, 2, 7])

    def test_cancelled_http_result_cannot_install_new_tokens(self):
        client = self.make_client()
        client._stop.set()
        with self.assertRaises(_Cancelled):
            client._set_tokens(token_response())
        self.assertEqual(client._access, "")
        self.assertEqual(client._refresh, "")

    def test_refresh_rotates_memory_only_tokens_and_revalidates(self):
        client = self.make_client()
        client._set_tokens(token_response())
        client._expires_at = time.monotonic() + 5
        data = token_response()
        data["refresh_token"] = "rotated-synthetic-refresh-value"
        client._http = FakeHttp([data, validate_response()])
        client._maintain_authorization()
        self.assertEqual(client._refresh, "rotated-synthetic-refresh-value")
        self.assertEqual(client._http.calls[0][1]["form"]["grant_type"], "refresh_token")
        self.assertNotIn("client_secret", client._http.calls[0][1]["form"])
        self.assertEqual(client._http.calls[1][0], VALIDATE_URL)

    def test_stop_clears_credentials_and_immediately_notifies(self):
        client = self.make_client()
        client._set_tokens(token_response())
        client.stop()
        self.assertEqual((client._access, client._refresh), ("", ""))
        self.assertEqual(self.statuses[-1][0], "disconnected")

    def test_exception_details_never_escape_into_status(self):
        client = self.make_client()
        client._set_tokens(token_response())
        with patch.object(
            client, "_authorize", side_effect=RuntimeError("synthetic-secret-that-must-not-escape")
        ):
            client._run()
        self.assertNotIn("synthetic-secret", repr(self.statuses))
        self.assertEqual((client._access, client._refresh), ("", ""))
        self.assertEqual(self.statuses[-1][0], "disconnected")


class SocketTests(unittest.TestCase):
    def make_client(self, chats):
        client = TwitchClient(
            "syntheticClientId123", "streamer", chats.append, lambda *_: None, lambda *_: None
        )
        client._set_tokens(token_response())
        client._validated_at = time.monotonic()
        client._user_id = "123"
        client._http = FakeHttp([{"data": [{"id": "subscription-1", "status": "enabled"}]}])
        return client

    def test_authenticated_socket_subscribes_to_own_channel_and_reads_chat(self):
        chats = []
        client = self.make_client(chats)
        socket = FakeSocket(
            [
                welcome(),
                notification(time.time()),
                {"metadata": {"message_type": "revocation"}, "payload": {}},
            ]
        )
        with patch("websockets.sync.client.connect", return_value=socket) as connect:
            with self.assertRaises(TwitchError):
                client._listen()
        self.assertEqual(len(chats), 1)
        args = connect.call_args
        self.assertEqual(args.args, (WEBSOCKET_URL,))
        self.assertIsNone(args.kwargs["ping_interval"])
        self.assertIsNone(args.kwargs["proxy"])
        self.assertEqual(args.kwargs["max_size"], MAX_FRAME_BYTES)
        self.assertEqual(client._http.calls[0][0], SUBSCRIBE_URL)
        self.assertEqual(
            client._http.calls[0][1]["body"]["condition"], {"broadcaster_user_id": "123", "user_id": "123"}
        )
        self.assertTrue(socket.closed)

    def test_reconnect_requires_local_action_and_does_not_follow_remote_url(self):
        client = self.make_client([])
        socket = FakeSocket(
            [
                welcome(),
                {
                    "metadata": {"message_type": "session_reconnect"},
                    "payload": {"session": {"reconnect_url": "wss://attacker.test/"}},
                },
            ]
        )
        with patch("websockets.sync.client.connect", return_value=socket) as connect:
            with self.assertRaisesRegex(TwitchError, "Connect again"):
                client._listen()
        self.assertEqual(connect.call_count, 1)
        self.assertTrue(socket.closed)

    def test_invalid_or_oversized_websocket_frames_close_session(self):
        for raw in (b"{}", "[1]", "invalid", "x" * (MAX_FRAME_BYTES + 1), '{"metadata":null,"payload":{}}'):
            with self.subTest(raw=str(raw)[:20]), self.assertRaises(TwitchError):
                TwitchClient._frame(raw)

    def test_websocket_failures_are_sanitized_and_credentials_cleared(self):
        client = self.make_client([])
        statuses = []
        client._on_status = lambda *a: statuses.append(a)
        with (
            patch.object(client, "_authorize"),
            patch.object(client, "_listen", side_effect=RuntimeError("synthetic-secret")),
        ):
            client._run()
        self.assertEqual(statuses[-1][0], "disconnected")
        self.assertNotIn("synthetic-secret", repr(statuses))
        self.assertEqual((client._access, client._refresh), ("", ""))

    def test_keepalive_timeout_fails_closed(self):
        client = self.make_client([])
        socket = FakeSocket([welcome()])
        with (
            patch("websockets.sync.client.connect", return_value=socket),
            patch.object(client, "_maintain_authorization"),
            patch("twitch_chat_control.twitch.time.monotonic", side_effect=[100.0, 111.01]),
        ):
            with self.assertRaisesRegex(TwitchError, "stopped responding"):
                client._listen()
        self.assertTrue(socket.closed)

    def test_revalidation_failure_disarms_before_the_http_request(self):
        statuses = []
        client = self.make_client([])
        client._on_status = lambda *a: statuses.append(a)
        client._validated_at = time.monotonic() - 4000
        client._http = FakeHttp([_HttpFailure(401)])
        with self.assertRaises(TwitchError):
            client._maintain_authorization()
        self.assertEqual(statuses[0][0], "connecting")
        self.assertNotIn("connected", [kind for kind, _ in statuses])


class HttpTests(unittest.TestCase):
    def test_only_fixed_https_twitch_endpoints_are_allowed(self):
        http = _Http()
        for url in (
            "http://id.twitch.tv/oauth2/token",
            "https://attacker.test/",
            TOKEN_URL + "?access_token=secret",
        ):
            with self.subTest(url=url), self.assertRaises(TwitchError):
                http.request(url)

    def test_redirects_never_forward_the_request(self):
        self.assertIsNone(
            _NoRedirect().redirect_request(None, None, 307, "redirect", {}, "https://attacker.test")
        )

    def test_http_error_body_and_exception_are_not_revealed(self):
        http = _Http()
        with patch.object(
            http._opener,
            "open",
            side_effect=HTTPError(
                TOKEN_URL, 400, "synthetic-secret", {}, io.BytesIO(b'{"message":"synthetic-secret"}')
            ),
        ):
            with self.assertRaises(_HttpFailure) as error:
                http.request(TOKEN_URL, form={"client_id": "syntheticClientId123"})
        self.assertNotIn("synthetic-secret", str(error.exception))
        self.assertEqual(error.exception.code, "request_failed")


if __name__ == "__main__":
    unittest.main()
