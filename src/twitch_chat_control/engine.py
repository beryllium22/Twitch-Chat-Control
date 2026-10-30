"""Fail-closed control state machine. All input paths share one policy and lock."""

from __future__ import annotations

import math
import re
import threading
import time
from collections import OrderedDict, deque
from dataclasses import dataclass
from typing import Any, Callable, Protocol

from .models import Command, Profile, normalize_command


class InputBackend(Protocol):
    @property
    def target_selected(self) -> bool: ...
    @property
    def safety_ready(self) -> bool: ...
    def is_target_foreground(self) -> bool: ...
    def physical_modifiers_down(self) -> bool: ...
    def key_down(self, key: str) -> None: ...
    def key_up(self, key: str) -> None: ...
    def release_all(self) -> bool | None: ...
    def bind_live_session(self, live: bool) -> None: ...


@dataclass(frozen=True, slots=True)
class Decision:
    accepted: bool
    reason: str
    command: str = ""


@dataclass(frozen=True, slots=True)
class EngineEvent:
    kind: str
    reason: str
    command: str = ""
    user_id: str = ""
    timestamp: float = 0.0


@dataclass(frozen=True, slots=True)
class EngineSnapshot:
    state: str
    preview: bool
    connected: bool
    reason: str
    queue_depth: int
    active_key: str | None
    accepted: int
    rejected: int
    pending_seconds: float
    backend_fault: bool


@dataclass(frozen=True, slots=True)
class _QueuedAction:
    command: Command
    user_id: str
    message_id: str
    expires_at: float
    generation: int


_ID = re.compile(r"[1-9][0-9]{0,31}\Z", re.ASCII)
_MESSAGE_ID = re.compile(r"[A-Za-z0-9_-]{1,128}\Z", re.ASCII)
_AUDIENCE_ROLES = {
    "subscriber": frozenset({"subscriber", "moderator", "broadcaster"}),
    "vip": frozenset({"vip", "moderator", "broadcaster"}),
    "moderator": frozenset({"moderator", "broadcaster"}),
    "broadcaster": frozenset({"broadcaster"}),
}
_KNOWN_ROLES = frozenset({"subscriber", "vip", "moderator", "broadcaster"})


