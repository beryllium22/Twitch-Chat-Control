import math
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace

import pytest

from twitch_chat_control.engine import SafetyEngine
from twitch_chat_control.models import Command, Profile


class Clock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now


class Backend:
    target_selected = True
    safety_ready = True
    foreground = True
    modifiers = False
    fail_down = False
    fail_up = False
    fail_release = False

    def __init__(self):
        self.events = []
        self.pressed = set()
        self.live = False

    def is_target_foreground(self):
        return self.foreground

    def physical_modifiers_down(self):
        return self.modifiers

    def bind_live_session(self, live):
        self.live = live
        self.events.append(("bind", live))

    def key_down(self, key):
        if self.fail_down:
            raise RuntimeError("SHOULD_NOT_APPEAR_IN_LOGS")
        assert not self.pressed, "No overlapping keys"
        assert self.live and self.foreground and not self.modifiers
        self.pressed.add(key)
        self.events.append(("down", key))

    def key_up(self, key):
        if self.fail_up:
            raise RuntimeError("SHOULD_NOT_APPEAR_IN_LOGS")
        self.pressed.discard(key)
        self.events.append(("up", key))

    def release_all(self):
        self.events.append(("release_all",))
        if self.fail_release:
            raise RuntimeError("SHOULD_NOT_APPEAR_IN_LOGS")
        self.pressed.clear()


@dataclass(frozen=True)
class Message:
    message_id: str
    user_id: str
    login: str
    text: str
    roles: frozenset[str]
    channel_id: str
    received_at: float


@pytest.fixture
def harness():
    clock = Clock()
    backend = Backend()
    events = []
    engine = SafetyEngine(backend, events.append, clock)
    return engine, backend, clock, events


def msg(clock, ident="message-1", user="123", text="!forward", roles=frozenset({"moderator"})):
    return Message(ident, user, "viewer", text, roles, "999", clock())


def advance(engine, clock, seconds):
    end = clock.now + seconds
    while clock.now < end:
        clock.now = min(end, clock.now + 0.010)
        engine.tick()


def arm_live(engine):
    engine.set_connected(True)
    assert engine.arm(preview=False, delay_seconds=0)
    assert engine.snapshot().state == "armed"


def test_startup_is_disarmed_and_receiving_chat_cannot_arm(harness):
    engine, backend, clock, _ = harness
    assert engine.snapshot().state == "disarmed"
    assert engine.snapshot().preview
    engine.set_connected(True)
    assert not engine.submit(msg(clock)).accepted
    engine.tick()
    assert not backend.pressed and not backend.live


def test_preview_runs_offline_without_injecting(harness):
    engine, backend, clock, events = harness
    backend.target_selected = False
    backend.safety_ready = False
    assert engine.arm(preview=True)
    assert engine.submit(msg(clock)).accepted
    engine.tick()
    assert any(event.kind == "preview" and event.command == "!forward" for event in events)
    assert not any(event[0] in {"down", "up"} for event in backend.events)
    assert not backend.live


@pytest.mark.parametrize("flag,reason", [("target_selected", "target_not_selected"), ("safety_ready", "emergency_stop_unavailable")])
def test_live_requires_native_safety_and_explicit_target(harness, flag, reason):
    engine, backend, _, _ = harness
    engine.set_connected(True)
    setattr(backend, flag, False)
    assert not engine.arm(preview=False)
    assert engine.snapshot().reason == reason
    assert not backend.live


def test_live_requires_connection(harness):
    engine, backend, _, _ = harness
    assert not engine.arm(preview=False)
    assert engine.snapshot().reason == "not_connected"
    assert not backend.live


def test_countdown_drops_messages_and_checks_foreground_at_expiry(harness):
    engine, backend, clock, _ = harness
    engine.set_connected(True)
    backend.foreground = False
    assert engine.arm(preview=False, delay_seconds=3)
    assert engine.snapshot().state == "pending"
    assert not engine.submit(msg(clock)).accepted
    advance(engine, clock, 2.9)
    backend.foreground = True
    advance(engine, clock, 0.2)
    assert engine.snapshot().state == "armed"
    assert engine.snapshot().queue_depth == 0
    assert not backend.pressed and backend.live


