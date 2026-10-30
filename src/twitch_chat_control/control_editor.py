"""Local command configuration. This component never sends operating-system input."""

from __future__ import annotations

import tkinter as tk
from dataclasses import replace
from tkinter import messagebox, ttk
from typing import Any, Callable

from .models import MAX_COMMANDS, SAFE_KEYS, Command, Profile, normalize_command

KEY_LABELS = {key: key.upper() for key in SAFE_KEYS if len(key) == 1}
KEY_LABELS.update({"space": "Space", "up": "Arrow Up", "down": "Arrow Down",
                   "left": "Arrow Left", "right": "Arrow Right"})
LABEL_KEYS = {label: key for key, label in KEY_LABELS.items()}
KEY_CHOICES = tuple(KEY_LABELS[key] for key in ("space", "up", "down", "left", "right")) + tuple(
    KEY_LABELS[key] for key in sorted(SAFE_KEYS) if len(key) == 1
)
# Tk uses different Alt/Meta masks on X11 and Windows. Lock/NumLock are not chords.
_MODIFIER_MASK = 0x0001 | 0x0004 | 0x0008 | 0x0020 | 0x0040 | 0x0080 | 0x20000


def captured_key(keysym: Any, state: Any) -> str | None:
    """Translate one unmodified Tk key event to an already-approved game key."""
    if type(keysym) is not str or type(state) is not int or state & _MODIFIER_MASK:
        return None
    value = keysym.lower()
    return value if value in SAFE_KEYS else None


def unique_trigger(commands: tuple[Command, ...], base: str = "!action") -> str:
    """Create a valid name without changing any existing mapping."""
    existing = {command.trigger for command in commands}
    if base not in existing and normalize_command(base) == base:
        return base
    stem = base[:20]
    for index in range(2, MAX_COMMANDS + 3):
        candidate = f"{stem}_{index}"
        if candidate not in existing:
            return candidate
    raise ValueError("No unique command name is available.")