class SafetyEngine:
    """No auto-arm, no persisted authority, no unbounded execution queue.

    ``tick`` should run on a dedicated worker every 10 ms. The native backend must
    additionally enforce its own release deadline and focus/physical-key monitor.
    The event callback must be nonblocking (the GUI puts events into a bounded
    queue); callback failures never bypass input safety checks.
    """

    WATCHDOG_SECONDS = 0.250
    RELEASE_GAP_SECONDS = 0.050
    DEDUP_SECONDS = 120.0
    MAX_RECENT_IDS = 4096
    MAX_RECENT_VIEWERS = 2048
    REJECTION_EVENT_INTERVAL_SECONDS = 0.100

    def __init__(self, backend: InputBackend, on_event: Callable[[EngineEvent], None] | None = None,
                 clock: Callable[[], float] = time.monotonic, profile: Profile | None = None) -> None:
        self.backend = backend
        self._clock = clock
        self._on_event = on_event
        self._lock = threading.RLock()
        self._profile = profile if profile is not None else Profile.default()
        if type(self._profile) is not Profile:
            raise TypeError("profile must be a validated Profile")
        self._commands = {command.trigger: command for command in self._profile.commands}
        self._state = "disarmed"
        self._preview = True
        self._connected = False
        self._reason = "start_disarmed"
        self._faulted = False
        self._generation = 0
        self._pending_until = 0.0
        self._armed_at = math.inf
        self._active: _QueuedAction | None = None
        self._release_at = 0.0
        self._queue: deque[_QueuedAction] = deque()
        self._recent: OrderedDict[str, float] = OrderedDict()
        self._viewer_times: OrderedDict[str, float] = OrderedDict()
        self._command_times: dict[str, float] = {}
        self._action_times: deque[float] = deque()
        self._last_admitted = -math.inf
        self._next_execution_at = 0.0
        self._last_tick = self._clock()
        self._accepted = 0
        self._rejected = 0
        self._next_rejection_event_at = -math.inf

    @property
    def profile(self) -> Profile:
        with self._lock:
            return self._profile

    def snapshot(self) -> EngineSnapshot:
        with self._lock:
            return EngineSnapshot(self._state, self._preview, self._connected, self._reason,
                                  len(self._queue), self._active.command.key if self._active else None,
                                  self._accepted, self._rejected,
                                  max(0.0, self._pending_until - self._clock()) if self._state == "pending" else 0.0,
                                  self._faulted)

    def _emit(self, kind: str, reason: str, command: str = "", user_id: str = "") -> None:
        if self._on_event is None:
            return
        try:
            self._on_event(EngineEvent(kind, reason, command, user_id, self._clock()))
        except Exception:
            # UI/logging failure cannot turn into an input bypass or crash a
            # key-release path. The UI's own heartbeat governs its availability.
            pass

    def _release(self) -> None:
        failed = False
        try:
            self.backend.bind_live_session(False)
        except Exception:
            failed = True
        try:
            if self.backend.release_all() is False:
                failed = True
        except Exception:
            failed = True
        self._active = None
        self._release_at = 0.0
        if failed:
            self._faulted = True
            self._reason = "input_release_failed_restart_required"
            self._emit("fault", self._reason)

    def _disarm(self, reason: str) -> None:
        self._state = "disarmed"
        self._reason = reason
        self._generation += 1
        self._pending_until = 0.0
        self._armed_at = math.inf
        self._queue.clear()
        # A prior profile's pacing deadline cannot strand a newly armed
        # session. Admission budgets remain in place across local changes.
        self._next_execution_at = min(self._next_execution_at, self._clock())
        self._release()
        self._emit("state", self._reason)

    def disarm(self, reason: str = "local_pause") -> None:
        with self._lock:
            # This API is for local callers; never echo arbitrary exception or
            # chat content into logs via a disarm reason.
            safe = re.sub(r"[^a-z0-9_]", "_", str(reason).lower())[:64] or "local_pause"
            self._disarm(safe)

    def emergency_stop(self) -> None:
        self.disarm("emergency_stop")

    def set_connected(self, connected: bool) -> None:
        if type(connected) is not bool:
            raise TypeError("connected must be bool")
        with self._lock:
            self._connected = connected
            if not connected:
                self._disarm("connection_lost")
            else:
                self._emit("connection", "connected_controls_remain_disarmed")

    def set_profile(self, profile: Profile) -> None:
        if type(profile) is not Profile:
            raise TypeError("profile must be a validated Profile")
        with self._lock:
            self._disarm("profile_changed")
            self._profile = profile
            self._commands = {command.trigger: command for command in profile.commands}
            # Retain recent rate and replay history across configuration edits.
            self._command_times = {key: value for key, value in self._command_times.items()
                                   if key in self._commands}

    def _guard(self) -> str | None:
        if not self._connected:
            return "not_connected"
        if self._faulted:
            return "backend_fault_restart_required"
        try:
            if not self.backend.safety_ready:
                return "emergency_stop_unavailable"
            if not self.backend.target_selected:
                return "target_not_selected"
            if not self.backend.is_target_foreground():
                return "target_not_foreground"
            if self.backend.physical_modifiers_down():
                return "physical_modifier_held"
        except Exception:
            self._faulted = True
            return "backend_fault_restart_required"
        return None

    def arm(self, preview: bool = True, delay_seconds: float = 3.0) -> bool:
        if type(preview) is not bool:
            raise TypeError("preview must be bool")
        if isinstance(delay_seconds, bool) or not isinstance(delay_seconds, (int, float)) or not math.isfinite(delay_seconds) or not 0 <= delay_seconds <= 10:
            raise ValueError("delay_seconds must be between 0 and 10")
        with self._lock:
            self._disarm("rearming")
            self._preview = preview
            now = self._clock()
            self._last_tick = now
            if preview:
                self._state = "armed"
                self._armed_at = now
                self._reason = "preview_armed"
                self._emit("state", self._reason)
                return True
            # During the countdown the streamer may focus the selected game.
            # The full foreground/modifier check occurs at countdown expiry.
            reason = None
            try:
                if not self._connected:
                    reason = "not_connected"
                elif self._faulted:
                    reason = "backend_fault_restart_required"
                elif not self.backend.safety_ready:
                    reason = "emergency_stop_unavailable"
                elif not self.backend.target_selected:
                    reason = "target_not_selected"
            except Exception:
                self._faulted = True
                reason = "backend_fault_restart_required"
            if reason:
                self._reason = reason
                self._emit("blocked", reason)
                return False
            self._state = "pending"
            self._pending_until = now + delay_seconds
            self._reason = "focus_game_countdown"
            self._emit("state", self._reason)
            if delay_seconds == 0:
                self._finish_arm(now)
            return self._state in {"pending", "armed"}

    def _finish_arm(self, now: float) -> None:
        reason = self._guard()
        if reason:
            self._disarm(reason)
            return
        try:
            self.backend.bind_live_session(True)
        except Exception:
            self._faulted = True
            self._disarm("backend_fault_restart_required")
            return
        self._state = "armed"
        self._armed_at = now
        self._pending_until = 0.0
        self._reason = "live_armed"
        self._emit("state", self._reason)

    def _reject(self, reason: str, command: str = "", user_id: str = "") -> Decision:
        self._rejected += 1
        # Keep exact attempt counts, but do not let chat spam fill the GUI's
        # event queue with repeated cooldown/access failures. State, connection,
        # release and accepted-action events are never subject to this throttle.
        now = self._clock()
        if now >= self._next_rejection_event_at:
            self._next_rejection_event_at = now + self.REJECTION_EVENT_INTERVAL_SECONDS
            self._emit("rejected", reason, command, user_id)
        return Decision(False, reason, command)

    def _prune(self, now: float) -> None:
        while self._recent and next(iter(self._recent.values())) <= now:
            self._recent.popitem(last=False)
        while self._viewer_times and next(iter(self._viewer_times.values())) <= now - 60:
            self._viewer_times.popitem(last=False)
        while self._action_times and self._action_times[0] <= now - 60:
            self._action_times.popleft()

    def submit(self, message: Any) -> Decision:
        with self._lock:
            # Ordinary conversation and unknown bot commands are not input
            # attempts. Ignore them before any native process/window query,
            # history maintenance or UI event; large chats stay inexpensive.
            try:
                trigger = normalize_command(message.text)
            except (AttributeError, TypeError):
                return Decision(False, "invalid_message")
            if trigger is None:
                return Decision(False, "not_an_exact_command")
            command = self._commands.get(trigger)
            if command is None:
                return Decision(False, "unknown_or_disabled_command")
            if not command.enabled:
                return self._reject("unknown_or_disabled_command", trigger)
            now = self._clock()
            self._prune(now)
            if self._state != "armed":
                return self._reject("not_armed", trigger)
            # Treat even transport-created objects as untrusted at this boundary.
            try:
                message_id, user_id, channel_id = message.message_id, message.user_id, message.channel_id
                received_at, roles = message.received_at, message.roles
            except (AttributeError, TypeError):
                return self._reject("invalid_message", trigger)
            if type(message_id) is not str or not _MESSAGE_ID.fullmatch(message_id):
                return self._reject("invalid_message_id", trigger)
            if type(user_id) is not str or not _ID.fullmatch(user_id) or type(channel_id) is not str or not _ID.fullmatch(channel_id):
                return self._reject("invalid_identity", trigger)
            if type(roles) not in (set, frozenset) or not roles <= _KNOWN_ROLES:
                return self._reject("invalid_roles", trigger, user_id)
            if type(received_at) not in (int, float) or not math.isfinite(received_at) or received_at > now + 0.05:
                return self._reject("invalid_timestamp", trigger, user_id)
            if received_at < self._armed_at or now - received_at > self._profile.queue_ttl_ms / 1000:
                return self._reject("stale_message", trigger, user_id)
            if message_id in self._recent:
                return self._reject("duplicate_message", trigger, user_id)
            if user_id in self._profile.blocked_user_ids:
                return self._reject("viewer_blocked", trigger, user_id)
            if self._profile.allowed_user_ids and user_id not in self._profile.allowed_user_ids:
                return self._reject("viewer_not_allowed", trigger, user_id)
            if self._profile.audience != "everyone" and not roles & _AUDIENCE_ROLES[self._profile.audience]:
                return self._reject("audience_restricted", trigger, user_id)
            if now - self._last_admitted < self._profile.global_cooldown_ms / 1000:
                return self._reject("global_cooldown", trigger, user_id)
            if now - self._viewer_times.get(user_id, -math.inf) < self._profile.viewer_cooldown_ms / 1000:
                return self._reject("viewer_cooldown", trigger, user_id)
            if now - self._command_times.get(trigger, -math.inf) < command.cooldown_ms / 1000:
                return self._reject("command_cooldown", trigger, user_id)
            if len(self._action_times) >= self._profile.max_actions_per_minute:
                return self._reject("minute_budget", trigger, user_id)
            if len(self._queue) >= self._profile.queue_size:
                return self._reject("queue_full", trigger, user_id)
            # Only an otherwise admissible command incurs the native checks.
            # tick() and the independent native monitor keep watching focus and
            # modifiers between actions, including during rejected-chat floods.
            if not self._preview:
                reason = self._guard()
                if reason:
                    self._disarm(reason)
                    return self._reject(reason, trigger, user_id)
                now = self._clock()
                if now - received_at > self._profile.queue_ttl_ms / 1000:
                    return self._reject("stale_message", trigger, user_id)
            expires_at = min(received_at + self._profile.queue_ttl_ms / 1000,
                             now + self._profile.queue_ttl_ms / 1000)
            self._queue.append(_QueuedAction(command, user_id, message_id, expires_at, self._generation))
            self._recent[message_id] = now + self.DEDUP_SECONDS
            while len(self._recent) > self.MAX_RECENT_IDS:
                self._recent.popitem(last=False)
            self._viewer_times[user_id] = now
            self._viewer_times.move_to_end(user_id)
            while len(self._viewer_times) > self.MAX_RECENT_VIEWERS:
                self._viewer_times.popitem(last=False)
            self._last_admitted = now
            self._command_times[trigger] = now
            self._action_times.append(now)
            self._accepted += 1
            self._emit("accepted", "preview_queued" if self._preview else "live_queued", trigger, user_id)
            return Decision(True, "queued", trigger)

    def tick(self) -> None:
        with self._lock:
            now = self._clock()
            elapsed = now - self._last_tick
            self._last_tick = now
            if self._state == "disarmed":
                return
            if not self._preview and (elapsed < 0 or elapsed > self.WATCHDOG_SECONDS):
                self._disarm("worker_watchdog_delay")
                return
            if self._state == "pending":
                if now >= self._pending_until:
                    self._finish_arm(now)
                return
            if not self._preview:
                reason = self._guard()
                if reason:
                    self._disarm(reason)
                    return
            if self._active and now >= self._release_at:
                action = self._active
                try:
                    self.backend.key_up(action.command.key)
                except Exception:
                    self._faulted = True
                    self._disarm("input_release_failed_restart_required")
                    return
                self._active = None
                self._release_at = 0.0
                self._next_execution_at = max(self._next_execution_at, now + self.RELEASE_GAP_SECONDS)
                self._emit("released", "tap_finished", action.command.trigger, action.user_id)
            # Discard stale work even when a key is held; never replay a backlog.
            while self._queue and (self._queue[0].expires_at <= now or self._queue[0].generation != self._generation):
                action = self._queue.popleft()
                self._emit("dropped", "queue_expired", action.command.trigger, action.user_id)
            if self._active or not self._queue or now < self._next_execution_at:
                return
            action = self._queue.popleft()
            if self._preview:
                self._next_execution_at = now + self._profile.global_cooldown_ms / 1000
                self._emit("preview", "would_tap", action.command.trigger, action.user_id)
                return
            # Last guard directly before the native API; the native backend
            # checks independently again immediately before SendInput.
            reason = self._guard()
            if reason:
                self._disarm(reason)
                return
            try:
                self.backend.key_down(action.command.key)
            except Exception:
                self._faulted = True
                self._disarm("input_failed_restart_required")
                return
            self._active = action
            self._release_at = now + action.command.hold_ms / 1000
            self._next_execution_at = now + self._profile.global_cooldown_ms / 1000
            self._emit("executed", "key_down", action.command.trigger, action.user_id)
