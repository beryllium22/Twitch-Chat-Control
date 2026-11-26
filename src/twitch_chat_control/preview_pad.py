"""A visual practice area with no route to keyboard, mouse, or network input.

The controller calls ``show_command`` only after its Preview policy executes an
action. This component draws the result; it neither admits nor executes input.
Rejection text must already be an approved application message, never raw chat
or an exception. Virtual coordinates are illustrative and do not calibrate a
game's sensitivity or claim to confine the actual Windows pointer.
"""

from __future__ import annotations

import tkinter as tk
from collections import deque
from tkinter import ttk
from typing import Any

from .models import Command

_FIELD = "#101a2a"
_GRID = "#293e59"
_TEXT = "#edf2f8"
_MUTED = "#b1bfd1"
_ACCENT = "#9ac6ff"
_TRAIL = "#56779c"
_BUTTON = "#ffcf80"
_REJECTED = "#ffacb8"


class PreviewPad(ttk.Frame):
    """Compact, keyboard-accessible feedback for already-approved previews.

    ``virtual_position`` and ``trail`` are immutable views of this drawing's
    state. No global pointer position is inspected or changed. Resizing only
    changes drawing scale, and Reset affects this practice area alone.
    """

    LOGICAL_WIDTH = 400
    LOGICAL_HEIGHT = 200
    MAX_TRAIL_POINTS = 16
    MAX_REJECTION_LENGTH = 240

    def __init__(self, parent: tk.Misc) -> None:
        super().__init__(parent, style="Card.TFrame")
        self.columnconfigure(0, weight=1)
        self._position = (self.LOGICAL_WIDTH // 2, self.LOGICAL_HEIGHT // 2)
        self._trail: deque[tuple[int, int]] = deque([self._position], maxlen=self.MAX_TRAIL_POINTS)
        self._last_command: Command | None = None
        self._rejected = False
        self._at_edge = False
        self._draw_job: str | None = None
        # Root-wide shutdown may cancel callbacks before destroying children.
        # Register on that same owner so Tkinter does not retain a stale child
        # Tcl command and attempt to delete it a second time on destruction.
        self._callback_owner = self.winfo_toplevel()
        self._draw_signature: tuple[Any, ...] | None = None
        self._closed = False

        header = ttk.Frame(self, style="Card.TFrame")
        header.grid(row=0, column=0, sticky="ew")
        header.columnconfigure(0, weight=1)
        ttk.Label(header, text="Practice pad", style="CardHeading.TLabel").grid(row=0, column=0, sticky="w")
        self.reset_button = ttk.Button(header, text="Reset pad", command=self.reset)
        self.reset_button.grid(row=0, column=1, sticky="e", padx=(12, 0))
        self.preview_label = ttk.Label(
            self, text="Preview only. Your real keyboard and mouse stay untouched.",
            style="CardMuted.TLabel", wraplength=460, justify="left",
        )
        self.preview_label.grid(row=1, column=0, sticky="ew", pady=(5, 8))
        self.canvas = tk.Canvas(
            self, width=1, height=154, bg=_FIELD, highlightthickness=1,
            highlightbackground=_GRID, borderwidth=0, takefocus=False,
        )
        self.canvas.grid(row=2, column=0, sticky="ew")
        self.summary_var = tk.StringVar(self, value="Try a command in Preview to see its action here.")
        self.position_var = tk.StringVar(self, value="Practice pointer: center.")
        self.summary_label = ttk.Label(
            self, textvariable=self.summary_var, style="Card.TLabel", wraplength=460, justify="left",
        )
        self.summary_label.grid(row=3, column=0, sticky="ew", pady=(8, 3))
        self.position_label = ttk.Label(
            self, textvariable=self.position_var, style="CardMuted.TLabel", wraplength=460, justify="left",
        )
        self.position_label.grid(row=4, column=0, sticky="ew")
        self.scale_label = ttk.Label(
            self, text="Movement is illustrative; game sensitivity can differ. Reset only clears this pad.",
            style="CardMuted.TLabel", wraplength=460, justify="left",
        )
        self.scale_label.grid(row=5, column=0, sticky="ew", pady=(3, 0))
        self.bind("<Configure>", self._layout, add="+")
        self.bind("<Destroy>", self._destroyed, add="+")
        self.canvas.bind("<Configure>", self._request_draw, add="+")
        self._request_draw()

    @property
    def virtual_position(self) -> tuple[int, int]:
        return self._position

    @property
    def trail(self) -> tuple[tuple[int, int], ...]:
        return tuple(self._trail)

    def show_command(self, command: Command) -> None:
        """Illustrate one action that the caller already executed in Preview."""
        if type(command) is not Command:
            raise TypeError("The practice pad requires a validated Command.")
        if not command.enabled:
            raise ValueError("A disabled command cannot be shown as an executed preview.")
        self._last_command = command
        self._rejected = False
        self._at_edge = False
        if command.action_type == "mouse_move":
            x, y = self._position
            proposed = (x + command.dx, y + command.dy)
            self._position = (
                max(0, min(self.LOGICAL_WIDTH, proposed[0])),
                max(0, min(self.LOGICAL_HEIGHT, proposed[1])),
            )
            self._at_edge = self._position != proposed
            if self._position != self._trail[-1]:
                self._trail.append(self._position)
        self._update_position_text()
        result = f"Last preview: {command.trigger} — {command.action_summary}."
        if self._at_edge:
            result += " Practice edge reached; reset the pad to start from center."
        self.summary_var.set(result)
        self.summary_label.configure(foreground=_TEXT)
        self._request_draw()

    def show_rejection(self, message: str) -> None:
        """Show an approved refusal without moving the virtual pointer."""
        if type(message) is not str:
            raise TypeError("Practice feedback must be an approved display string.")
        # Bound presentation only. This does not redact secrets: the controller
        # must continue passing fixed, approved reasons instead of raw input.
        text = " ".join(message[:self.MAX_REJECTION_LENGTH].splitlines()).strip()
        text = "".join(character for character in text
                       if character.isprintable() and not 0x202A <= ord(character) <= 0x202E
                       and not 0x2066 <= ord(character) <= 0x2069)
        self._last_command = None
        self._rejected = True
        self._at_edge = False
        self.summary_var.set("Not run: " + (text or "This command was not accepted."))
        self.summary_label.configure(foreground=_REJECTED)
        self._request_draw()

    def reset(self) -> None:
        """Reset only the illustration, never the session or its policy history."""
        self._position = (self.LOGICAL_WIDTH // 2, self.LOGICAL_HEIGHT // 2)
        self._trail.clear()
        self._trail.append(self._position)
        self._last_command = None
        self._rejected = False
        self._at_edge = False
        self.summary_var.set("Pad reset. Try another command in Preview; session limits are unchanged.")
        self._update_position_text()
        self.summary_label.configure(foreground=_TEXT)
        self._request_draw()

    def _update_position_text(self) -> None:
        x = self._position[0] - self.LOGICAL_WIDTH // 2
        y = self._position[1] - self.LOGICAL_HEIGHT // 2
        parts = []
        if x:
            parts.append(f"{abs(x)} {'right' if x > 0 else 'left'}")
        if y:
            parts.append(f"{abs(y)} {'down' if y > 0 else 'up'}")
        self.position_var.set("Practice pointer: " +
                              (" / ".join(parts) + " units from center." if parts else "center."))

    def _layout(self, event: Any) -> None:
        width = max(160, event.width - 4)
        for label in (self.preview_label, self.summary_label, self.position_label, self.scale_label):
            if label.cget("wraplength") != width:
                label.configure(wraplength=width)

    def _request_draw(self, _event: Any = None) -> None:
        if not self._closed and self._draw_job is None:
            self._draw_job = self._callback_owner.after_idle(self._draw)

    def _draw(self) -> None:
        self._draw_job = None
        if self._closed:
            return
        width, height = self.canvas.winfo_width(), self.canvas.winfo_height()
        if width < 80 or height < 60:
            return
        signature = (width, height, self._position, self.trail, self._last_command, self._rejected, self._at_edge)
        if signature == self._draw_signature:
            return
        self._draw_signature = signature
        self.canvas.delete("all")
        left, top, right, bottom = 24, 36, width - 24, height - 18

        def screen(point: tuple[int, int]) -> tuple[float, float]:
            return (left + point[0] * (right - left) / self.LOGICAL_WIDTH,
                    top + point[1] * (bottom - top) / self.LOGICAL_HEIGHT)

        self.canvas.create_rectangle(left, top, right, bottom, outline=_GRID)
        mid_x, mid_y = screen((self.LOGICAL_WIDTH // 2, self.LOGICAL_HEIGHT // 2))
        self.canvas.create_line(left, mid_y, right, mid_y, fill=_GRID, dash=(3, 6))
        self.canvas.create_line(mid_x, top, mid_x, bottom, fill=_GRID, dash=(3, 6))
        if len(self._trail) > 1:
            points = [coordinate for point in self._trail for coordinate in screen(point)]
            self.canvas.create_line(*points, fill=_TRAIL, width=2, tags="trail")
        x, y = screen(self._position)
        self.canvas.create_line(x - 11, y, x + 11, y, fill=_ACCENT)
        self.canvas.create_line(x, y - 11, x, y + 11, fill=_ACCENT)
        self.canvas.create_oval(x - 5, y - 5, x + 5, y + 5, fill=_ACCENT, outline=_TEXT, tags="pointer")
        command = self._last_command
        badge, color = ("Not run", _REJECTED) if self._rejected else ("Virtual pointer", _MUTED)
        if command is not None:
            badge, color = command.action_label, _ACCENT
            if command.action_type == "mouse_button":
                color = _BUTTON
                self.canvas.create_oval(x - 14, y - 14, x + 14, y + 14, outline=color,
                                        width=2, tags="button-mark")
            elif command.action_type == "mouse_wheel":
                arrow_x = max(left + 16, min(right - 16, x + 26))
                arrow_y = max(top + 18, min(bottom - 18, y))
                direction = -1 if command.wheel > 0 else 1
                self.canvas.create_line(arrow_x, arrow_y - direction * 14,
                                        arrow_x, arrow_y + direction * 14,
                                        fill=color, width=3, arrow="last", tags="wheel-mark")
            elif command.action_type == "key":
                badge = f"Key: {command.action_label}"
        # A persistent last-action badge describes what was simulated. It is
        # deliberately not an animated "held" indicator or real-time timer.
        label = self.canvas.create_text(16, 15, text=badge, fill=color, anchor="w", tags="action-badge")
        if command is not None and command.action_type == "key":
            box = self.canvas.bbox(label)
            if box:
                keycap = self.canvas.create_rectangle(box[0] - 5, box[1] - 3, box[2] + 5, box[3] + 3,
                                                      fill="#25364e", outline=_ACCENT, tags="key-mark")
                self.canvas.tag_lower(keycap, label)

    def _destroyed(self, event: Any) -> None:
        if event.widget is self:
            self._closed = True
            if self._draw_job is not None:
                self._callback_owner.after_cancel(self._draw_job)
                self._draw_job = None