def test_failed_countdown_latches_disarmed_until_new_local_arm(harness):
    engine, backend, clock, _ = harness
    engine.set_connected(True)
    backend.foreground = False
    assert engine.arm(preview=False, delay_seconds=0.1)
    advance(engine, clock, 0.11)
    assert engine.snapshot().state == "disarmed"
    backend.foreground = True
    advance(engine, clock, 0.1)
    assert not engine.submit(msg(clock)).accepted


def test_live_tap_releases_at_bounded_deadline(harness):
    engine, backend, clock, _ = harness
    arm_live(engine)
    assert engine.submit(msg(clock)).accepted
    engine.tick()
    assert backend.pressed == {"w"}
    advance(engine, clock, 0.16)
    assert not backend.pressed
    assert ("up", "w") in backend.events


@pytest.mark.parametrize("fault,reason", [("foreground", "target_not_foreground"), ("modifiers", "physical_modifier_held"), ("safety_ready", "emergency_stop_unavailable"), ("target_selected", "target_not_selected")])
def test_safety_loss_releases_and_latches_disarmed(harness, fault, reason):
    engine, backend, clock, _ = harness
    arm_live(engine)
    assert engine.submit(msg(clock)).accepted
    engine.tick()
    setattr(backend, fault, fault == "modifiers")
    advance(engine, clock, 0.01)
    assert not backend.pressed and not backend.live
    assert engine.snapshot().state == "disarmed"
    assert engine.snapshot().reason == reason
    setattr(backend, fault, fault != "modifiers")
    advance(engine, clock, 0.01)
    assert not engine.submit(msg(clock, "fresh-id")).accepted


def test_guard_runs_on_submit_and_again_immediately_before_execution(harness):
    engine, backend, clock, _ = harness
    arm_live(engine)
    assert engine.submit(msg(clock)).accepted
    backend.foreground = False
    engine.tick()
    assert not any(item[0] == "down" for item in backend.events)
    assert engine.snapshot().queue_depth == 0


@pytest.mark.parametrize("operation", ["disconnect", "profile", "panic", "pause"])
def test_all_stop_paths_drop_queued_work_and_release_active_key(harness, operation):
    engine, backend, clock, _ = harness
    engine.set_profile(Profile(commands=(Command("!forward", "w", 450, 0),), global_cooldown_ms=100))
    arm_live(engine)
    assert engine.submit(msg(clock)).accepted
    engine.tick()
    advance(engine, clock, 0.11)
    assert engine.submit(msg(clock, "second", "456")).accepted
    if operation == "disconnect":
        engine.set_connected(False)
    elif operation == "profile":
        engine.set_profile(Profile.default())
    elif operation == "panic":
        engine.emergency_stop()
    else:
        engine.disarm()
    assert engine.snapshot().state == "disarmed"
    assert engine.snapshot().queue_depth == 0
    assert not backend.pressed and not backend.live
    engine.set_connected(True)
    advance(engine, clock, 0.2)
    assert len([item for item in backend.events if item[0] == "down"]) == 1


def test_worker_stall_is_fail_closed(harness):
    engine, backend, clock, _ = harness
    arm_live(engine)
    assert engine.submit(msg(clock)).accepted
    engine.tick()
    clock.now += 0.3
    engine.tick()
    assert engine.snapshot().reason == "worker_watchdog_delay"
    assert not backend.pressed and not backend.live


@pytest.mark.parametrize("failure", ["fail_down", "fail_up", "fail_release"])
def test_backend_failure_latches_restart_required_without_secret_exception_text(harness, failure):
    engine, backend, clock, events = harness
    arm_live(engine)
    if failure == "fail_down":
        backend.fail_down = True
    assert engine.submit(msg(clock)).accepted
    engine.tick()
    if failure == "fail_up":
        backend.fail_up = True
        advance(engine, clock, 0.16)
    elif failure == "fail_release":
        backend.fail_release = True
        engine.disarm()
    assert engine.snapshot().state == "disarmed"
    assert engine.snapshot().backend_fault
    assert not engine.arm(preview=False, delay_seconds=0)
    assert "SHOULD_NOT_APPEAR_IN_LOGS" not in repr(events)
    assert engine.arm(preview=True)


