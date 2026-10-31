"""Local command configuration. This component never sends operating-system input."""

from __future__ import annotations

import tkinter as tk
import tkinter.font as tkfont
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
ACTION_LABELS = {"key": "Keyboard key", "mouse_button": "Mouse button",
                 "mouse_move": "Mouse movement", "mouse_wheel": "Mouse wheel"}
LABEL_ACTIONS = {label: value for value, label in ACTION_LABELS.items()}
BUTTON_LABELS = {"left": "Left mouse", "right": "Right mouse", "middle": "Middle mouse",
                 "x1": "Side button 1", "x2": "Side button 2"}
LABEL_BUTTONS = {label: value for value, label in BUTTON_LABELS.items()}


def action_label(command: Command, *, compact: bool = False) -> str:
    """Describe the approved action without exposing implementation fields."""
    if compact and command.action_type == "mouse_move":
        return f"Move ({command.dx:+}, {command.dy:+})"
    return command.action_label
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
        self._validation_feedback = False
        self._layout_mode: str | None = None
        self._layout_job: str | None = None
        self._layout_owner = self.winfo_toplevel()
        self._column_metrics_key: tuple[Any, ...] | None = None
        self._column_widths: tuple[int, ...] | None = None
        self._shown_action: str | None = None

        self.trigger_var = tk.StringVar()
        self.action_var = tk.StringVar(value=ACTION_LABELS["key"])
        self.key_var = tk.StringVar(value="Space")
        self.button_var = tk.StringVar(value=BUTTON_LABELS["left"])
        self.dx_var = tk.StringVar(value="25")
        self.dy_var = tk.StringVar(value="0")
        self.wheel_direction_var = tk.StringVar(value="Up")
        self.wheel_steps_var = tk.StringVar(value="1")
        self.hold_var = tk.StringVar(value="100")
        self.cooldown_var = tk.StringVar(value="500")
        self.enabled_var = tk.BooleanVar(value=True)
        self.filter_var = tk.StringVar()
        self.count_var = tk.StringVar()
        self.empty_var = tk.StringVar()
        self.draft_var = tk.StringVar()
        self._invalid_widget: ttk.Widget | None = None
        self.heading_var = tk.StringVar(value="Select or create a command")
        self.summary_var = tk.StringVar()
        self.error_var = tk.StringVar()
        self.capture_var = tk.StringVar()
        self.preview_var = tk.StringVar()
        self._build()
        for var in (self.trigger_var, self.action_var, self.key_var, self.button_var, self.dx_var, self.dy_var,
                    self.wheel_direction_var, self.wheel_steps_var, self.hold_var,
                    self.cooldown_var, self.enabled_var):
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
        self.columnconfigure(0, weight=9)
        self.columnconfigure(1, weight=11)
        self.rowconfigure(0, weight=1)
        left = ttk.Frame(self, style="Surface.TFrame", padding=16)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        left.columnconfigure(0, weight=1)
        left.rowconfigure(2, weight=1)
        heading = ttk.Frame(left, style="Card.TFrame")
        heading.grid(row=0, column=0, sticky="ew", pady=(0, 12))
        title = ttk.Frame(heading, style="Card.TFrame")
        title.pack(side="left", fill="x", expand=True)
        ttk.Label(title, text="Your commands", style="CardHeading.TLabel").pack(anchor="w")
        ttk.Label(title, textvariable=self.count_var, style="CardMuted.TLabel").pack(anchor="w", pady=(3, 0))
        self.new_button = ttk.Button(heading, text="New command", command=self.new_command)
        self.new_button.pack(side="right", padx=(8, 0))

        search = ttk.Frame(left, style="Card.TFrame")
        search.grid(row=1, column=0, sticky="ew", pady=(0, 10))
        ttk.Label(search, text="Search", style="Card.TLabel").pack(side="left", padx=(0, 8))
        self.search_entry = ttk.Entry(search, textvariable=self.filter_var, width=12)
        self.search_entry.pack(side="left", fill="x", expand=True)
        self.clear_search_button = ttk.Button(search, text="Clear", width=5, command=self.clear_search)
        self.clear_search_button.pack(side="left", padx=(6, 0))
        self.search_entry.bind("<Escape>", lambda _event: self.clear_search())

        table = ttk.Frame(left, style="Card.TFrame")
        table.grid(row=2, column=0, sticky="nsew")
        self.tree = ttk.Treeview(table, columns=("trigger", "key", "enabled"),
                                 show="headings", selectmode="browse", height=6)
        for name, label, width in (("trigger", "Chat command", 120), ("key", "Game action", 115),
                                   ("enabled", "Status", 74)):
            self.tree.heading(name, text=label)
            self.tree.column(name, width=width, minwidth=width, stretch=name == "trigger", anchor="w")
        bar = ttk.Scrollbar(table, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=bar.set)
        bar.pack(side="right", fill="y")
        self.tree.pack(side="left", fill="both", expand=True)
        self.tree.bind("<<TreeviewSelect>>", self._selection_changed)
        self.tree.bind("<Return>", self._focus_command)
        self.empty_table = ttk.Label(table, textvariable=self.empty_var, style="CardMuted.TLabel",
                                     justify="center", anchor="center", wraplength=250)

        actions = ttk.Frame(left, style="Card.TFrame")
        actions.grid(row=3, column=0, sticky="ew", pady=(10, 0))
        self.duplicate_button = ttk.Button(actions, text="Duplicate", command=self.duplicate_command)
        self.duplicate_button.pack(side="left", padx=(0, 7))
        self.remove_button = ttk.Button(actions, text="Remove", command=self.remove_command)
        self.remove_button.pack(side="left")
        ttk.Label(left, text="Editing pauses live control. Save your profile to keep changes.",
                  style="CardMuted.TLabel", wraplength=310).grid(row=4, column=0, sticky="w", pady=(10, 0))

        right = ttk.Frame(self, style="Surface.TFrame", padding=16)
        right.grid(row=0, column=1, sticky="nsew")
        right.columnconfigure(0, weight=1)
        ttk.Label(right, textvariable=self.heading_var, style="CardHeading.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(right, textvariable=self.draft_var, style="CardMuted.TLabel").grid(row=1, column=0, sticky="w", pady=(3, 12))
        self.form = form = ttk.Frame(right, style="Card.TFrame")
        form.grid(row=2, column=0, sticky="ew")
        form.columnconfigure(0, weight=1)
        form.columnconfigure(1, weight=1)
        ttk.Label(form, text="Chat command", style="Card.TLabel").grid(row=0, column=0, columnspan=2, sticky="w")
        self.trigger_entry = ttk.Entry(form, textvariable=self.trigger_var, width=18)
        self.trigger_entry.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(4, 3))
        ttk.Label(form, text="Start with !, for example !jump or !left.", style="CardMuted.TLabel").grid(
            row=2, column=0, columnspan=2, sticky="w", pady=(0, 10))
        ttk.Label(form, text="Game action", style="Card.TLabel").grid(row=3, column=0, columnspan=2, sticky="w")
        self.action_combo = ttk.Combobox(form, textvariable=self.action_var, values=tuple(ACTION_LABELS.values()),
                                         state="readonly", width=18)
        self.action_combo.grid(row=4, column=0, columnspan=2, sticky="ew", pady=(4, 10))

        self.key_fields = ttk.Frame(form, style="Card.TFrame")
        self.key_fields.grid(row=5, column=0, columnspan=2, sticky="ew")
        self.key_fields.columnconfigure(0, weight=1)
        self.key_fields.columnconfigure(1, weight=1)
        ttk.Label(self.key_fields, text="Game key", style="Card.TLabel").grid(row=0, column=0, columnspan=2, sticky="w")
        self.key_combo = ttk.Combobox(self.key_fields, textvariable=self.key_var, values=KEY_CHOICES, state="readonly", width=12)
        self.key_combo.grid(row=1, column=0, sticky="ew", pady=(4, 10), padx=(0, 8))
        self.capture_button = ttk.Button(self.key_fields, text="Press a key", command=self.start_capture)
        self.capture_button.grid(row=1, column=1, sticky="ew", pady=(4, 10))
        self.capture_row = ttk.Frame(self.key_fields, style="Card.TFrame")
        self.capture_row.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(0, 10))
        self.capture_row.columnconfigure(0, weight=1)
        self.capture_entry = ttk.Entry(self.capture_row, textvariable=self.capture_var,
                                       state="readonly", takefocus=False, width=20)
        self.capture_entry.grid(row=0, column=0, sticky="ew")
        self.cancel_capture_button = ttk.Button(self.capture_row, text="Cancel", width=6, command=self.cancel_capture)
        self.cancel_capture_button.grid(row=0, column=1, padx=(7, 0))
        ttk.Label(self.capture_row, text="No modifier keys. Esc cancels; F8 stops control.",
                  style="CardMuted.TLabel", wraplength=340).grid(row=1, column=0, columnspan=2,
                                                               sticky="w", pady=(5, 0))
        self.capture_row.grid_remove()
        self.capture_entry.bind("<KeyPress>", self._capture_keypress)
        self.capture_entry.bind("<FocusOut>", self._capture_focus_lost)
        self.capture_entry.bind("<Unmap>", self._capture_unmapped)
        self.button_fields = ttk.Frame(form, style="Card.TFrame")
        self.button_fields.grid(row=5, column=0, columnspan=2, sticky="ew")
        self.button_fields.columnconfigure(0, weight=1)
        ttk.Label(self.button_fields, text="Mouse button", style="Card.TLabel").grid(row=0, column=0, sticky="w")
        self.button_combo = ttk.Combobox(self.button_fields, textvariable=self.button_var,
                                         values=tuple(BUTTON_LABELS.values()), state="readonly", width=18)
        self.button_combo.grid(row=1, column=0, sticky="ew", pady=(4, 10))

        self.move_fields = ttk.Frame(form, style="Card.TFrame")
        self.move_fields.grid(row=5, column=0, columnspan=2, sticky="ew")
        self.move_fields.columnconfigure(0, weight=1)
        self.move_fields.columnconfigure(1, weight=1)
        ttk.Label(self.move_fields, text="Horizontal · units", style="Card.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(self.move_fields, text="Vertical · units", style="Card.TLabel").grid(row=0, column=1, sticky="w")
        self.dx_entry = ttk.Spinbox(self.move_fields, from_=-100, to=100, increment=5,
                                    textvariable=self.dx_var, width=10)
        self.dy_entry = ttk.Spinbox(self.move_fields, from_=-100, to=100, increment=5,
                                    textvariable=self.dy_var, width=10)
        self.dx_entry.grid(row=1, column=0, sticky="ew", padx=(0, 8), pady=(4, 3))
        self.dy_entry.grid(row=1, column=1, sticky="ew", pady=(4, 3))
        ttk.Label(self.move_fields, text="Left -  /  right +", style="CardMuted.TLabel").grid(row=2, column=0, sticky="w")
        ttk.Label(self.move_fields, text="Up -  /  down +", style="CardMuted.TLabel").grid(row=2, column=1, sticky="w")
        ttk.Label(self.move_fields, text="-100 to 100 per axis; at least one must move. Relative input units, not pixels: game and Windows sensitivity affect distance.",
                  style="CardMuted.TLabel", wraplength=340).grid(row=3, column=0, columnspan=2, sticky="w", pady=(7, 10))

        self.wheel_fields = ttk.Frame(form, style="Card.TFrame")
        self.wheel_fields.grid(row=5, column=0, columnspan=2, sticky="ew")
        self.wheel_fields.columnconfigure(0, weight=1)
        self.wheel_fields.columnconfigure(1, weight=1)
        ttk.Label(self.wheel_fields, text="Wheel direction", style="Card.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(self.wheel_fields, text="Steps", style="Card.TLabel").grid(row=0, column=1, sticky="w")
        self.wheel_direction_combo = ttk.Combobox(self.wheel_fields, textvariable=self.wheel_direction_var,
                                                  values=("Up", "Down"), state="readonly", width=10)
        self.wheel_steps_entry = ttk.Spinbox(self.wheel_fields, from_=1, to=3, increment=1,
                                             textvariable=self.wheel_steps_var, width=10)
        self.wheel_direction_combo.grid(row=1, column=0, sticky="ew", padx=(0, 8), pady=(4, 10))
        self.wheel_steps_entry.grid(row=1, column=1, sticky="ew", pady=(4, 10))

        self.hold_fields = ttk.Frame(form, style="Card.TFrame")
        self.hold_fields.grid(row=6, column=0, sticky="ew", padx=(0, 8))
        self.hold_fields.columnconfigure(0, weight=1)
        ttk.Label(self.hold_fields, text="Hold time · ms", style="Card.TLabel").grid(row=0, column=0, sticky="w")
        self.hold_entry = ttk.Spinbox(self.hold_fields, from_=10, to=450, increment=10, textvariable=self.hold_var, width=10)
        self.hold_entry.grid(row=1, column=0, sticky="ew", pady=(4, 3))
        ttk.Label(self.hold_fields, text="10 to 450 ms per press", style="CardMuted.TLabel").grid(row=2, column=0, sticky="w")
        self.cooldown_fields = ttk.Frame(form, style="Card.TFrame")
        self.cooldown_fields.grid(row=6, column=1, sticky="ew")
        self.cooldown_fields.columnconfigure(0, weight=1)
        ttk.Label(self.cooldown_fields, text="Cooldown · ms", style="Card.TLabel").grid(row=0, column=0, sticky="w")
        self.cooldown_entry = ttk.Spinbox(self.cooldown_fields, from_=0, to=60_000, increment=100,
                                          textvariable=self.cooldown_var, width=10)
        self.cooldown_entry.grid(row=1, column=0, sticky="ew", pady=(4, 3))
        ttk.Label(self.cooldown_fields, text="0 to 60,000 ms between uses", style="CardMuted.TLabel").grid(row=2, column=0, sticky="w")
        self.mouse_hint = ttk.Label(form, text="Live mouse actions require the pointer over the selected game's play area. Preview never moves or clicks your mouse.",
                                    style="CardMuted.TLabel", wraplength=340)
        self.mouse_hint.grid(row=7, column=0, columnspan=2, sticky="w", pady=(9, 0))
        self.enabled_check = ttk.Checkbutton(form, text="Enable this command", variable=self.enabled_var,
                                             style="Card.TCheckbutton")
        self.enabled_check.grid(row=8, column=0, columnspan=2, sticky="w", pady=(8, 0))
        self.summary_label = ttk.Label(right, textvariable=self.summary_var, style="CardMuted.TLabel", wraplength=390)
        self.summary_label.grid(row=3, column=0, sticky="ew", pady=(8, 8))
        self.error_label = ttk.Label(right, textvariable=self.error_var, style="Card.TLabel",
                                     foreground="#ffb4bd", wraplength=390)
        self.error_label.grid(row=4, column=0, sticky="ew", pady=(0, 8))
        self.controls = ttk.Frame(right, style="Card.TFrame")
        self.controls.grid(row=5, column=0, sticky="ew")
        self.apply_button = ttk.Button(self.controls, text="Apply command", style="Accent.TButton", command=self.apply_pending)
        self.apply_button.pack(side="left", padx=(0, 7))
        self.revert_button = ttk.Button(self.controls, text="Revert", command=self.discard_pending)
        self.revert_button.pack(side="left", padx=(0, 7))
        self.preview_button = ttk.Button(self.controls, text="Preview", command=self.preview_command)
        self.preview_button.pack(side="left")
        self.preview_label = ttk.Label(right, textvariable=self.preview_var, style="CardMuted.TLabel", wraplength=390)
        self.preview_label.grid(row=6, column=0, sticky="ew", pady=(10, 0))
        self.empty_detail = ttk.Frame(right, style="Card.TFrame", padding=(0, 16, 0, 32))
        self.empty_detail.grid(row=2, column=0, rowspan=5, sticky="new")
        ttk.Label(self.empty_detail, text="Give chat one simple action at a time.\n\nTry !jump for Space or !lookright for a small mouse movement.",
                  style="CardMuted.TLabel", wraplength=340, justify="left").pack(anchor="w", pady=(0, 18))
        ttk.Button(self.empty_detail, text="Create first command", style="Accent.TButton", command=self.new_command).pack(anchor="w")
        self.error_var.trace_add("write", self._feedback_changed)
        self.preview_var.trace_add("write", self._feedback_changed)
        for field in (self.trigger_entry, self.hold_entry, self.cooldown_entry,
                      self.dx_entry, self.dy_entry, self.wheel_steps_entry):
            field.bind("<Return>", self._apply_from_keyboard)
        for field in self._form_widgets():
            field.bind("<FocusOut>", self._field_left, add="+")
        self._left_card, self._right_card = left, right
        for widget in (self, left, right):
            widget.bind("<Configure>", self._schedule_layout, add="+")
        self.bind("<<ThemeChanged>>", self._theme_changed, add="+")
        self.bind("<Destroy>", self._layout_destroyed, add="+")
        self._schedule_layout()

    def _form_widgets(self) -> tuple[ttk.Widget, ...]:
        return (self.trigger_entry, self.action_combo, self.key_combo, self.button_combo,
                self.dx_entry, self.dy_entry, self.wheel_direction_combo, self.wheel_steps_entry,
                self.hold_entry, self.cooldown_entry)

    def _show_action_fields(self) -> None:
        action = LABEL_ACTIONS.get(self.action_var.get(), "")
        if action == self._shown_action:
            return
        self._shown_action = action
        if action != "key":
            self.cancel_capture()
        for kind, widget in (("key", self.key_fields), ("mouse_button", self.button_fields),
                             ("mouse_move", self.move_fields), ("mouse_wheel", self.wheel_fields)):
            widget.grid() if kind == action else widget.grid_remove()
        timed = action in {"key", "mouse_button"}
        self.hold_fields.grid() if timed else self.hold_fields.grid_remove()
        self.cooldown_fields.grid_configure(column=1 if timed else 0, columnspan=1 if timed else 2)
        self.mouse_hint.grid() if action.startswith("mouse_") else self.mouse_hint.grid_remove()
        self._schedule_layout()

    def _size_table_columns(self) -> bool:
        """Keep complete headings and key/status labels readable at larger text sizes."""
        style = ttk.Style(self)
        body_spec = style.lookup("Treeview", "font") or "TkDefaultFont"
        heading_spec = style.lookup("Treeview.Heading", "font") or body_spec
        metrics_key = (str(body_spec), str(heading_spec), self.tk.call("tk", "scaling"),
                       tuple((command.trigger, action_label(command, compact=True)) for command in self._commands))
        if metrics_key == self._column_metrics_key:
            return False
        self._column_metrics_key = metrics_key
        body = tkfont.Font(root=self, font=body_spec)
        heading = tkfont.Font(root=self, font=heading_spec)
        columns = (
            ("trigger", "Chat command", 120, (command.trigger for command in self._commands)),
            ("key", "Game action", 115,
             (*KEY_LABELS.values(), *BUTTON_LABELS.values(), "Move (-100, +100)", "Wheel down 3")),
            ("enabled", "Status", 74, ("Enabled", "Disabled")),
        )
        widths = tuple(max(minimum, heading.measure(label) + 24,
                           max((body.measure(value) + 20 for value in values), default=0))
                       for _, label, minimum, values in columns)
        if widths == self._column_widths:
            return False
        self._column_widths = widths
        for (name, _, _, _), width in zip(columns, widths, strict=True):
            self.tree.column(name, width=width, minwidth=width)
        return True

    def _schedule_layout(self, _event: Any = None) -> None:
        if self._layout_job is None:
            self._layout_job = self._layout_owner.after_idle(self._layout_cards)

    def _theme_changed(self, _event: Any = None) -> None:
        self._column_metrics_key = None
        self._schedule_layout()

    def _layout_destroyed(self, event: Any) -> None:
        if event.widget is self and self._layout_job is not None:
            self._layout_owner.after_cancel(self._layout_job)
            self._layout_job = None

    def _layout_cards(self) -> None:
        self._layout_job = None
        if self._size_table_columns():
            # Let Tk settle the new natural widths before choosing the layout.
            self._schedule_layout()
            return
        available = self.winfo_width()
        if available <= 1:
            return
        required = self._left_card.winfo_reqwidth() + self._right_card.winfo_reqwidth() + 10
        mode = "columns" if available >= required else "stacked"
        if mode == self._layout_mode:
            return
        self._layout_mode = mode
        if mode == "columns":
            self.columnconfigure(0, weight=9)
            self.columnconfigure(1, weight=11)
            self.rowconfigure(0, weight=1)
            self.rowconfigure(1, weight=0)
            self._left_card.grid_configure(row=0, column=0, padx=(0, 10), pady=0, sticky="nsew")
            self._right_card.grid_configure(row=0, column=1, padx=0, pady=0, sticky="nsew")
        else:
            self.columnconfigure(0, weight=1)
            self.columnconfigure(1, weight=0)
            self.rowconfigure(0, weight=0)
            self.rowconfigure(1, weight=0)
            self._left_card.grid_configure(row=0, column=0, padx=0, pady=(0, 10), sticky="ew")
            self._right_card.grid_configure(row=1, column=0, padx=0, pady=0, sticky="ew")

    def _focus_command(self, _event: Any = None) -> str:
        if self._new or self._selected_trigger:
            self.trigger_entry.focus_set()
            self.trigger_entry.selection_range(0, "end")
        return "break"

    def _apply_from_keyboard(self, _event: Any = None) -> str:
        self.apply_pending()
        return "break"

    def focus_problem(self) -> None:
        """Focus a validation problem after the parent reveals this page."""
        widget = self._invalid_widget or self.trigger_entry
        if self._new or self._selected_trigger is not None:
            widget.focus_set()

    def clear_search(self) -> str:
        self.filter_var.set("")
        self.search_entry.focus_set()
        return "break"

    def _feedback_changed(self, *_args: Any) -> None:
        active = self._new or self._selected_trigger is not None
        if active and self.error_var.get():
            self._validation_feedback = True
        for label, variable in ((self.error_label, self.error_var), (self.preview_label, self.preview_var)):
            if active and variable.get():
                label.grid()
            else:
                label.grid_remove()

    def _field_left(self, event: Any) -> None:
        # Give the user time to finish format-heavy values such as !jump or a
        # duration. Once feedback is shown, edits can clear it immediately.
        if self._loading or self._blocked or not self._dirty or self.focus_get() is event.widget:
            return
        if self._new or self._selected_trigger is not None:
            self._validate_draft()

    def _values(self) -> tuple[Any, ...]:
        action = LABEL_ACTIONS.get(self.action_var.get())
        fields = {
            "key": (self.key_var, self.hold_var),
            "mouse_button": (self.button_var, self.hold_var),
            "mouse_move": (self.dx_var, self.dy_var),
            "mouse_wheel": (self.wheel_direction_var, self.wheel_steps_var),
        }.get(action, ())
        return (self.trigger_var.get(), self.action_var.get(), tuple(var.get() for var in fields),
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
        self._show_action_fields()
        self._dirty = self._new or self._values() != self._baseline
        self.preview_var.set("")
        if not self._pause():
            self._summarize()
            return
        if self._validation_feedback:
            self._validate_draft()
        self._summarize()

    def _validate_draft(self) -> Command | None:
        self._invalid_widget = None
        for widget in self._form_widgets():
            widget.state(["!invalid"])
        trigger = self.trigger_var.get()
        error = ""
        if normalize_command(trigger) != trigger:
            error = "Chat command: start with ! and a lowercase letter, like !jump. Use up to 24 letters, numbers, _ or - after !."
            self._invalid_widget = self.trigger_entry
        elif any(command.trigger == trigger and command.trigger != self._selected_trigger for command in self._commands):
            error = "Chat command: that name is already assigned. Choose a unique name."
            self._invalid_widget = self.trigger_entry
        action = LABEL_ACTIONS.get(self.action_var.get())
        if not error and action is None:
            error = "Game action: choose a keyboard or mouse action from the list."
            self._invalid_widget = self.action_combo
        key = LABEL_KEYS.get(self.key_var.get()) if action == "key" else ""
        if not error and action == "key" and key is None:
            error = "Game key: choose a letter, number, arrow, or Space from the list."
            self._invalid_widget = self.key_combo
        button = LABEL_BUTTONS.get(self.button_var.get()) if action == "mouse_button" else ""
        if not error and action == "mouse_button" and button is None:
            error = "Mouse button: choose a button from the list."
            self._invalid_widget = self.button_combo
        if not error and action == "mouse_wheel" and self.wheel_direction_var.get() not in {"Up", "Down"}:
            error = "Wheel direction: choose Up or Down."
            self._invalid_widget = self.wheel_direction_combo
        numbers: dict[str, int] = {"Hold": 100, "Horizontal": 0, "Vertical": 0, "Steps": 0}
        fields = [("Cooldown", self.cooldown_var, self.cooldown_entry, 0, 60_000, " ms")]
        if action in {"key", "mouse_button"}:
            fields.insert(0, ("Hold", self.hold_var, self.hold_entry, 10, 450, " ms"))
        elif action == "mouse_move":
            fields[0:0] = [("Horizontal", self.dx_var, self.dx_entry, -100, 100, " units"),
                           ("Vertical", self.dy_var, self.dy_entry, -100, 100, " units")]
        elif action == "mouse_wheel":
            fields.insert(0, ("Steps", self.wheel_steps_var, self.wheel_steps_entry, 1, 3, ""))
        for label, var, widget, low, high, units in fields:
            raw = var.get()
            digits = raw[1:] if low < 0 and raw.startswith(("-", "+")) else raw
            if len(raw) > 6 or not raw.isascii() or not digits.isdecimal() or not low <= int(raw) <= high:
                if not error:
                    error = f"{label}: enter a whole number from {low:,} to {high:,}{units}."
                    self._invalid_widget = widget
            else:
                numbers[label] = int(raw)
        if not error and action == "mouse_move" and numbers["Horizontal"] == numbers["Vertical"] == 0:
            error = "Mouse movement: set Horizontal or Vertical to a nonzero value."
            self._invalid_widget = self.dx_entry
        if error:
            self.error_var.set(error)
            if self._invalid_widget is not None:
                self._invalid_widget.state(["invalid"])
            return None
        try:
            wheel = numbers["Steps"] * (1 if self.wheel_direction_var.get() == "Up" else -1) if action == "mouse_wheel" else 0
            candidate = Command(trigger, key, numbers["Hold"], numbers["Cooldown"], self.enabled_var.get(),
                                action_type=action, button=button, dx=numbers["Horizontal"],
                                dy=numbers["Vertical"], wheel=wheel)
        except (ValueError, TypeError, tk.TclError):
            self.error_var.set("The mapping is invalid. Review its command, game action, and limits.")
            return None
        self.error_var.set("")
        return candidate

    def _summarize(self) -> None:
        if self._selected_trigger is None and not self._new:
            self.summary_var.set("Create a command to choose its game control.")
            self.draft_var.set("A few simple controls are a good place to start.")
            return
        self.draft_var.set("Unapplied changes · Apply command when ready" if self._dirty else "Applied to this profile")
        if self.enabled_var.get():
            action = LABEL_ACTIONS.get(self.action_var.get())
            if action == "key":
                description = f"press {self.key_var.get()[:16]} for {self.hold_var.get()[:6]} ms"
            elif action == "mouse_button":
                description = f"press {self.button_var.get()[:20]} for {self.hold_var.get()[:6]} ms"
            elif action == "mouse_move":
                description = f"move the mouse by ({self.dx_var.get()[:6]}, {self.dy_var.get()[:6]}) relative units"
            elif action == "mouse_wheel":
                description = f"scroll {self.wheel_direction_var.get()[:4].lower()} {self.wheel_steps_var.get()[:2]} wheel step(s)"
            else:
                description = "use the selected game action"
            self.summary_var.set(f"When chat sends {self.trigger_var.get()[:25]}, {description}.")
        else:
            self.summary_var.set("Disabled. Chat cannot use this command until you enable and apply it.")
        self.apply_button.state(["!disabled"] if self._dirty and not self._blocked else ["disabled"])
        self.revert_button.state(["!disabled"] if self._dirty else ["disabled"])
        if self._blocked:
            self.error_var.set("Controls could not be paused. Restart the app before changing mappings.")

    def _load_form(self, command: Command | None, *, new: bool = False) -> None:
        self.cancel_capture()
        self._loading = True
        self._validation_feedback = False
        self._new = new
        self._selected_trigger = None if new or command is None else command.trigger
        current = command or Command("!action", "space")
        self.trigger_var.set(current.trigger if command is not None else "")
        self.action_var.set(ACTION_LABELS[current.action_type])
        self.key_var.set(KEY_LABELS.get(current.key, "Space"))
        self.button_var.set(BUTTON_LABELS.get(current.button, BUTTON_LABELS["left"]))
        self.dx_var.set(str(current.dx) if current.action_type == "mouse_move" else "25")
        self.dy_var.set(str(current.dy) if current.action_type == "mouse_move" else "0")
        self.wheel_direction_var.set("Down" if current.wheel < 0 else "Up")
        self.wheel_steps_var.set(str(abs(current.wheel)) if current.action_type == "mouse_wheel" else "1")
        self.hold_var.set(str(current.hold_ms))
        self.cooldown_var.set(str(current.cooldown_ms))
        self.enabled_var.set(current.enabled)
        self._baseline = self._values()
        self._dirty = new
        self._loading = False
        self.error_var.set("")
        self.preview_var.set("")
        self.heading_var.set("New command" if new else (f"Edit {command.trigger}" if command else "Create your first command"))
        active = command is not None
        for widget in (self.form, self.summary_label, self.controls):
            widget.grid() if active else widget.grid_remove()
        self.empty_detail.grid_remove() if active else self.empty_detail.grid()
        for widget in (self.trigger_entry, self.hold_entry, self.cooldown_entry, self.dx_entry, self.dy_entry,
                       self.wheel_steps_entry, self.enabled_check, self.capture_button, self.apply_button,
                       self.revert_button, self.preview_button):
            widget.state(["!disabled"] if active else ["disabled"])
        for widget in (self.action_combo, self.key_combo, self.button_combo, self.wheel_direction_combo):
            widget.configure(state="readonly" if active else "disabled")
        for widget in (self.duplicate_button, self.remove_button):
            widget.state(["!disabled"] if self._selected_trigger else ["disabled"])
        for widget in self._form_widgets():
            widget.state(["!invalid"])
        self._invalid_widget = None
        self._shown_action = None
        self._show_action_fields()
        self._feedback_changed()
        self._summarize()
        self._schedule_layout()

    def _render_table(self) -> None:
        query = self.filter_var.get().lower()[:80]
        visible = tuple(command for command in self._commands
                        if not query or query in command.trigger.lower()
                        or query in action_label(command).lower()
                        or query in ACTION_LABELS[command.action_type].lower())
        wanted = {command.trigger for command in visible}
        for row in self.tree.get_children():
            if row not in wanted:
                self.tree.delete(row)
        # Keep existing rows and selection identities instead of destroying and
        # recreating the whole table on every search or command-field edit.
        for index, command in enumerate(visible):
            values = (command.trigger, action_label(command, compact=True), "Enabled" if command.enabled else "Disabled")
            if not self.tree.exists(command.trigger):
                self.tree.insert("", index, iid=command.trigger, values=values)
            elif tuple(self.tree.item(command.trigger, "values")) != values:
                self.tree.item(command.trigger, values=values)
            if self.tree.get_children()[index] != command.trigger:
                self.tree.move(command.trigger, "", index)
        count = len(self._commands)
        self.count_var.set(f"{len(visible)} of {count} shown" if query else f"{count} of {MAX_COMMANDS} commands")
        self.new_button.state(["disabled"] if count >= MAX_COMMANDS else ["!disabled"])
        if count >= MAX_COMMANDS:
            self.duplicate_button.state(["disabled"])
        self.clear_search_button.state(["!disabled"] if self.filter_var.get() else ["disabled"])
        if visible:
            self.empty_table.place_forget()
        else:
            self.empty_var.set("No matches. Try a command name or a game action." if self._commands
                               else "No commands yet.\nChoose New command to add one.")
            self.empty_table.place(relx=0.48, rely=0.5, anchor="center")
        self._restore_selection()
        self._schedule_layout()

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
            self.focus_problem()
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
        if (LABEL_ACTIONS.get(self.action_var.get()) != "key"
                or (not self._new and self._selected_trigger is None) or not self._pause()):
            return
        self._capture_active = True
        self.capture_var.set("Press one game key now...")
        self.capture_row.grid()
        self.capture_button.configure(text="Listening...")
        self.capture_button.state(["disabled"])
        self.capture_entry.focus_set()

    def cancel_capture(self) -> None:
        self._capture_active = False
        self.capture_var.set("")
        self.capture_row.grid_remove()
        self.capture_button.configure(text="Press a key")
        self.capture_button.state(["!disabled"] if (self._new or self._selected_trigger)
                                   and LABEL_ACTIONS.get(self.action_var.get()) == "key" else ["disabled"])

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
            self.capture_var.set("Use one allowed key without modifiers.")
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
        self.preview_var.set(result[:300] if isinstance(result, str) else "Preview requested. No keyboard or mouse input is sent.")