class CommandEditor(ttk.Frame):
    """An immutable command list plus an explicit, locally edited draft.

    The parent owns the live policy and profile file. ``on_change`` must apply
    the entire validated tuple atomically and return True on success. Drafts
    remain intact on failure. ``on_edit`` must disarm the parent's live engine.
    ``on_preview`` must be a dry run; there is no native backend in this module.
    """

    def __init__(
        self,
        parent: tk.Misc,
        *,
        on_change: Callable[[tuple[Command, ...]], bool],
        on_edit: Callable[[], None],
        on_preview: Callable[[Command], str | None],
        on_panic: Callable[[], None],
        on_notice: Callable[[str], None],
    ) -> None:
        super().__init__(parent)
        self._on_change = on_change
        self._on_edit = on_edit
        self._on_preview = on_preview
        self._on_panic = on_panic
        self._on_notice = on_notice
        self._commands: tuple[Command, ...] = ()
        self._selected_trigger: str | None = None
        self._return_trigger: str | None = None
        self._new = False
        self._dirty = False
        self._loading = False
        self._capture_active = False
        self._baseline: tuple[Any, ...] = ()
        self._blocked = False

        self.trigger_var = tk.StringVar()
        self.key_var = tk.StringVar(value="Space")
        self.hold_var = tk.StringVar(value="100")
        self.cooldown_var = tk.StringVar(value="500")
        self.enabled_var = tk.BooleanVar(value=True)
        self.filter_var = tk.StringVar()
        self.count_var = tk.StringVar()
        self.heading_var = tk.StringVar(value="Select or create a command")
        self.summary_var = tk.StringVar()
        self.error_var = tk.StringVar()
        self.capture_var = tk.StringVar()
        self.preview_var = tk.StringVar()
        self._build()
        for var in (self.trigger_var, self.key_var, self.hold_var, self.cooldown_var, self.enabled_var):
            var.trace_add("write", self._draft_changed)
        self.filter_var.trace_add("write", self._filter_changed)
        self.bind("<Unmap>", self._capture_unmapped)
        self.set_commands(())

    @property
    def dirty(self) -> bool:
        return self._dirty

    @property
    def commands(self) -> tuple[Command, ...]:
        return self._commands

    @property
    def capture_active(self) -> bool:
        return self._capture_active

    @property
    def selected_trigger(self) -> str | None:
        return self._selected_trigger

    def _build(self) -> None:
        self.columnconfigure(0, weight=1, minsize=330)
        self.columnconfigure(1, weight=1, minsize=370)
        self.rowconfigure(0, weight=1)
        left = ttk.Frame(self, padding=(0, 0, 18, 0))
        left.grid(row=0, column=0, sticky="nsew")
        left.columnconfigure(0, weight=1)
        left.rowconfigure(2, weight=1)
        ttk.Label(left, textvariable=self.count_var, style="Heading.TLabel").grid(row=0, column=0, sticky="w", pady=(0, 7))
        search = ttk.Frame(left)
        search.grid(row=1, column=0, sticky="ew", pady=(0, 8))
        ttk.Label(search, text="Find").pack(side="left", padx=(0, 8))
        ttk.Entry(search, textvariable=self.filter_var, width=18).pack(side="left", fill="x", expand=True)

        table = ttk.Frame(left)
        table.grid(row=2, column=0, sticky="nsew")
        self.tree = ttk.Treeview(table, columns=("trigger", "key", "hold", "enabled"), show="headings", selectmode="browse", height=6)
        for name, label, width in (("trigger", "Chat command", 138), ("key", "Key", 85), ("hold", "Hold", 55), ("enabled", "On", 38)):
            self.tree.heading(name, text=label)
            self.tree.column(name, width=width, minwidth=width, stretch=name == "trigger", anchor="w")
        bar = ttk.Scrollbar(table, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=bar.set)
        bar.pack(side="right", fill="y")
        self.tree.pack(side="left", fill="both", expand=True)
        self.tree.bind("<<TreeviewSelect>>", self._selection_changed)

        actions = ttk.Frame(left)
        actions.grid(row=3, column=0, sticky="ew", pady=(10, 0))
        self.new_button = ttk.Button(actions, text="New", command=self.new_command)
        self.new_button.pack(side="left", padx=(0, 5))
        self.duplicate_button = ttk.Button(actions, text="Duplicate", command=self.duplicate_command)
        self.duplicate_button.pack(side="left", padx=(0, 5))
        self.remove_button = ttk.Button(actions, text="Remove", command=self.remove_command)
        self.remove_button.pack(side="left")
        ttk.Label(left, text="Changes pause live control. Apply a mapping, then save its profile.", style="Muted.TLabel", wraplength=350).grid(row=4, column=0, sticky="w", pady=(9, 0))

        right = ttk.Frame(self, padding=(18, 0, 0, 0))
        right.grid(row=0, column=1, sticky="nsew")
        right.columnconfigure(0, weight=1)
        ttk.Label(right, textvariable=self.heading_var, style="Heading.TLabel").grid(row=0, column=0, sticky="w", pady=(0, 9))
        form = ttk.Frame(right)
        form.grid(row=1, column=0, sticky="ew")
        form.columnconfigure(0, weight=1)
        form.columnconfigure(1, weight=1)
        ttk.Label(form, text="Chat command").grid(row=0, column=0, columnspan=2, sticky="w")
        self.trigger_entry = ttk.Entry(form, textvariable=self.trigger_var, width=18)
        self.trigger_entry.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(3, 8))
        ttk.Label(form, text="Game control").grid(row=2, column=0, columnspan=2, sticky="w")
        self.key_combo = ttk.Combobox(form, textvariable=self.key_var, values=KEY_CHOICES, state="readonly", width=14)
        self.key_combo.grid(row=3, column=0, sticky="ew", pady=(3, 8), padx=(0, 7))
        self.capture_button = ttk.Button(form, text="Press a key", command=self.start_capture)
        self.capture_button.grid(row=3, column=1, sticky="ew", pady=(3, 8))
        self.capture_entry = ttk.Entry(form, textvariable=self.capture_var, state="readonly", takefocus=False)
        self.capture_entry.grid(row=4, column=0, columnspan=2, sticky="ew", pady=(0, 8))
        self.capture_entry.grid_remove()
        self.capture_entry.bind("<KeyPress>", self._capture_keypress)
        self.capture_entry.bind("<FocusOut>", self._capture_focus_lost)
        self.capture_entry.bind("<Unmap>", self._capture_unmapped)
        ttk.Label(form, text="Hold (10-450 ms)").grid(row=5, column=0, sticky="w")
        ttk.Label(form, text="Cooldown (0-60,000 ms)").grid(row=5, column=1, sticky="w")
        self.hold_entry = ttk.Spinbox(form, from_=10, to=450, increment=10, textvariable=self.hold_var, width=10)
        self.hold_entry.grid(row=6, column=0, sticky="ew", padx=(0, 7), pady=(3, 5))
        self.cooldown_entry = ttk.Spinbox(form, from_=0, to=60_000, increment=100, textvariable=self.cooldown_var, width=10)
        self.cooldown_entry.grid(row=6, column=1, sticky="ew", pady=(3, 5))
        enabled = ttk.Frame(form)
        enabled.grid(row=7, column=0, columnspan=2, sticky="ew")
        self.enabled_check = ttk.Checkbutton(enabled, text="Command enabled", variable=self.enabled_var)
        self.enabled_check.pack(side="left")
        self.toggle_button = ttk.Button(enabled, text="Disable / enable", command=self.toggle_command)
        self.toggle_button.pack(side="right")
        ttk.Label(right, textvariable=self.summary_var, style="Muted.TLabel", wraplength=400).grid(row=2, column=0, sticky="ew", pady=(8, 3))
        ttk.Label(right, textvariable=self.error_var, foreground="#ffaaaa", wraplength=400).grid(row=3, column=0, sticky="ew", pady=(0, 4))
        controls = ttk.Frame(right)
        controls.grid(row=4, column=0, sticky="ew", pady=(0, 7))
        self.apply_button = ttk.Button(controls, text="Apply", style="Accent.TButton", command=self.apply_pending)
        self.apply_button.pack(side="left", padx=(0, 6))
        self.revert_button = ttk.Button(controls, text="Revert", command=self.discard_pending)
        self.revert_button.pack(side="left", padx=(0, 6))
        self.preview_button = ttk.Button(controls, text="Preview", command=self.preview_command)
        self.preview_button.pack(side="left")
        ttk.Label(right, textvariable=self.preview_var, style="Muted.TLabel", wraplength=400).grid(row=5, column=0, sticky="ew")

    def _values(self) -> tuple[Any, ...]:
        return (self.trigger_var.get(), self.key_var.get(), self.hold_var.get(),
                self.cooldown_var.get(), self.enabled_var.get())

    def _pause(self) -> bool:
        try:
            self._on_edit()
            return not self._blocked
        except Exception:
            self._blocked = True
            self.error_var.set("Controls could not be paused. Restart the app before changing mappings.")
            self.apply_button.state(["disabled"])
            return False

    def _draft_changed(self, *_args: Any) -> None:
        if self._loading:
            return
        self._pause()
        self._dirty = self._new or self._values() != self._baseline
        self.preview_var.set("")
        self._validate_draft()
        self._summarize()

    def _validate_draft(self) -> Command | None:
        trigger = self.trigger_var.get()
        error = ""
        if normalize_command(trigger) != trigger:
            error = "Chat command: use ! followed by 1-24 lowercase letters, digits, _ or -. Start with a letter."
        elif any(command.trigger == trigger and command.trigger != self._selected_trigger for command in self._commands):
            error = "Chat command: that name is already assigned. Choose a unique name."
        key = LABEL_KEYS.get(self.key_var.get())
        if not error and key is None:
            error = "Game control: choose an approved key from the list."
        numbers: dict[str, int] = {}
        for label, var, low, high in (("Hold", self.hold_var, 10, 450), ("Cooldown", self.cooldown_var, 0, 60_000)):
            raw = var.get()
            if len(raw) > 6 or not raw.isascii() or not raw.isdecimal() or not low <= int(raw) <= high:
                if not error:
                    error = f"{label}: enter a whole number from {low:,} to {high:,} ms."
            else:
                numbers[label] = int(raw)
        if error:
            self.error_var.set(error)
            return None
        try:
            candidate = Command(trigger, key, numbers["Hold"], numbers["Cooldown"], self.enabled_var.get())
        except (ValueError, TypeError, tk.TclError):
            self.error_var.set("The mapping is invalid. Review its command, key, and timings.")
            return None
        self.error_var.set("")
        return candidate

    def _summarize(self) -> None:
        if self._selected_trigger is None and not self._new:
            self.summary_var.set("Create a command to choose its game control.")
            return
        suffix = " · unapplied changes" if self._dirty else ""
        self.summary_var.set(f"{self.trigger_var.get()[:25]}  ->  {self.key_var.get()[:16]} for {self.hold_var.get()[:6]} ms{suffix}")

    def _load_form(self, command: Command | None, *, new: bool = False) -> None:
        self.cancel_capture()
        self._loading = True
        self._new = new
        self._selected_trigger = None if new or command is None else command.trigger
        current = command or Command("!action", "space")
        self.trigger_var.set(current.trigger if command is not None else "")
        self.key_var.set(KEY_LABELS[current.key])
        self.hold_var.set(str(current.hold_ms))
        self.cooldown_var.set(str(current.cooldown_ms))
        self.enabled_var.set(current.enabled)
        self._baseline = self._values()
        self._dirty = new
        self._loading = False
        self.error_var.set("")
        self.preview_var.set("")
        self.heading_var.set("New command" if new else ("Edit mapping" if command else "Create your first command"))
        active = command is not None
        for widget in (self.trigger_entry, self.hold_entry, self.cooldown_entry, self.enabled_check, self.capture_button, self.apply_button, self.revert_button, self.preview_button):
            widget.state(["!disabled"] if active else ["disabled"])
        self.key_combo.configure(state="readonly" if active else "disabled")
        for widget in (self.duplicate_button, self.remove_button, self.toggle_button):
            widget.state(["!disabled"] if self._selected_trigger else ["disabled"])
        if self._blocked:
            self.apply_button.state(["disabled"])
        self._summarize()

    def _render_table(self) -> None:
        query = self.filter_var.get().lower()[:80]
        for row in self.tree.get_children():
            self.tree.delete(row)
        for command in self._commands:
            if query and query not in command.trigger.lower() and query not in KEY_LABELS[command.key].lower():
                continue
            self.tree.insert("", "end", iid=command.trigger, values=(command.trigger, KEY_LABELS[command.key], f"{command.hold_ms} ms", "Yes" if command.enabled else "No"))
        self.count_var.set(f"Commands · {len(self._commands)}/{MAX_COMMANDS}")
        self._restore_selection()

    def _restore_selection(self) -> None:
        if self._selected_trigger and self.tree.exists(self._selected_trigger):
            self.tree.selection_set(self._selected_trigger)
            self.tree.focus(self._selected_trigger)
            self.tree.see(self._selected_trigger)
        else:
            self.tree.selection_remove(self.tree.selection())

    def _filter_changed(self, *_args: Any) -> None:
        if not self._loading:
            self._render_table()

    def set_commands(self, commands: tuple[Command, ...], *, select_trigger: str | None = None) -> None:
        Profile(commands=commands)  # Shared model validation, including collisions and size.
        self.cancel_capture()
        previous = select_trigger if select_trigger is not None else self._selected_trigger
        self._commands = commands
        selected = next((command for command in commands if command.trigger == previous), None)
        if selected is None and commands:
            selected = commands[0]
        self._load_form(selected)
        self._render_table()

    def _resolve_pending(self) -> bool:
        if not self._dirty:
            return True
        self.cancel_capture()
        answer = messagebox.askyesnocancel(
            "Unapplied command changes",
            "Apply the command before changing your selection?\n\nYes applies it. No discards this draft. Cancel keeps editing.",
            parent=self.winfo_toplevel(),
        )
        if answer is None:
            return False
        if answer:
            return self.apply_pending()
        self.discard_pending()
        return True

    def _selection_changed(self, _event: Any = None) -> None:
        if self._loading:
            return
        selection = self.tree.selection()
        if not selection:
            return
        target = selection[0]
        if target == self._selected_trigger and not self._new:
            return
        if not self._resolve_pending():
            self._restore_selection()
            return
        selected = next((command for command in self._commands if command.trigger == target), None)
        if selected is not None:
            self._load_form(selected)
            self._restore_selection()

    def _commit(self, commands: tuple[Command, ...], selected: str | None) -> bool:
        if not self._pause():
            return False
        try:
            Profile(commands=commands)
            accepted = self._on_change(commands)
        except Exception:
            accepted = False
        if accepted is not True:
            self.error_var.set("Changes were not applied. Review the profile's safety settings and try again.")
            return False
        self.set_commands(commands, select_trigger=selected)
        self._on_notice("Mapping applied. Controls remain disarmed; save the profile to keep your changes.")
        return True

    def apply_pending(self) -> bool:
        self.cancel_capture()
        if not self._dirty:
            return not self._blocked
        candidate = self._validate_draft()
        if candidate is None:
            return False
        commands = list(self._commands)
        if self._new:
            if len(commands) >= MAX_COMMANDS:
                self.error_var.set(f"A profile supports at most {MAX_COMMANDS} commands.")
                return False
            commands.append(candidate)
        else:
            index = next((index for index, command in enumerate(commands) if command.trigger == self._selected_trigger), None)
            if index is None:
                self.error_var.set("The selected command changed. Select it again before editing.")
                return False
            commands[index] = candidate
        return self._commit(tuple(commands), candidate.trigger)

    def discard_pending(self) -> None:
        selected = self._return_trigger if self._new else self._selected_trigger
        self.set_commands(self._commands, select_trigger=selected)

    def new_command(self) -> None:
        if not self._resolve_pending():
            return
        if len(self._commands) >= MAX_COMMANDS:
            self.error_var.set(f"A profile supports at most {MAX_COMMANDS} commands. Remove one first.")
            return
        if not self._pause():
            return
        self._return_trigger = self._selected_trigger
        self._load_form(Command(unique_trigger(self._commands), "space"), new=True)
        self._restore_selection()
        self.trigger_entry.focus_set()
        self.trigger_entry.selection_range(0, "end")

    def duplicate_command(self) -> None:
        if not self._resolve_pending():
            return
        current = next((command for command in self._commands if command.trigger == self._selected_trigger), None)
        if current is None:
            return
        if len(self._commands) >= MAX_COMMANDS:
            self.error_var.set(f"A profile supports at most {MAX_COMMANDS} commands. Remove one first.")
            return
        if not self._pause():
            return
        self._return_trigger = current.trigger
        name = unique_trigger(self._commands, current.trigger[:20] + "_copy")
        self._load_form(replace(current, trigger=name), new=True)
        self._restore_selection()

    def toggle_command(self) -> None:
        if not self._resolve_pending():
            return
        current = next((command for command in self._commands if command.trigger == self._selected_trigger), None)
        if current is None:
            return
        commands = tuple(replace(command, enabled=not command.enabled) if command is current else command for command in self._commands)
        self._commit(commands, current.trigger)

    def remove_command(self) -> None:
        if not self._resolve_pending():
            return
        current = next((command for command in self._commands if command.trigger == self._selected_trigger), None)
        if current is None:
            return
        if not messagebox.askyesno("Remove command", f"Remove {current.trigger} from this profile?", parent=self.winfo_toplevel()):
            return
        self._commit(tuple(command for command in self._commands if command is not current), None)

    def start_capture(self) -> None:
        if self._capture_active:
            self.cancel_capture()
            return
        if (not self._new and self._selected_trigger is None) or not self._pause():
            return
        self._capture_active = True
        self.capture_var.set("Press one game key. Esc cancels; F8 stops.")
        self.capture_entry.grid()
        self.capture_button.configure(text="Listening...")
        self.capture_button.state(["disabled"])
        self.capture_entry.focus_set()

    def cancel_capture(self) -> None:
        self._capture_active = False
        self.capture_var.set("")
        self.capture_entry.grid_remove()
        self.capture_button.configure(text="Press a key")
        self.capture_button.state(["!disabled"] if self._new or self._selected_trigger else ["disabled"])

    def _capture_focus_lost(self, _event: Any) -> None:
        # A queued FocusOut from a previous capture must not cancel a newly
        # focused capture. The actual current focus is authoritative.
        if self.focus_get() is not self.capture_entry:
            self.cancel_capture()

    def _capture_unmapped(self, _event: Any) -> None:
        if not self.winfo_ismapped() or not self.capture_entry.winfo_ismapped():
            self.cancel_capture()

    def _capture_keypress(self, event: Any) -> str | None:
        if not self._capture_active:
            return None
        # Do not depend on the root binding: returning 'break' below would stop it.
        if event.keysym == "F8":
            self.cancel_capture()
            self._on_panic()
            return "break"
        if event.keysym == "Escape":
            self.cancel_capture()
            return "break"
        if event.widget is not self.capture_entry or self.focus_get() is not self.capture_entry:
            self.cancel_capture()
            return "break"
        key = captured_key(event.keysym, event.state)
        if key is None:
            self.capture_var.set("Use a letter, number, arrow, or Space without modifiers.")
            return "break"
        self.key_var.set(KEY_LABELS[key])
        self.cancel_capture()
        self._on_notice(f"Game key selected: {KEY_LABELS[key]}. Apply the mapping when ready.")
        return "break"

    def preview_command(self) -> None:
        self.cancel_capture()
        if not self.apply_pending():
            return
        current = next((command for command in self._commands if command.trigger == self._selected_trigger), None)
        if current is None or not self._pause():
            return
        try:
            result = self._on_preview(current)
        except Exception:
            self.preview_var.set("Preview could not run. Controls remain disarmed.")
            return
        self.preview_var.set(result[:300] if isinstance(result, str) else "Preview requested. No keyboard input is sent.")