def test_native_false_release_result_latches_fault_even_without_exception(harness):
    engine, backend, clock, events = harness
    arm_live(engine)
    assert engine.submit(msg(clock)).accepted
    engine.tick()
    backend.release_all = lambda: False
    engine.disarm()
    assert engine.snapshot().state == "disarmed"
    assert engine.snapshot().backend_fault
    assert engine.snapshot().reason == "input_release_failed_restart_required"
    assert any(event.kind == "fault" for event in events)
    assert not engine.arm(preview=False, delay_seconds=0)
    assert engine.snapshot().queue_depth == 0


@pytest.mark.parametrize("text", ["!forward && evil", "!forward\n!jump", "!forward\x00", "!forward ", "!FORWАRD", "!win", "!run", "!jump 999999"])
def test_untrusted_text_never_becomes_input(harness, text):
    engine, backend, clock, events = harness
    arm_live(engine)
    assert not engine.submit(msg(clock, text=text)).accepted
    engine.tick()
    assert not backend.pressed
    assert text not in [event.reason for event in events]


def test_default_audience_requires_authenticated_moderator_or_broadcaster_roles(harness):
    engine, _, clock, _ = harness
    engine.arm(preview=True)
    assert engine.submit(msg(clock, roles=frozenset())).reason == "audience_restricted"
    assert engine.submit(msg(clock, roles=frozenset({"subscriber"}))).reason == "audience_restricted"
    assert engine.submit(msg(clock, roles=frozenset({"broadcaster"}))).accepted


@pytest.mark.parametrize("audience,allowed,denied", [
    ("everyone", frozenset(), None),
    ("subscriber", frozenset({"subscriber"}), frozenset({"vip"})),
    ("vip", frozenset({"vip"}), frozenset({"subscriber"})),
    ("moderator", frozenset({"moderator"}), frozenset({"vip"})),
    ("broadcaster", frozenset({"broadcaster"}), frozenset({"moderator"})),
])
def test_audience_policy_uses_twitch_roles_not_message_text(harness, audience, allowed, denied):
    engine, _, clock, _ = harness
    engine.set_profile(replace(Profile.default(), audience=audience))
    engine.arm(preview=True)
    if denied is not None:
        assert engine.submit(msg(clock, roles=denied)).reason == "audience_restricted"
    assert engine.submit(msg(clock, roles=allowed)).accepted


def test_blocklist_wins_over_allowlist_and_broadcaster_role(harness):
    engine, _, clock, _ = harness
    engine.set_profile(replace(Profile.default(), allowed_user_ids=frozenset({"123"}), blocked_user_ids=frozenset({"123"})))
    engine.arm(preview=True)
    assert engine.submit(msg(clock, roles=frozenset({"broadcaster"}))).reason == "viewer_blocked"
    assert engine.submit(msg(clock, user="456")).reason == "viewer_not_allowed"


def test_duplicate_message_cannot_execute_twice_or_reset_on_profile_edit(harness):
    engine, _, clock, _ = harness
    engine.arm(preview=True)
    assert engine.submit(msg(clock)).accepted
    engine.tick()
    advance(engine, clock, 2)
    engine.set_profile(Profile.default())
    engine.arm(preview=True)
    assert engine.submit(msg(clock)).reason == "duplicate_message"


@pytest.mark.parametrize("timestamp,reason", [(math.nan, "invalid_timestamp"), (math.inf, "invalid_timestamp"), (True, "invalid_timestamp"), (101.0, "invalid_timestamp"), (99.0, "stale_message")])
def test_invalid_or_old_timestamps_are_rejected(harness, timestamp, reason):
    engine, _, clock, _ = harness
    engine.arm(preview=True)
    assert engine.submit(replace(msg(clock), received_at=timestamp)).reason == reason


