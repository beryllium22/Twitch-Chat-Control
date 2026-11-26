"""Mixed keyboard/mouse policy and release paths; all input is a fake backend."""

from dataclasses import replace

import pytest
from test_engine import Backend, Clock, advance, arm_live, msg

from twitch_chat_control.engine import InputBlocked, SafetyEngine
from twitch_chat_control.models import Command, Profile
from twitch_chat_control.presets import PreviewBackend

COMMANDS = (
    Command("!key", "w", hold_ms=100, cooldown_ms=0),
    Command("!button", action_type="mouse_button", button="left", hold_ms=450, cooldown_ms=0),
    Command("!move", action_type="mouse_move", dx=25, dy=-10, cooldown_ms=0),
    Command("!wheel", action_type="mouse_wheel", wheel=-1, cooldown_ms=0),
)


class MouseBackend(Backend):
    blocked_reason = None

    def _mouse(self, *event):
        assert self.live and self.foreground and not self.modifiers
        assert not self.pressed, "Keyboard and mouse holds must not overlap"
        if self.blocked_reason:
            raise InputBlocked(self.blocked_reason)
        if self.fail_down:
            raise RuntimeError("PRIVATE_NATIVE_ERROR")
        self.events.append(event)

    def mouse_button_down(self, button):
        self._mouse("mouse_down", button)
        self.pressed.add("mouse:" + button)

    def mouse_button_up(self, button):
        if self.fail_up:
            raise RuntimeError("PRIVATE_NATIVE_ERROR")
        self.pressed.discard("mouse:" + button)
        self.events.append(("mouse_up", button))

    def mouse_move(self, dx, dy):
        self._mouse("move", dx, dy)

    def mouse_wheel(self, steps):
        self._mouse("wheel", steps)


@pytest.fixture
def rig():
    backend, clock, events = MouseBackend(), Clock(), []
    profile = Profile(commands=COMMANDS, global_cooldown_ms=100, viewer_cooldown_ms=250,
                      queue_ttl_ms=750)
    return SafetyEngine(backend, events.append, clock, profile), backend, clock, events


@pytest.mark.parametrize("command,event", [
    (COMMANDS[0], ("down", "w")),
    (COMMANDS[1], ("mouse_down", "left")),
    (COMMANDS[2], ("move", 25, -10)),
    (COMMANDS[3], ("wheel", -1)),
])
def test_only_exact_configured_action_is_dispatched(rig, command, event):
    engine, backend, clock, _ = rig
    arm_live(engine)
    assert engine.submit(msg(clock, text=command.trigger)).accepted
    engine.tick()
    assert event in backend.events
    snapshot = engine.snapshot()
    assert snapshot.active_key == (command.key if command.action_type == "key" else None)
    assert snapshot.active_action == (command.action_label if command.is_held else None)
    advance(engine, clock, 0.6)
    assert not backend.pressed
    assert engine.snapshot().state == "armed"
    assert sum(entry == event for entry in backend.events) == 1


@pytest.mark.parametrize("command", COMMANDS)
def test_preview_has_no_native_path_for_any_action(command):
    clock, events = Clock(), []
    engine = SafetyEngine(PreviewBackend(), events.append, clock, Profile(commands=(command,)))
    assert engine.arm(preview=True)
    assert engine.submit(msg(clock, text=command.trigger)).accepted
    engine.tick()
    assert [event.command for event in events if event.kind == "preview"] == [command.trigger]
    assert engine.snapshot().active_action is None
    assert not engine.snapshot().backend_fault


@pytest.mark.parametrize("stop", ["pause", "panic", "disconnect", "profile", "focus", "modifier", "watchdog"])
def test_every_stop_path_releases_mouse_and_discards_queue(rig, stop):
    engine, backend, clock, _ = rig
    arm_live(engine)
    assert engine.submit(msg(clock, text="!button")).accepted
    engine.tick()
    assert backend.pressed == {"mouse:left"}
    advance(engine, clock, 0.11)
    assert engine.submit(msg(clock, ident="second", user="456", text="!move")).accepted
    if stop == "pause":
        engine.disarm()
    elif stop == "panic":
        engine.emergency_stop()
    elif stop == "disconnect":
        engine.set_connected(False)
    elif stop == "profile":
        engine.set_profile(replace(engine.profile, name="Edited"))
    elif stop == "focus":
        backend.foreground = False
        engine.tick()
    elif stop == "modifier":
        backend.modifiers = True
        engine.tick()
    else:
        clock.now += 0.3
        engine.tick()
    assert not backend.pressed and not backend.live
    assert engine.snapshot().state == "disarmed"
    assert engine.snapshot().queue_depth == 0
    assert not any(event[0] == "move" for event in backend.events)


