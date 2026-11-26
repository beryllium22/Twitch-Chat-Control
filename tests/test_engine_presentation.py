"""Execution feedback keeps the exact immutable action and its session identity."""

from dataclasses import FrozenInstanceError, replace

import pytest
from test_engine import Clock, advance, arm_live, msg
from test_mouse_engine import COMMANDS, MouseBackend

from twitch_chat_control.engine import SafetyEngine
from twitch_chat_control.models import Profile


@pytest.fixture
def presentation():
    clock, backend, events = Clock(), MouseBackend(), []
    profile = Profile(commands=COMMANDS, global_cooldown_ms=100)
    engine = SafetyEngine(backend, events.append, clock, profile)
    return engine, backend, clock, events


@pytest.mark.parametrize("command", COMMANDS)
def test_preview_feedback_distinguishes_admission_from_execution(presentation, command):
    engine, backend, clock, events = presentation
    assert engine.arm(preview=True)
    generation = engine.snapshot().generation
    assert engine.submit(msg(clock, text=command.trigger)).accepted
    admitted = [event for event in events if event.kind == "accepted"]
    assert len(admitted) == 1
    assert admitted[0].action is command
    assert admitted[0].generation == generation
    assert not any(event.kind == "preview" for event in events)

    engine.tick()
    rendered = [event for event in events if event.kind == "preview"]
    assert len(rendered) == 1
    assert rendered[0].action is command
    assert rendered[0].command == command.trigger
    assert rendered[0].generation == generation
    assert not any(event[0] in {"down", "up", "mouse_down", "mouse_up", "move", "wheel"}
                   for event in backend.events)


def test_delayed_event_retains_old_mapping_and_session_after_profile_change(presentation):
    engine, _, clock, events = presentation
    command = COMMANDS[2]
    assert engine.arm(preview=True)
    assert engine.submit(msg(clock, text=command.trigger)).accepted
    engine.tick()
    old_event = next(event for event in events if event.kind == "preview")

    replacement = replace(command, dx=-command.dx, dy=-command.dy)
    engine.set_profile(replace(engine.profile, commands=(replacement,)))
    assert engine.arm(preview=True)
    assert old_event.generation != engine.snapshot().generation
    assert old_event.action is command
    assert old_event.action != replacement
    with pytest.raises(FrozenInstanceError):
        old_event.action.dx = 90
    with pytest.raises(FrozenInstanceError):
        old_event.generation = engine.snapshot().generation


@pytest.mark.parametrize("stop", ["pause", "emergency", "disconnect", "profile", "rearm"])
def test_each_session_boundary_invalidates_queued_presentation(presentation, stop):
    engine, _, clock, events = presentation
    assert engine.arm(preview=True)
    assert engine.submit(msg(clock, text=COMMANDS[2].trigger)).accepted
    admitted = next(event for event in events if event.kind == "accepted")
    if stop == "pause":
        engine.disarm()
    elif stop == "emergency":
        engine.emergency_stop()
    elif stop == "disconnect":
        engine.set_connected(False)
    elif stop == "profile":
        engine.set_profile(replace(engine.profile, name="Changed profile"))
    else:
        engine.arm(preview=True)
    snapshot = engine.snapshot()
    assert snapshot.generation > admitted.generation
    assert snapshot.queue_depth == 0
    engine.tick()
    assert not any(event.kind == "preview" for event in events)


def test_expired_admission_does_not_produce_executed_preview_feedback(presentation):
    engine, _, clock, events = presentation
    assert engine.arm(preview=True)
    assert engine.submit(msg(clock, text=COMMANDS[2].trigger)).accepted
    clock.now += engine.profile.queue_ttl_ms / 1000 + 0.001
    engine.tick()
    assert engine.snapshot().accepted == 1
    assert engine.snapshot().queue_depth == 0
    dropped = [event for event in events if event.kind == "dropped"]
    assert len(dropped) == 1
    assert dropped[0].reason == "queue_expired"
    assert dropped[0].action is None
    assert dropped[0].generation == engine.snapshot().generation
    assert not any(event.kind == "preview" for event in events)


@pytest.mark.parametrize("command", COMMANDS)
def test_live_feedback_keeps_action_without_becoming_preview(presentation, command):
    engine, _, clock, events = presentation
    arm_live(engine)
    assert engine.submit(msg(clock, text=command.trigger)).accepted
    engine.tick()
    executed = [event for event in events if event.kind == "executed"]
    assert len(executed) == 1
    assert executed[0].action is command
    assert executed[0].generation == engine.snapshot().generation
    assert not any(event.kind == "preview" for event in events)
    if command.is_held:
        advance(engine, clock, command.hold_ms / 1000 + 0.01)
        released = [event for event in events if event.kind == "released"]
        assert len(released) == 1
        assert released[0].action is command
        assert released[0].generation == executed[0].generation


def test_rejected_feedback_has_no_executed_action(presentation):
    engine, _, clock, events = presentation
    assert engine.arm(preview=True)
    assert engine.submit(msg(clock, text=COMMANDS[0].trigger)).accepted
    rejected = engine.submit(msg(clock, ident="later", user="456", text=COMMANDS[2].trigger))
    assert not rejected.accepted
    feedback = [event for event in events if event.kind == "rejected"]
    assert len(feedback) == 1
    assert feedback[0].action is None
    assert feedback[0].generation == engine.snapshot().generation