def test_pre_arm_and_expired_messages_cannot_be_replayed(harness):
    engine, backend, clock, _ = harness
    old = msg(clock)
    clock.now += 0.1
    engine.arm(preview=True)
    assert engine.submit(old).reason == "stale_message"
    fresh = msg(clock)
    clock.now += 0.51
    assert engine.submit(fresh).reason == "stale_message"
    assert not backend.pressed


def test_cooldowns_apply_globally_per_viewer_and_per_command(harness):
    engine, _, clock, _ = harness
    engine.arm(preview=True)
    assert engine.submit(msg(clock)).accepted
    assert engine.submit(msg(clock, "second", "456", "!back")).reason == "global_cooldown"
    advance(engine, clock, 0.26)
    assert engine.submit(msg(clock, "third", "123", "!back")).reason == "viewer_cooldown"
    assert engine.submit(msg(clock, "fourth", "456")).reason == "command_cooldown"
    assert engine.submit(msg(clock, "fifth", "456", "!back")).accepted


def test_rolling_minute_budget_is_not_reset_by_disarm_or_profile_edits(harness):
    engine, _, clock, _ = harness
    profile = replace(Profile.default(), max_actions_per_minute=1)
    engine.set_profile(profile)
    engine.arm(preview=True)
    assert engine.submit(msg(clock)).accepted
    engine.tick()
    advance(engine, clock, 2)
    engine.set_profile(profile)
    engine.arm(preview=True)
    assert engine.submit(msg(clock, "second", "456")).reason == "minute_budget"
    advance(engine, clock, 58.01)
    assert engine.submit(msg(clock, "third", "456")).accepted


def test_queue_is_bounded_and_taps_have_release_gap(harness):
    engine, backend, clock, _ = harness
    engine.set_profile(Profile(commands=(Command("!forward", "w", 450, 0),), global_cooldown_ms=100,
                               queue_size=2, queue_ttl_ms=750))
    arm_live(engine)
    assert engine.submit(msg(clock)).accepted
    engine.tick()
    for index in range(2):
        advance(engine, clock, 0.11)
        assert engine.submit(msg(clock, f"next-{index}", str(index + 200))).accepted
    advance(engine, clock, 0.11)
    assert engine.submit(msg(clock, "overflow", "400")).reason == "queue_full"
    assert engine.snapshot().queue_depth == 2
    advance(engine, clock, 0.13)
    assert not backend.pressed
    assert len([item for item in backend.events if item[0] == "down"]) == 1
    advance(engine, clock, 0.05)
    assert backend.pressed == {"w"}
    assert len([item for item in backend.events if item[0] == "down"]) == 2


def test_expired_queued_commands_are_dropped_instead_of_bursting(harness):
    engine, backend, clock, events = harness
    engine.set_profile(replace(Profile.default(), queue_ttl_ms=100))
    engine.arm(preview=True)
    assert engine.submit(msg(clock)).accepted
    clock.now += 0.101
    engine.tick()
    assert engine.snapshot().queue_depth == 0
    assert any(event.kind == "dropped" for event in events)
    assert not any(event.kind == "preview" for event in events)
    assert not backend.pressed


def test_new_profile_does_not_inherit_a_stale_execution_deadline(harness):
    engine, _, clock, events = harness
    engine.set_profile(replace(Profile.default(), global_cooldown_ms=60_000))
    engine.arm(preview=True)
    assert engine.submit(msg(clock)).accepted
    engine.tick()
    advance(engine, clock, 2)
    engine.set_profile(Profile.default())
    engine.arm(preview=True)
    assert engine.submit(msg(clock, "second", "456")).accepted
    engine.tick()
    assert sum(event.kind == "preview" for event in events) == 2


def test_simultaneous_messages_do_not_race_global_budget(harness):
    engine, _, clock, _ = harness
    engine.arm(preview=True)
    with ThreadPoolExecutor(max_workers=12) as executor:
        decisions = list(executor.map(lambda index: engine.submit(msg(clock, f"event-{index}", str(index + 1))), range(200)))
    assert sum(decision.accepted for decision in decisions) == 1
    assert engine.snapshot().queue_depth == 1


