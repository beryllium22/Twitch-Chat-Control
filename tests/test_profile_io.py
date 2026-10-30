import json

import pytest

from twitch_chat_control.models import Profile
from twitch_chat_control.profile_io import MAX_PROFILE_BYTES, load_profile, save_profile


def test_round_trip_exports_only_approved_profile_schema(tmp_path):
    path = tmp_path / "game.json"
    save_profile(Profile.default(), path)
    assert load_profile(path) == Profile.default()
    data = json.loads(path.read_text())
    assert set(data) == {
        "schema_version", "name", "audience", "global_cooldown_ms",
        "viewer_cooldown_ms", "max_actions_per_minute", "queue_size", "queue_ttl_ms",
        "allowed_user_ids", "blocked_user_ids", "commands",
    }
    assert not list(tmp_path.glob(".tcc-profile-*"))


@pytest.mark.parametrize("raw", [
    b'{"schema_version":1,"schema_version":1,"commands":[]}',
    b'{"schema_version":1,"commands":[{"trigger":"!go","key":"w","key":"s"}]}',
    b'{"schema_version":1,"commands":[],"access_token":"must-not-export"}',
    b'{"schema_version":1,"commands":[],"armed":true}',
    b'\xff\xfe',
    b'[' * 5000,
    b' ' * (MAX_PROFILE_BYTES + 1),
])
def test_malicious_or_oversized_profile_rejected(tmp_path, raw):
    path = tmp_path / "game.json"
    path.write_bytes(raw)
    with pytest.raises(ValueError):
        load_profile(path)
