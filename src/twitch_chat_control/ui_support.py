"""Small GUI-only helpers for bounded rendering and predictable scrolling.

These helpers do not accept chat directly, inspect credentials, or send input.
Activity messages must already be the application's approved display strings.
"""

from __future__ import annotations

import tkinter as tk
from collections import deque
from datetime import datetime
from tkinter import ttk
from typing import Any, Iterable

ACTIVITY_LIMIT = 200
ACTIVITY_MESSAGE_LIMIT = 350


def append_log_lines(
    widget: tk.Text,
    messages: Iterable[str],
    *,
    max_lines: int = ACTIVITY_LIMIT,
    timestamp: datetime | None = None,
) -> None:
    """Append one bounded batch without stealing a reader's scroll position.

    One insert replaces per-event Text mutations. Both incoming batches and
    retained history are bounded. This is presentation formatting, not a secret
    scrubber; callers must continue passing only approved activity messages.
    """
    if type(max_lines) is not int or not 1 <= max_lines <= 2000:
        raise ValueError("Activity history must contain between 1 and 2000 lines.")
    stamp = (timestamp if timestamp is not None else datetime.now()).strftime("%H:%M:%S")
    lines: deque[str] = deque(maxlen=max_lines)
    for message in messages:
        if type(message) is not str:
            raise TypeError("Activity messages must be strings.")
        # One approved event occupies one bounded line, even if its local
        # formatting accidentally contains a line break.
        text = " ".join(message[:ACTIVITY_MESSAGE_LIMIT].splitlines())
        lines.append(f"{stamp}  {text}\n")
    if not lines:
        return
    follow_tail = widget.yview()[1] >= 0.999
    widget.configure(state="normal")
    try:
        widget.insert("end", "".join(lines))
        # Text always includes a trailing empty line. Retain max_lines events.
        excess = int(widget.index("end-1c").split(".", 1)[0]) - 1 - max_lines
        if excess > 0:
            widget.delete("1.0", f"{excess + 1}.0")
        if follow_tail:
            widget.see("end")
    finally:
        widget.configure(state="disabled")


def clear_log(widget: tk.Text) -> None:
    """Clear the read-only activity display without leaving it editable."""
    widget.configure(state="normal")
    try:
        widget.delete("1.0", "end")
    finally:
        widget.configure(state="disabled")


def set_var_if_changed(variable: tk.Variable, value: Any) -> bool:
    """Avoid repeated Tcl writes and variable traces for unchanged status."""
    if variable.get() == value:
        return False
    variable.set(value)
    return True


def configure_if_changed(widget: tk.Misc, **options: Any) -> bool:
    """Configure only differing widget options, such as status/button text."""
    changed = {}
    for name, value in options.items():
        current, desired = widget.cget(name), value
        if name == "values" and isinstance(value, (tuple, list)):
            # Tk returns an empty choice list as "", and nonempty choices as
            # a tuple of strings, regardless of the caller's sequence type.
            current = tuple(str(item) for item in widget.tk.splitlist(current))
            desired = tuple(str(item) for item in value)
        if current != desired:
            changed[name] = value
    if not changed:
        return False
    widget.configure(**changed)
    return True


def set_enabled(widget: ttk.Widget, enabled: bool) -> bool:
    """Toggle the disabled bit only when needed, preserving other ttk state."""
    disabled = widget.instate(("disabled",))
    if disabled == (not enabled):
        return False
    widget.state(("!disabled",) if enabled else ("disabled",))
    return True


class PageWheelRouter:
    """Route wheels before editable controls' default class bindings.

    A dedicated bindtag avoids application-wide wheel hooks. Nested Text,
    Treeview, Listbox and scrollbar controls keep their native wheel handling;
    scrolling over fields moves the page without changing a field's value.
    """

    _NATIVE_SCROLL_CLASSES = frozenset({"Text", "Treeview", "Listbox", "Scrollbar", "TScrollbar"})
    _SEQUENCES = ("<MouseWheel>", "<Button-4>", "<Button-5>")

    def __init__(self, page: tk.Misc, canvas: tk.Canvas, content: tk.Misc) -> None:
        self.page = page
        self.canvas = canvas
        self.content = content
        self._tag = f"TwitchPageWheel:{page}"
        self._closed = False
        for sequence in self._SEQUENCES:
            page.bind_class(self._tag, sequence, self._wheel)
        page.bind("<Destroy>", self._destroyed, add="+")
        self.refresh()

    def refresh(self) -> None:
        """Include newly created page widgets without installing duplicate tags."""
        if self._closed:
            return
        pending = [self.page]
        while pending:
            widget = pending.pop()
            tags = widget.bindtags()
            if self._tag not in tags:
                # Keep widget-specific bindings first; intercept before ttk
                # class handlers which otherwise alter Spinbox/Combobox values.
                widget.bindtags((tags[0], self._tag, *tags[1:]))
            pending.extend(widget.winfo_children())

    def _wheel(self, event: Any) -> str | None:
        if self._closed or not self.page.winfo_ismapped():
            return None
        if event.widget.winfo_class() in self._NATIVE_SCROLL_CLASSES:
            return None
        number = getattr(event, "num", None)
        if number in (4, 5):
            units = -3 if number == 4 else 3
        else:
            delta = getattr(event, "delta", 0)
            if not delta:
                return "break"
            units = -int(delta / 120) * 3 if abs(delta) >= 120 else (-1 if delta > 0 else 1)
            units = max(-12, min(12, units))
        if self.content.winfo_reqheight() > self.canvas.winfo_height():
            self.canvas.yview_scroll(units, "units")
        # Even a page that fits must not change a number or selection by wheel.
        return "break"

    def _destroyed(self, event: Any) -> None:
        if event.widget is self.page and not self._closed:
            self._closed = True
            for sequence in self._SEQUENCES:
                self.page.unbind_class(self._tag, sequence)


def install_page_wheel_routing(page: tk.Misc, canvas: tk.Canvas, content: tk.Misc) -> PageWheelRouter:
    """Install after building a page; call the returned router's refresh if needed."""
    return PageWheelRouter(page, canvas, content)
