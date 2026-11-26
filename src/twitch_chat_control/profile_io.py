"""Bounded JSON profile I/O. Profiles have no authentication fields."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from .models import Profile

MAX_PROFILE_BYTES = 65_536


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate profile fields are not allowed.")
        result[key] = value
    return result


def load_profile(path: str | Path) -> Profile:
    with Path(path).open("rb") as handle:
        raw = handle.read(MAX_PROFILE_BYTES + 1)
    if len(raw) > MAX_PROFILE_BYTES:
        raise ValueError("Profile exceeds the 64 KiB limit.")
    try:
        data = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object)
    except (UnicodeError, json.JSONDecodeError, RecursionError) as exc:
        raise ValueError("Profile must be a valid UTF-8 JSON object.") from exc
    return Profile.from_dict(data)


def save_profile(profile: Profile, path: str | Path) -> None:
    # Revalidate the allowlisted schema before serializing. Tokens, channel
    # authorization, window identity, and armed state are never exported.
    data = Profile.from_dict(profile.to_dict()).to_dict()
    raw = (json.dumps(data, indent=2, ensure_ascii=True) + "\n").encode("utf-8")
    if len(raw) > MAX_PROFILE_BYTES:
        raise ValueError("Profile exceeds the 64 KiB limit.")
    destination = Path(path)
    fd, temporary = tempfile.mkstemp(prefix=".tcc-profile-", suffix=".tmp", dir=destination.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