def test_rejected_flood_does_not_grow_internal_state(harness):
    engine, _, clock, _ = harness
    engine.arm(preview=True)
    assert engine.submit(msg(clock)).accepted
    for index in range(10_000):
        assert not engine.submit(msg(clock, f"flood-{index}", str(index + 200), "!unknown")).accepted
    assert len(engine._recent) == 1
    assert len(engine._viewer_times) == 1
    assert len(engine._action_times) == 1
    assert engine.snapshot().queue_depth == 1


@pytest.mark.parametrize("armed", [False, True])
def test_ordinary_and_unknown_chat_is_quiet_and_skips_native_queries(harness, armed):
    engine, backend, clock, events = harness
    if armed:
        arm_live(engine)
    events.clear()

    def unexpected_native_query():
        pytest.fail("Ordinary chat must not query native window/process state")

    backend.is_target_foreground = unexpected_native_query
    backend.physical_modifiers_down = unexpected_native_query
    for index in range(10_000):
        text = "ordinary conversation" if index % 2 else "!another_bots_command"
        assert not engine.submit(msg(clock, f"chatter-{index}", text=text)).accepted
    assert not events
    assert engine.snapshot().rejected == 0
    assert engine.snapshot().queue_depth == 0
    assert not engine._recent and not engine._viewer_times
    assert engine.snapshot().state == ("armed" if armed else "disarmed")


def test_access_and_rate_denials_skip_native_but_admissible_commands_are_guarded(harness):
    engine, backend, clock, _ = harness
    arm_live(engine)
    queries = []

    def foreground_query():
        queries.append("foreground")
        return backend.foreground

    backend.is_target_foreground = foreground_query
    assert engine.submit(msg(clock, roles=frozenset())).reason == "audience_restricted"
    assert not queries
    assert engine.submit(msg(clock)).accepted
    assert len(queries) == 1
    assert engine.submit(msg(clock, "rate-denied", "456")).reason == "global_cooldown"
    assert len(queries) == 1
    engine.tick()
    assert backend.pressed == {"w"}
    assert len(queries) == 3  # tick safety check and immediate pre-injection check


def test_admissible_command_still_disarms_on_native_focus_loss(harness):
    engine, backend, clock, _ = harness
    arm_live(engine)
    backend.foreground = False
    assert engine.submit(msg(clock)).reason == "target_not_foreground"
    assert engine.snapshot().state == "disarmed"
    assert engine.snapshot().queue_depth == 0
    assert not backend.pressed


def test_rejection_spam_is_counted_but_cannot_flood_state_events(harness):
    engine, _, clock, events = harness
    engine.arm(preview=True)
    assert engine.submit(msg(clock)).accepted
    events.clear()
    for index in range(5_000):
        assert engine.submit(msg(clock, f"spam-{index}")).reason == "global_cooldown"
    assert engine.snapshot().rejected == 5_000
    assert len([event for event in events if event.kind == "rejected"]) == 1
    clock.now += 0.11
    assert engine.submit(msg(clock, "fresh-spam")).reason == "global_cooldown"
    assert len([event for event in events if event.kind == "rejected"]) == 2
    engine.emergency_stop()
    assert events[-1].kind == "state"
    assert events[-1].reason == "emergency_stop"


def test_event_callback_failure_does_not_break_release_paths(harness):
    engine, backend, clock, _ = harness
    def broken_callback(_event):
        raise RuntimeError("UI queue failure")
    engine._on_event = broken_callback
    arm_live(engine)
    assert engine.submit(msg(clock)).accepted
    engine.tick()
    engine.emergency_stop()
    assert not backend.pressed and not backend.live


@pytest.mark.parametrize("changes", [{"user_id": "someone"}, {"user_id": "0"}, {"channel_id": ""}, {"message_id": "bad\nID"}, {"roles": frozenset({"admin"})}, {"roles": ["moderator"]}])
def test_malformed_transport_metadata_cannot_reach_input(harness, changes):
    engine, backend, clock, _ = harness
    arm_live(engine)
    assert not engine.submit(replace(msg(clock), **changes)).accepted
    engine.tick()
    assert not backend.pressed