@pytest.mark.parametrize("following", ["!key", "!move", "!wheel"])
def test_mouse_hold_never_overlaps_next_action_and_release_gap_is_preserved(rig, following):
    engine, backend, clock, _ = rig
    arm_live(engine)
    assert engine.submit(msg(clock, text="!button")).accepted
    engine.tick()
    advance(engine, clock, 0.11)
    assert engine.submit(msg(clock, ident="next", user="456", text=following)).accepted
    advance(engine, clock, 0.3)
    assert backend.pressed == {"mouse:left"}
    assert not any(event[0] in {"down", "move", "wheel"} for event in backend.events)
    advance(engine, clock, 0.07)
    assert not backend.pressed
    assert not any(event[0] in {"down", "move", "wheel"} for event in backend.events)
    advance(engine, clock, 0.04)
    assert any(event[0] in {"down", "move", "wheel"} for event in backend.events)


def test_expired_mouse_action_is_not_replayed_after_long_hold(rig):
    engine, backend, clock, events = rig
    engine.set_profile(replace(engine.profile, queue_ttl_ms=100))
    arm_live(engine)
    engine.submit(msg(clock, text="!button"))
    engine.tick()
    advance(engine, clock, 0.11)
    assert engine.submit(msg(clock, ident="old", user="456", text="!move")).accepted
    advance(engine, clock, 0.5)
    assert not any(event[0] == "move" for event in backend.events)
    assert any(event.reason == "queue_expired" for event in events)


@pytest.mark.parametrize("first,second", [("!move", "!key"), ("!key", "!wheel"), ("!wheel", "!button")])
def test_all_modalities_share_admission_pacing_and_action_budget(rig, first, second):
    engine, _, clock, _ = rig
    engine.set_profile(replace(engine.profile, max_actions_per_minute=1))
    arm_live(engine)
    assert engine.submit(msg(clock, text=first)).accepted
    assert engine.submit(msg(clock, ident="quick", user="456", text=second)).reason == "global_cooldown"
    engine.tick()
    advance(engine, clock, 0.3)
    assert engine.submit(msg(clock, ident="over-budget", user="456", text=second)).reason == "minute_budget"


@pytest.mark.parametrize("reason", sorted(InputBlocked.REASONS))
def test_routine_mouse_guard_pauses_without_latching_native_fault(rig, reason):
    engine, backend, clock, events = rig
    arm_live(engine)
    backend.blocked_reason = reason
    assert engine.submit(msg(clock, text="!move")).accepted
    engine.tick()
    snapshot = engine.snapshot()
    assert snapshot.state == "disarmed" and snapshot.reason == reason
    assert not snapshot.backend_fault and not backend.live
    backend.blocked_reason = None
    advance(engine, clock, 0.3)
    arm_live(engine)
    assert engine.submit(msg(clock, ident="fresh", text="!move")).accepted
    engine.tick()
    assert ("move", 25, -10) in backend.events
    assert any(event.reason == reason for event in events)


@pytest.mark.parametrize("trigger", ["!button", "!move", "!wheel"])
def test_native_mouse_send_failure_stays_latched(rig, trigger):
    engine, backend, clock, events = rig
    arm_live(engine)
    backend.fail_down = True
    assert engine.submit(msg(clock, text=trigger)).accepted
    engine.tick()
    assert engine.snapshot().backend_fault
    assert engine.snapshot().state == "disarmed"
    backend.fail_down = False
    assert not engine.arm(preview=False, delay_seconds=0)
    assert "PRIVATE_NATIVE_ERROR" not in repr(events)


def test_mouse_release_failure_retries_all_and_latches_live_input(rig):
    engine, backend, clock, _ = rig
    arm_live(engine)
    engine.submit(msg(clock, text="!button"))
    engine.tick()
    backend.fail_up = True
    advance(engine, clock, 0.46)
    assert engine.snapshot().backend_fault and engine.snapshot().state == "disarmed"
    assert not backend.pressed and not backend.live
    assert not engine.arm(preview=False, delay_seconds=0)


@pytest.mark.parametrize("trigger", ["!move 1000", "!button left", "!wheel -50", "!move !button"])
def test_chat_cannot_supply_coordinates_button_or_macro_arguments(rig, trigger):
    engine, backend, clock, _ = rig
    arm_live(engine)
    assert not engine.submit(msg(clock, text=trigger)).accepted
    engine.tick()
    assert not any(event[0] in {"move", "wheel", "mouse_down", "down"} for event in backend.events)


def test_guard_errors_cannot_echo_arbitrary_native_details():
    with pytest.raises(ValueError, match="Unsupported native input guard reason"):
        InputBlocked("PRIVATE_NATIVE_ERROR")
