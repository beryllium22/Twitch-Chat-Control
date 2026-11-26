"""Local desktop controls. Twitch callbacks never touch Tk or arm the engine."""

from __future__ import annotations

import argparse
import queue
import threading
import time
import tkinter as tk
import tkinter.font as tkfont
import uuid
import webbrowser
from dataclasses import replace
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Any, Callable

from . import __version__
from .control_editor import CommandEditor
from .engine import EngineEvent, SafetyEngine
from .models import Command, Profile, ProfileError, normalize_command
from .presets import PROFILE_TEMPLATES, PreviewBackend, new_profile
from .preview_pad import PreviewPad
from .profile_io import load_profile, save_profile
from .session_tools import command_reference, paused_guidance
from .session_ui import Readiness, reason_text, session_readiness
from .twitch import ACTIVATE_URL, ChatMessage, TwitchClient
from .ui_support import (
    append_log_lines,
    clear_log,
    configure_if_changed,
    install_page_wheel_routing,
    set_enabled,
    set_var_if_changed,
)
from .windows import WindowsBackend

BG = "#101722"
PANEL = "#182334"
TEXT = "#edf2f8"
MUTED = "#b1bfd1"
ACCENT = "#9ac6ff"
BORDER = "#35465f"
CONTROL_BORDER = "#7286a3"

AUDIENCE_LABELS = {
    "moderator": "Moderators + you (recommended)",
    "broadcaster": "Only you",
    "subscriber": "Subscribers + moderators + you",
    "vip": "VIPs + moderators + you",
    "everyone": "Everyone in chat",
}
ROLE_LABELS = {"viewer": "Viewer", "subscriber": "Subscriber", "vip": "VIP",
               "moderator": "Moderator", "broadcaster": "Broadcaster (you)"}


class ScrollPage(ttk.Frame):
    """Keep all local controls reachable on smaller or scaled displays."""

    def __init__(self, parent: ttk.Notebook) -> None:
        super().__init__(parent)
        self._focus_job: str | None = None
        self._content_size: tuple[int, int] | None = None
        self._canvas_size: tuple[int, int] | None = None
        self.canvas = tk.Canvas(self, bg=BG, highlightthickness=0, borderwidth=0)
        scroll = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)
        self.content = ttk.Frame(self.canvas, padding=16)
        self._window = self.canvas.create_window((0, 0), window=self.content, anchor="nw")
        self.content.bind("<Configure>", self._resize_content)
        self.canvas.bind("<Configure>", self._resize_canvas)
        self.bind("<Destroy>", self._cancel_focus_reveal, add="+")

    def install_navigation(self) -> None:
        self._wheel_router = install_page_wheel_routing(self, self.canvas, self.content)
        pending = [self.content]
        while pending:
            widget = pending.pop()
            widget.bind("<FocusIn>", lambda event: self.reveal_widget(event.widget), add="+")
            pending.extend(widget.winfo_children())

    def reveal_widget(self, widget: tk.Widget) -> None:
        if not self.winfo_ismapped() or not widget.winfo_ismapped():
            return
        self.update_idletasks()
        top = widget.winfo_rooty() - self.content.winfo_rooty()
        bottom = top + widget.winfo_height()
        visible_top = self.canvas.canvasy(0)
        visible_height = self.canvas.winfo_height()
        total = max(1, self.content.winfo_height())
        if top < visible_top + 8:
            self.canvas.yview_moveto(max(0, top - 12) / total)
        elif bottom > visible_top + visible_height - 8:
            self.canvas.yview_moveto(max(0, bottom + 12 - visible_height) / total)

    def _queue_focus_reveal(self) -> None:
        if self._focus_job is None:
            self._focus_job = self.after_idle(self._reveal_current_focus)

    def _reveal_current_focus(self) -> None:
        self._focus_job = None
        widget = self.focus_get()
        if widget is not None and str(widget).startswith(str(self.content) + "."):
            self.reveal_widget(widget)

    def _cancel_focus_reveal(self, event: Any) -> None:
        if event.widget is self and self._focus_job is not None:
            self.after_cancel(self._focus_job)
            self._focus_job = None

    def _resize_content(self, event: Any) -> None:
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))
        size = (event.width, event.height)
        if size != self._content_size:
            self._content_size = size
            self._queue_focus_reveal()

    def _resize_canvas(self, event: Any) -> None:
        self.canvas.itemconfigure(self._window, width=event.width)
        size = (event.width, event.height)
        if size != self._canvas_size:
            self._canvas_size = size
            # Readiness text and validation can resize the viewport after a
            # FocusIn. Keep that field visible once the new geometry settles.
            # Position-only events from manual scrolling do not snap it back.
            self._queue_focus_reveal()

class App:
    def __init__(
        self,
        root: tk.Tk,
        *,
        backend: Any = None,
        twitch_factory: Callable[..., Any] = TwitchClient,
        start_workers: bool = True,
    ) -> None:
        self.root = root
        self.backend = backend if backend is not None else WindowsBackend()
        self._twitch_factory = twitch_factory
        self._events: queue.Queue[tuple[str, Any]] = queue.Queue(maxsize=256)
        self._connection_lock = threading.RLock()
        self._generation = 0
        self._client: Any = None
        self._closed = False
        self._stop = threading.Event()
        self._last_ui_heartbeat = time.monotonic()
        self._profile = Profile.default()
        self._profile_path: Path | None = None
        self._saved_profile: Profile | None = self._profile
        self.profile_name = tk.StringVar(master=root)
        self.notice_var = tk.StringVar(master=root)
        self._windows: list[Any] = []
        self._field_groups: dict[ttk.Frame, list[tuple[int, ttk.Label, ttk.Entry, ttk.Label]]] = {}
        self._device_dialog: tk.Toplevel | None = None
        self._device_value: tk.StringVar | None = None
        self._loading = True
        self._dirty = False
        self._worker: threading.Thread | None = None
        self._pump_id: str | None = None
        self.engine = SafetyEngine(self.backend, on_event=lambda event: self._post("decision", event))

        self.root.title(f"Twitch Chat Control · {__version__}")
        width = min(1180, max(940, self.root.winfo_screenwidth() - 80))
        height = min(900, max(640, self.root.winfo_screenheight() - 80))
        self.root.geometry(f"{width}x{height}")
        self.root.minsize(940, 640)
        self.root.configure(bg=BG)
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.root.bind("<F8>", lambda _event: self._panic())
        self._style()
        self._build()
        self._show_profile(self._profile)
        self._loading = False
        self._refresh_session_ui()
        if start_workers:
            try:
                self.backend.start_safety_monitor(
                    on_fault=lambda reason: self.engine.disarm(reason),
                    on_panic=self.engine.emergency_stop,
                )
            except Exception:
                self._post("notice", "Emergency hotkey unavailable. Live input is disabled.")
            self._worker = threading.Thread(target=self._run_engine, name="input-policy", daemon=True)
            self._worker.start()
        self._pump_id = self.root.after(50, self._pump)
        self._post("notice", "Welcome. Try the starter controls in Preview, or open Controls & commands to make them your own.")

    def _style(self) -> None:
        families = {name.lower() for name in tkfont.families(self.root)}
        self._font_family = "Segoe UI" if "segoe ui" in families else tkfont.nametofont("TkDefaultFont").actual("family")
        self._mono_family = "Consolas" if "consolas" in families else "Courier"
        style = ttk.Style(self.root)
        style.theme_use("clam")
        style.configure(".", background=BG, foreground=TEXT, font=(self._font_family, 10))
        style.configure("TFrame", background=BG)
        style.configure("Card.TFrame", background=PANEL, relief="flat", borderwidth=0)
        style.configure("Surface.TFrame", background=PANEL, bordercolor=BORDER, relief="solid", borderwidth=1)
        style.configure("TLabel", background=BG, foreground=TEXT)
        style.configure("Muted.TLabel", foreground=MUTED)
        style.configure("Small.TLabel", foreground=MUTED, font=(self._font_family, 9))
        style.configure("Title.TLabel", font=(self._font_family, 21, "bold"))
        style.configure("Heading.TLabel", font=(self._font_family, 13, "bold"))
        style.configure("Card.TLabel", background=PANEL)
        style.configure("CardMuted.TLabel", background=PANEL, foreground=MUTED)
        style.configure("CardHeading.TLabel", background=PANEL, font=(self._font_family, 12, "bold"))
        for name, color in (("Status", ACCENT), ("Paused", MUTED), ("Live", "#ffcf80"), ("Stopped", "#ffacb8")):
            style.configure(f"{name}.TLabel", foreground=color, font=(self._font_family, 11, "bold"))
        style.configure("TButton", padding=(13, 8), background="#273750", foreground=TEXT,
                        bordercolor=CONTROL_BORDER, relief="flat", focuscolor=ACCENT)
        style.map("TButton", background=[("disabled", "#1d293c"), ("pressed", "#203955"), ("active", "#354b69")],
                  foreground=[("disabled", "#8c9bb1")], bordercolor=[("focus", ACCENT)])
        style.configure("Accent.TButton", background="#315fd4", foreground="white", bordercolor="#315fd4")
        style.map("Accent.TButton", background=[("disabled", "#253858"), ("pressed", "#2447a2"), ("active", "#3d6de5")],
                  foreground=[("disabled", "#9cabc1")], bordercolor=[("focus", "#b9d8ff")])
        style.configure("Danger.TButton", background="#9b3148", foreground="white", bordercolor="#b3445c",
                        font=(self._font_family, 10, "bold"))
        style.map("Danger.TButton", background=[("pressed", "#7d263b"), ("active", "#af3953")])
        style.configure("Link.TButton", background=BG, foreground=ACCENT, padding=(8, 6), borderwidth=0)
        style.configure("CardLink.TButton", background=PANEL, foreground=ACCENT, padding=(8, 6), borderwidth=0)
        for name in ("TEntry", "TSpinbox", "TCombobox"):
            style.configure(name, fieldbackground="#202e44", foreground=TEXT, insertcolor=TEXT,
                            bordercolor=CONTROL_BORDER, lightcolor=CONTROL_BORDER, darkcolor=CONTROL_BORDER, padding=7)
            style.map(name, bordercolor=[("invalid", "#ffacb8"), ("focus", ACCENT)],
                      fieldbackground=[("disabled", "#1c283a"), ("readonly", "#202e44")],
                      foreground=[("disabled", "#8c9bb1"), ("readonly", TEXT)])
        style.configure("TNotebook", background=BG, borderwidth=0, tabmargins=(0, 0, 0, 5))
        style.configure("TNotebook.Tab", padding=(17, 10), background=BG, foreground=MUTED)
        style.map("TNotebook.Tab", background=[("selected", PANEL)], foreground=[("selected", TEXT)],
                  lightcolor=[("focus", ACCENT)])
        style.configure("Treeview", background=PANEL, fieldbackground=PANEL, foreground=TEXT,
                        rowheight=32, bordercolor=BORDER)
        style.configure("Treeview.Heading", background="#25364e", foreground=TEXT, padding=7)
        style.map("Treeview", background=[("selected", "#2c5080")], foreground=[("selected", "#ffffff")])
        for name in ("TRadiobutton", "TCheckbutton"):
            style.configure(name, padding=(0, 5))
            style.configure("Card." + name, background=PANEL, padding=(0, 5))
            style.map("Card." + name, background=[("active", PANEL)], foreground=[("disabled", "#8c9bb1")])

    def _build(self) -> None:
        outer = ttk.Frame(self.root, padding=(22, 16))
        outer.pack(fill="both", expand=True)
        outer.columnconfigure(0, weight=1)
        outer.rowconfigure(2, weight=1)
        heading = ttk.Frame(outer)
        heading.grid(row=0, column=0, sticky="ew")
        title = ttk.Frame(heading)
        title.pack(side="left", fill="x", expand=True)
        ttk.Label(title, text="Twitch Chat Control", style="Title.TLabel").pack(anchor="w")
        ttk.Button(heading, text="STOP ALL  ·  F8", style="Danger.TButton", command=self._panic).pack(side="right")

        status_row = ttk.Frame(outer, padding=(0, 9, 0, 9))
        status_row.grid(row=1, column=0, sticky="ew")
        self.state_var = tk.StringVar(value="DISARMED · PREVIEW")
        self.status_var = tk.StringVar(value="Paused · Preview")
        self.connection_var = tk.StringVar(value="Twitch not connected")
        self.status_label = ttk.Label(status_row, textvariable=self.status_var, style="Paused.TLabel")
        self.status_label.pack(side="left")
        ttk.Label(status_row, textvariable=self.connection_var, style="Muted.TLabel").pack(side="right")

        self.notebook = ttk.Notebook(outer)
        # Reserve the stop/start guidance even when enlarged text makes a page
        # taller than the available screen. Only the page viewport shrinks.
        self.notebook.grid(row=2, column=0, sticky="nsew")
        self._pages = [ScrollPage(self.notebook) for _ in range(3)]
        self.session_tab, self.commands_tab, self.safety_tab = (page.content for page in self._pages)
        self.activity_tab = ttk.Frame(self.notebook, padding=16)
        for frame, label in zip((*self._pages, self.activity_tab),
                                ("Session", "Controls & commands", "Safety & access", "Activity"), strict=True):
            self.notebook.add(frame, text=label)
        self._build_session()
        self._build_commands()
        self._build_safety()
        self._build_activity()
        self.notebook.enable_traversal()
        self.notebook.bind("<<NotebookTabChanged>>", lambda _event: self.command_editor.cancel_capture())

        footer = ttk.Frame(outer, style="Surface.TFrame", padding=(14, 10))
        footer.grid(row=3, column=0, sticky="ew", pady=(12, 0))
        footer.columnconfigure(0, weight=1)
        self.readiness_var = tk.StringVar()
        self.readiness_detail_var = tk.StringVar()
        self.readiness_heading = ttk.Label(footer, textvariable=self.readiness_var, style="CardHeading.TLabel", wraplength=400)
        self.readiness_heading.grid(row=0, column=0, sticky="ew")
        self.readiness_label = ttk.Label(footer, textvariable=self.readiness_detail_var, style="CardMuted.TLabel", wraplength=600, justify="left")
        self.readiness_label.grid(row=1, column=0, columnspan=3, sticky="ew", pady=(4, 0))
        self.next_step_button = ttk.Button(footer, text="Review commands", style="CardLink.TButton", command=self._next_step)
        self.next_step_button.grid(row=0, column=1, padx=(8, 16), sticky="e")
        actions = ttk.Frame(footer, style="Card.TFrame")
        actions.grid(row=0, column=2, sticky="e")
        self.arm_button = ttk.Button(actions, text="Start preview", style="Accent.TButton", command=self._arm)
        self.arm_button.pack(side="left", padx=(0, 8))
        self.pause_button = ttk.Button(actions, text="Pause", command=self._pause)
        self.pause_button.pack(side="left")
        self.notice_label = ttk.Label(outer, textvariable=self.notice_var, style="Small.TLabel", wraplength=1080)
        self.notice_label.grid(row=4, column=0, sticky="ew", pady=(8, 0))
        # Button labels change when switching modes, even if the footer itself
        # keeps its size. Follow each label's allocated width instead of stale
        # estimates from the outer frame. Details use a full-width second row.
        for label in (self.readiness_heading, self.readiness_label):
            label.bind("<Configure>", lambda event, target=label:
                       configure_if_changed(target, wraplength=max(40, event.width - 4)))
        outer.bind("<Configure>", lambda event: configure_if_changed(self.notice_label, wraplength=max(400, event.width - 44)))

        for page in self._pages:
            page.install_navigation()
        for sequence, action in (("<Control-s>", "save"), ("<Control-Shift-S>", "save_as"), ("<Control-o>", "open")):
            self.root.bind(sequence, lambda _event, selected=action: self._shortcut(selected), add="+")
        for index in range(4):
            self.root.bind(f"<Control-Key-{index + 1}>", lambda _event, selected=index: self._shortcut(f"tab_{selected}"), add="+")

    def _card(self, parent: tk.Misc, title: str, subtitle: str = "") -> ttk.Frame:
        card = ttk.Frame(parent, style="Surface.TFrame", padding=16)
        ttk.Label(card, text=title, style="CardHeading.TLabel").pack(anchor="w")
        if subtitle:
            note = ttk.Label(card, text=subtitle, style="CardMuted.TLabel", wraplength=410, justify="left")
            note.pack(anchor="w", fill="x", pady=(4, 12))
            card.bind("<Configure>", lambda event: configure_if_changed(note, wraplength=max(260, event.width - 34)))
        return card

    def _wrapped_label(self, parent: tk.Misc, **options: Any) -> ttk.Label:
        options.setdefault("wraplength", 400)
        label = ttk.Label(parent, **options)
        parent.bind("<Configure>", lambda event: configure_if_changed(label, wraplength=max(160, event.width - 40)), add="+")
        return label

    def _build_session(self) -> None:
        frame = self.session_tab
        frame.columnconfigure((0, 1), weight=1, uniform="session")
        ttk.Label(frame, text="Make chat part of your game", style="Heading.TLabel").grid(row=0, column=0, columnspan=2, sticky="w")
        self._wrapped_label(frame, text="Try your controls in Preview first. Connect your channel when you are ready.", style="Muted.TLabel").grid(row=1, column=0, columnspan=2, sticky="ew", pady=(4, 16))
        mode_card = self._card(frame, "1  Choose your mode", "Preview simulates your controls without sending keyboard or mouse input.")
        mode_card.grid(row=2, column=0, sticky="nsew", padx=(0, 7))
        self.mode_var = tk.StringVar(value="preview")
        ttk.Radiobutton(mode_card, text="Preview · no game input", style="Card.TRadiobutton", variable=self.mode_var, value="preview", command=self._mode_changed).pack(anchor="w")
        self.live_radio = ttk.Radiobutton(mode_card, text="Live game control · Windows", style="Card.TRadiobutton", variable=self.mode_var, value="live", command=self._mode_changed)
        self.live_radio.pack(anchor="w")
        if not self.backend.available:
            self.live_radio.state(["disabled"])
            ttk.Label(mode_card, text="Live control is available in the Windows app.", style="CardMuted.TLabel").pack(anchor="w", pady=(5, 0))

        profile_card = self._card(frame, "2  Review your controls", "Choose what chat can do and who can take part.")
        profile_card.grid(row=2, column=1, sticky="nsew", padx=(7, 0))
        self.profile_summary_var = tk.StringVar()
        self._wrapped_label(profile_card, textvariable=self.profile_summary_var, style="Card.TLabel", wraplength=410).pack(anchor="w", pady=(0, 8))
        links = ttk.Frame(profile_card, style="Card.TFrame")
        links.pack(anchor="w")
        ttk.Button(links, text="Edit commands", command=lambda: self._go_to_step("commands")).pack(side="left", padx=(0, 8))
        ttk.Button(links, text="Who can play?", command=lambda: self._go_to_step("safety")).pack(side="left")
        self.copy_commands_button = ttk.Button(profile_card, text="Copy chat commands", command=self._copy_chat_commands)
        self.copy_commands_button.pack(anchor="w", pady=(8, 0))
        self._wrapped_label(profile_card, text="Copies enabled commands for your viewers. Pending edits are checked and applied first.", style="CardMuted.TLabel", wraplength=410).pack(anchor="w", pady=(5, 0))

        self.preview_card = self._card(frame, "3  Try a chat command", "Start preview using the button below, then send a test command here.")
        self.preview_card.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(14, 0))
        form = ttk.Frame(self.preview_card, style="Card.TFrame")
        form.pack(fill="x")
        form.columnconfigure(0, weight=1)
        self.sim_var = tk.StringVar(value="!jump")
        self.sim_role = tk.StringVar(value="moderator")
        self.sim_role_label = tk.StringVar(value=ROLE_LABELS["moderator"])
        ttk.Label(form, text="Chat command", style="Card.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(form, text="Try as", style="Card.TLabel").grid(row=0, column=1, sticky="w")
        self.sim_entry = ttk.Combobox(form, textvariable=self.sim_var, width=24)
        self.sim_entry.grid(row=1, column=0, sticky="ew", padx=(0, 12), pady=(4, 0))
        roles = ttk.Combobox(form, textvariable=self.sim_role_label, state="readonly", width=19, values=tuple(ROLE_LABELS.values()))
        roles.grid(row=1, column=1, padx=(0, 12), pady=(4, 0))
        roles.bind("<<ComboboxSelected>>", lambda _event: self.sim_role.set(next(key for key, value in ROLE_LABELS.items() if value == self.sim_role_label.get())))
        self.sim_role.trace_add("write", lambda *_args: set_var_if_changed(self.sim_role_label, ROLE_LABELS.get(self.sim_role.get(), "Viewer")))
        self.sim_button = ttk.Button(form, text="Try command", command=self._simulate)
        self.sim_button.grid(row=1, column=2, pady=(4, 0))
        self.sim_action_var = tk.StringVar()
        self._wrapped_label(self.preview_card, textvariable=self.sim_action_var, style="CardMuted.TLabel", wraplength=900).pack(anchor="w", pady=(8, 0))
        self.sim_var.trace_add("write", lambda *_args: self._update_sim_action())
        self.sim_result_var = tk.StringVar(value="Start Preview to try a command. No keyboard or mouse input will be sent.")
        self._wrapped_label(self.preview_card, textvariable=self.sim_result_var, style="CardMuted.TLabel", wraplength=900).pack(anchor="w", pady=(10, 0))
        self.sim_entry.bind("<Return>", lambda _event: self._simulate())
        self.connection_setup_var = tk.BooleanVar(value=False)
        self.connection_setup_button = ttk.Button(self.preview_card, text="Connect Twitch to test real chat...", style="CardLink.TButton", command=self._toggle_connection_setup)
        self.connection_setup_button.pack(anchor="w", pady=(8, 0))
        self.preview_pad = PreviewPad(self.preview_card)
        self.preview_pad.pack(fill="x", pady=(12, 0))

        self.session_totals_var = tk.StringVar()
        self.queue_status_var = tk.StringVar()
        self.session_numbers = ttk.Frame(frame)
        self.session_numbers.grid(row=5, column=0, columnspan=2, sticky="ew", pady=(12, 0))
        self._wrapped_label(self.session_numbers, textvariable=self.session_totals_var, style="Small.TLabel", wraplength=900).pack(anchor="w")
        self._wrapped_label(self.session_numbers, textvariable=self.queue_status_var, style="Small.TLabel", wraplength=900).pack(anchor="w", pady=(3, 0))

        self.connection_card = self._card(frame, "Connect your Twitch channel", "Use your own Twitch Public application. Your sign-in stays in memory for this session.")
        self.connection_card.grid(row=4, column=0, sticky="nsew", pady=(14, 0), padx=(0, 7))
        self.channel_var = tk.StringVar()
        self.client_id_var = tk.StringVar()
        ttk.Label(self.connection_card, text="Channel name or Twitch URL", style="Card.TLabel").pack(anchor="w")
        self.channel_entry = ttk.Entry(self.connection_card, textvariable=self.channel_var)
        self.channel_entry.pack(fill="x", pady=(4, 10))
        ttk.Label(self.connection_card, text="Your Public Client ID", style="Card.TLabel").pack(anchor="w")
        self.client_entry = ttk.Entry(self.connection_card, textvariable=self.client_id_var, show="*")
        self.client_entry.pack(fill="x", pady=(4, 8))
        buttons = ttk.Frame(self.connection_card, style="Card.TFrame")
        buttons.pack(fill="x")
        self.connect_button = ttk.Button(buttons, text="Connect Twitch", command=self._connect)
        self.connect_button.pack(side="left", padx=(0, 8))
        ttk.Button(buttons, text="Get a Client ID", style="CardLink.TButton", command=lambda: webbrowser.open("https://dev.twitch.tv/console/apps")).pack(side="left")
        self._wrapped_label(self.connection_card, text="No client secret is needed. Sign in as your channel's owner.", style="CardMuted.TLabel", wraplength=410).pack(anchor="w", pady=(10, 0))

        self.game_card = self._card(frame, "Choose a game window", "Run your game without administrator rights, then refresh and choose its exact window.")
        self.game_card.grid(row=4, column=1, sticky="nsew", pady=(14, 0), padx=(7, 0))
        self.window_var = tk.StringVar(value="Refresh to find your game")
        self.window_combo = ttk.Combobox(self.game_card, textvariable=self.window_var, state="readonly", width=26)
        self.window_combo.pack(fill="x", pady=(0, 8))
        self.window_combo.bind("<<ComboboxSelected>>", self._select_window)
        ttk.Button(self.game_card, text="Refresh windows", command=self._refresh_windows).pack(anchor="w")
        self.ack_var = tk.BooleanVar(value=False)
        self.ack_check = ttk.Checkbutton(self.game_card, variable=self.ack_var, style="Card.TCheckbutton", text="I understand when to pause", command=self._ack_changed)
        self.ack_check.pack(anchor="w", pady=(12, 0))
        self._wrapped_label(self.game_card, text="Pause before opening game chat, consoles, overlays, purchases, or sensitive menus. Losing game focus also stops control.", style="CardMuted.TLabel", wraplength=410).pack(anchor="w", pady=(4, 0))
        self.mouse_hint = self._wrapped_label(self.game_card, text="Mouse bindings need the pointer over the game's play area. Keep it away from window borders; start with small movements.", style="CardMuted.TLabel", wraplength=410)

    def _build_commands(self) -> None:
        frame = self.commands_tab
        top = ttk.Frame(frame)
        top.pack(fill="x", pady=(0, 10))
        heading = ttk.Label(top, text="Your game, your commands", style="Heading.TLabel")
        buttons = ttk.Frame(top)
        ttk.Button(buttons, text="Open profile...", command=self._import_profile).pack(side="left")
        ttk.Button(buttons, text="Save profile", style="Accent.TButton", command=self._save_profile).pack(side="left", padx=(8, 0))
        ttk.Button(buttons, text="Save a copy...", command=lambda: self._save_profile(save_as=True)).pack(side="left", padx=(8, 0))
        self._adaptive_toolbar(top, heading, buttons)
        row = ttk.Frame(frame)
        row.pack(fill="x", pady=(0, 7))
        naming = ttk.Frame(row)
        ttk.Label(naming, text="Profile name").pack(side="left", padx=(0, 8))
        self.profile_name_entry = ttk.Entry(naming, textvariable=self.profile_name, width=22)
        self.profile_name_entry.pack(side="left")
        templates = ttk.Frame(row)
        ttk.Label(templates, text="Start from").pack(side="left", padx=(0, 8))
        self.template_var = tk.StringVar(value="Blank")
        ttk.Combobox(templates, textvariable=self.template_var, values=PROFILE_TEMPLATES, state="readonly", width=19).pack(side="left", padx=(0, 8))
        ttk.Button(templates, text="New profile", command=self._new_profile).pack(side="left")
        self._adaptive_toolbar(row, naming, templates, align="w")
        self.profile_status_var = tk.StringVar()
        self._wrapped_label(frame, textvariable=self.profile_status_var, style="Small.TLabel", wraplength=940).pack(anchor="w", pady=(0, 12))
        self.command_editor = CommandEditor(frame, on_change=self._commands_changed, on_edit=self._command_draft_edited,
                                            on_preview=self._preview_command, on_panic=self._panic, on_notice=self.notice_var.set)
        self.command_editor.pack(fill="both", expand=True)
        ttk.Label(frame, text="Ctrl+S saves your profile  ·  Ctrl+O opens a profile  ·  Ctrl+Tab switches tabs", style="Small.TLabel").pack(anchor="w", pady=(10, 0))

    def _adaptive_toolbar(self, container: ttk.Frame, first: tk.Widget, second: tk.Widget, *, align: str = "e") -> None:
        container.columnconfigure(0, weight=1 if align == "e" else 0)
        container.columnconfigure(1, weight=0 if align == "e" else 1)
        first.grid(row=0, column=0, sticky="w")
        second.grid(row=0, column=1, sticky=align, padx=(16, 0))
        stacked = False
        def arrange(event: Any) -> None:
            nonlocal stacked
            next_stacked = event.width < first.winfo_reqwidth() + second.winfo_reqwidth() + 20
            if next_stacked == stacked:
                return
            stacked = next_stacked
            first.grid_configure(columnspan=2 if stacked else 1)
            second.grid_configure(row=1 if stacked else 0, column=0 if stacked else 1,
                                  columnspan=2 if stacked else 1, sticky="w" if stacked else align,
                                  padx=0 if stacked else (16, 0), pady=(8, 0) if stacked else 0)
        container.bind("<Configure>", arrange)

    def _build_safety(self) -> None:
        frame = self.safety_tab
        ttk.Label(frame, text="Keep the session on your terms", style="Heading.TLabel").pack(anchor="w")
        ttk.Label(frame, text="Start with trusted viewers. You can invite more people when your controls feel right.", style="Muted.TLabel").pack(anchor="w", pady=(4, 14))
        self.audience_var = tk.StringVar()
        self.audience_label_var = tk.StringVar()
        self.allowed_var = tk.StringVar()
        self.blocked_var = tk.StringVar()
        self.limit_vars: dict[str, tk.StringVar] = {}
        self.safety_entries: dict[str, tk.Widget] = {}
        audience = self._card(frame, "Who can play?", "Audience and user lists work together. A blocked viewer is always excluded.")
        audience.pack(fill="x", pady=(0, 12))
        self.audience_combo = ttk.Combobox(audience, textvariable=self.audience_label_var, state="readonly", values=tuple(AUDIENCE_LABELS.values()), width=38)
        self.audience_combo.pack(anchor="w", fill="x")
        self.audience_combo.bind("<<ComboboxSelected>>", self._audience_changed)
        self.audience_var.trace_add("write", lambda *_args: set_var_if_changed(self.audience_label_var, AUDIENCE_LABELS.get(self.audience_var.get(), "Choose an audience")))
        self.access_summary_var = tk.StringVar()
        self._wrapped_label(audience, textvariable=self.access_summary_var, style="CardMuted.TLabel", wraplength=900).pack(anchor="w", pady=(8, 0))
        pace = self._card(frame, "Set a comfortable pace", "All of these limits apply together, including each command's own cooldown.")
        pace.pack(fill="x", pady=(0, 12))
        fields = ttk.Frame(pace, style="Card.TFrame")
        fields.pack(fill="x")
        fields.columnconfigure(2, weight=1)
        for row, (field, label, hint) in enumerate((
            ("global_cooldown_ms", "Between commands (ms)", "100 to 60,000"),
            ("viewer_cooldown_ms", "Each viewer waits (ms)", "250 to 60,000"),
            ("max_actions_per_minute", "Actions per minute", "1 to 300"),
        )):
            var = tk.StringVar()
            self.limit_vars[field] = var
            self.safety_entries[field] = self._field(fields, row, label, var, hint)
        self.advanced_button = ttk.Button(frame, text="Show advanced settings", command=self._toggle_advanced)
        self.advanced_button.pack(anchor="w", pady=(0, 8))
        self.advanced_frame = self._card(frame, "Advanced limits & viewer lists", "These settings still apply while this section is collapsed.")
        self._advanced_open = False
        advanced_fields = ttk.Frame(self.advanced_frame, style="Card.TFrame")
        advanced_fields.pack(fill="x")
        advanced_fields.columnconfigure(2, weight=1)
        for row, (field, label, hint) in enumerate((
            ("queue_size", "Queued actions", "1 to 8"),
            ("queue_ttl_ms", "Drop waiting input after (ms)", "100 to 750"),
        )):
            var = tk.StringVar()
            self.limit_vars[field] = var
            self.safety_entries[field] = self._field(advanced_fields, row, label, var, hint)
        self.safety_entries["allowed_user_ids"] = self._field(advanced_fields, 2, "Only these Twitch user IDs", self.allowed_var, "Optional; comma separated")
        self.safety_entries["blocked_user_ids"] = self._field(advanced_fields, 3, "Blocked Twitch user IDs", self.blocked_var, "Optional; comma separated")
        self._wrapped_label(self.advanced_frame, text="Use numeric Twitch IDs, not display names. The allowed list also requires the selected audience role.", style="CardMuted.TLabel", wraplength=900).pack(anchor="w", pady=(8, 0))
        self.safety_actions = ttk.Frame(frame)
        self.safety_actions.pack(fill="x", pady=(6, 0))
        ttk.Button(self.safety_actions, text="Apply safety settings", style="Accent.TButton", command=self._apply_profile).pack(side="left")
        ttk.Label(self.safety_actions, text="Changes pause control. Save the profile to keep them.", style="Muted.TLabel").pack(side="left", padx=(12, 0))
        self.safety_var = tk.StringVar(value="Checking local input safety...")
        self._wrapped_label(frame, textvariable=self.safety_var, style="Muted.TLabel", wraplength=930).pack(anchor="w", pady=(14, 0))
        for var in (self.profile_name, self.audience_var, self.allowed_var, self.blocked_var, *self.limit_vars.values()):
            var.trace_add("write", self._profile_edited)

    def _field(self, frame: ttk.Frame, row: int, label: str, var: tk.StringVar, hint: str = "") -> ttk.Entry:
        title = ttk.Label(frame, text=label, style="Card.TLabel")
        title.grid(row=row, column=0, sticky="w", padx=(0, 16))
        entry = ttk.Entry(frame, textvariable=var, width=40 if "IDs" in label else 16)
        entry.grid(row=row, column=1, sticky="w", pady=5)
        help_label = ttk.Label(frame, text=hint, style="CardMuted.TLabel")
        help_label.grid(row=row, column=2, sticky="w", padx=(16, 0))
        fields = self._field_groups.setdefault(frame, [])
        fields.append((row, title, entry, help_label))
        if len(fields) == 1:
            stacked = False
            def arrange(event: Any) -> None:
                nonlocal stacked
                required = sum(max(item[index].winfo_reqwidth() for item in fields) for index in (1, 2, 3)) + 32
                next_stacked = event.width < required
                if stacked == next_stacked:
                    return
                stacked = next_stacked
                for index, title, entry, help_label in fields:
                    title.grid_configure(row=index * 2 if stacked else index, column=0,
                                         columnspan=3 if stacked else 1, pady=(8, 0) if stacked else 0)
                    entry.grid_configure(row=index * 2 + 1 if stacked else index, column=0 if stacked else 1)
                    help_label.grid_configure(row=index * 2 + 1 if stacked else index, column=1 if stacked else 2,
                                              columnspan=2 if stacked else 1)
            frame.bind("<Configure>", arrange)
        return entry

    def _toggle_advanced(self) -> None:
        self._advanced_open = not self._advanced_open
        if self._advanced_open:
            self.advanced_frame.pack(fill="x", before=self.safety_actions, pady=(0, 8))
        else:
            self.advanced_frame.pack_forget()
        self.advanced_button.configure(text="Hide advanced settings" if self._advanced_open else "Show advanced settings")

    def _audience_changed(self, _event: Any = None) -> None:
        selected = next((key for key, value in AUDIENCE_LABELS.items() if value == self.audience_label_var.get()), None)
        if selected is not None:
            self.audience_var.set(selected)

    def _build_activity(self) -> None:
        header = ttk.Frame(self.activity_tab)
        header.pack(fill="x")
        heading = ttk.Label(header, text="Session activity", style="Heading.TLabel")
        clear_button = ttk.Button(header, text="Clear activity", command=self._clear_log)
        self._adaptive_toolbar(header, heading, clear_button)
        self._wrapped_label(self.activity_tab, textvariable=self.session_totals_var, style="Muted.TLabel", wraplength=900).pack(anchor="w", pady=(8, 0))
        self._wrapped_label(self.activity_tab, textvariable=self.queue_status_var, style="Muted.TLabel", wraplength=900).pack(anchor="w", pady=(3, 0))
        self._wrapped_label(self.activity_tab, text="Latest 200 events. Accepted commands can expire before running. Clearing this view keeps totals and limits. Chat text and sign-in details are never recorded.", style="Small.TLabel", wraplength=900).pack(anchor="w", pady=(6, 12))
        frame = ttk.Frame(self.activity_tab)
        frame.pack(fill="both", expand=True)
        self.log = tk.Text(frame, bg=PANEL, fg=TEXT, insertbackground=TEXT, relief="flat", wrap="word",
                           font=(self._mono_family, 10), height=12, state="disabled", padx=14, pady=12,
                           highlightthickness=1, highlightbackground=BORDER, highlightcolor=ACCENT)
        scroll = ttk.Scrollbar(frame, command=self.log.yview)
        self.log.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.log.pack(side="left", fill="both", expand=True)

    def _focus_widget(self, widget: tk.Widget) -> None:
        for index, page in enumerate(self._pages):
            if str(widget).startswith(str(page.content) + "."):
                self.notebook.select(index)
                self.root.update_idletasks()
                widget.focus_set()
                page.reveal_widget(widget)
                return
        widget.focus_set()

    def _go_to_step(self, action: str | None) -> None:
        self.command_editor.cancel_capture()
        if action == "commands":
            self._focus_widget(self.command_editor.trigger_entry if self.command_editor.commands else self.command_editor.new_button)
        elif action == "safety":
            self._focus_widget(self.audience_combo)
        elif action in {"connect", "game", "ack"}:
            self.connection_setup_var.set(True)
            self._refresh_session_ui()
            self._focus_widget({"connect": self.channel_entry, "game": self.window_combo, "ack": self.ack_check}[action])
        elif action == "restart":
            messagebox.showinfo("Restart the app", "Save your profile, close the app, and reopen it without administrator rights. If F8 is in use by another app, free it before restarting.", parent=self.root)

    def _next_step(self) -> None:
        readiness = self._refresh_session_ui()
        self._go_to_step(readiness.action or "commands")

    def _shortcut(self, action: str) -> str:
        if self.command_editor.capture_active:
            self.command_editor.cancel_capture()
            self.notice_var.set("Key capture canceled. Use an unmodified game key when capturing a control.")
            return "break"
        if action == "save":
            self._save_profile()
        elif action == "save_as":
            self._save_profile(save_as=True)
        elif action == "open":
            self._import_profile()
        elif action in {f"tab_{index}" for index in range(4)}:
            self.notebook.select(int(action[-1]))
        return "break"

    def _toggle_connection_setup(self) -> None:
        self.connection_setup_var.set(not self.connection_setup_var.get())
        self._refresh_session_ui()
        if self.connection_setup_var.get():
            self._focus_widget(self.channel_entry)

    def _pause(self) -> None:
        self.command_editor.cancel_capture()
        self.engine.disarm("stopped_locally")
        self.notice_var.set("Paused. Queued actions cleared; start again when you are ready.")
        self._refresh_session_ui()

    def _post(self, kind: str, value: Any) -> None:
        try:
            self._events.put_nowait((kind, value))
        except queue.Full:
            try:
                self._events.get_nowait()
            except queue.Empty:
                pass
            try:
                self._events.put_nowait((kind, value))
            except queue.Full:
                pass

    def _run_engine(self) -> None:
        while not self._stop.wait(0.01):
            try:
                if time.monotonic() - self._last_ui_heartbeat > 1.0:
                    if self.engine.snapshot().state != "disarmed":
                        self.engine.disarm("interface_unresponsive")
                self.engine.tick()
            except Exception:
                self.engine.disarm("policy_worker_failed")
                self._post("notice", "Input stopped after a safety worker failure. Restart the app.")
                self._stop.set()

    def _pump(self) -> None:
        if self._closed:
            return
        if self._pump_id is not None:
            self.root.after_cancel(self._pump_id)
            self._pump_id = None
        self._last_ui_heartbeat = time.monotonic()
        activity: list[str] = []
        for _ in range(60):
            try:
                kind, value = self._events.get_nowait()
            except queue.Empty:
                break
            if kind == "decision":
                label = {"state": "Session", "connection": "Twitch", "accepted": "Accepted", "rejected": "Not accepted",
                         "preview": "Preview", "executed": "Game input", "released": "Released", "dropped": "Skipped",
                         "fault": "Safety stop", "blocked": "Setup needed"}.get(value.kind, "Session")
                activity.append(f"{label}" + (f" · {value.command}" if value.command else "") + f": {reason_text(value.reason)}")
                self._render_preview_event(value)
            elif kind == "notice":
                set_var_if_changed(self.notice_var, value)
                activity.append(value)
            elif kind == "status":
                generation, status, detail = value
                if generation != self._generation:
                    continue
                labels = {"connected": "Twitch connected", "disconnected": "Twitch not connected", "error": "Twitch needs attention",
                          "connecting": "Connecting to Twitch...", "authorizing": "Waiting for Twitch sign-in..."}
                set_var_if_changed(self.connection_var, labels.get(status, "Twitch: " + status.replace("_", " ")))
                set_var_if_changed(self.notice_var, detail)
                activity.append(detail)
                if status in {"connected", "disconnected", "error"}:
                    self._clear_device()
                if status in {"disconnected", "error"}:
                    configure_if_changed(self.connect_button, text="Connect Twitch")
                    set_enabled(self.channel_entry, True)
                    set_enabled(self.client_entry, True)
            elif kind == "device":
                generation, uri, code = value
                if generation == self._generation:
                    self._show_device(uri, code)
        append_log_lines(self.log, activity)
        self._refresh_session_ui()
        self._pump_id = self.root.after(50, self._pump)

    def _refresh_session_ui(self) -> Readiness:
        snapshot = self.engine.snapshot()
        # Counters can change without any setup or mode change. Keep these
        # cheap writes outside the cached full-page presentation refresh.
        set_var_if_changed(self.session_totals_var, f"Preview + live since app opened: {snapshot.accepted:,} accepted · {snapshot.rejected:,} blocked")
        set_var_if_changed(self.queue_status_var, f"Queued now: {snapshot.queue_depth} · Held input: {snapshot.active_action or 'None'}")
        if snapshot.reason == "emergency_stop":
            # Registered native F8 can be consumed before Tk receives KeyPress.
            self.command_editor.cancel_capture()
        requested_mode = self.mode_var.get()
        if snapshot.state in {"armed", "pending"}:
            # The running engine is authoritative. A normal local mode change
            # disarms before refreshing, so this only repairs a stale UI value.
            requested_mode = "preview" if snapshot.preview else "live"
            set_var_if_changed(self.mode_var, requested_mode)
        available, safety_ready = self.backend.available, self.backend.safety_ready
        target_selected = self.backend.target_selected
        signature = (snapshot.state, snapshot.preview, snapshot.connected, snapshot.reason,
                     snapshot.backend_fault, round(snapshot.pending_seconds, 1), requested_mode,
                     available, safety_ready, target_selected, self._stop.is_set(), self._profile,
                     self.command_editor.dirty, self._dirty, self.ack_var.get(), self.connection_setup_var.get())
        if getattr(self, "_ui_signature", None) == signature:
            self._update_profile_status()
            return self._readiness
        preview = requested_mode == "preview" if snapshot.state == "disarmed" else snapshot.preview
        mode = "PREVIEW" if preview else "LIVE GAME INPUT"
        state_text = f"ARMING {snapshot.pending_seconds:.1f}s" if snapshot.state == "pending" else snapshot.state.upper()
        set_var_if_changed(self.state_var, f"{state_text} · {mode}")
        if snapshot.state == "pending":
            status, style = f"Switch to your game · {snapshot.pending_seconds:.1f}s", "Live.TLabel"
        elif snapshot.state == "armed":
            status = "Preview running · No game input" if preview else "Live control active · F8 stops input"
            style = "Status.TLabel" if preview else "Live.TLabel"
        elif snapshot.backend_fault and requested_mode == "live":
            status, style = "Stopped · Restart before live input", "Stopped.TLabel"
        elif snapshot.reason == "emergency_stop":
            status, style = "Emergency stop · Controls are off", "Stopped.TLabel"
        else:
            status = "Paused · Preview" if preview else "Paused · Game input is off"
            style = "Paused.TLabel"
        set_var_if_changed(self.status_var, status)
        configure_if_changed(self.status_label, style=style)
        enabled = sum(command.enabled for command in self._profile.commands)
        readiness = session_readiness(
            mode=("preview" if snapshot.preview else "live") if snapshot.state in {"armed", "pending"} else requested_mode,
            state=snapshot.state, connected=snapshot.connected,
            available=available, safety_ready=safety_ready,
            target_selected=target_selected, faulted=snapshot.backend_fault,
            worker_stopped=self._stop.is_set(), enabled_commands=enabled,
            command_draft=self.command_editor.dirty, safety_draft=self._dirty,
            acknowledged=self.ack_var.get(),
        )
        self._readiness = readiness
        pause = paused_guidance(snapshot.reason) if snapshot.state == "disarmed" else None
        # Keep a recoverable stop visible instead of immediately replacing its
        # cause with a generic Ready message. Readiness remains authoritative
        # for Start's enabled state; this text never grants input authority.
        if pause and (requested_mode == "live" or snapshot.reason == "emergency_stop"):
            heading, detail = pause
            if not readiness.ready and readiness.detail not in detail:
                detail += " " + readiness.detail
        else:
            heading, detail = readiness.headline, readiness.detail
        set_var_if_changed(self.readiness_var, heading)
        set_var_if_changed(self.readiness_detail_var, detail)
        action_labels = {"commands": "Review commands", "safety": "Review settings", "connect": "Connect Twitch",
                         "game": "Choose game", "ack": "Review reminder", "restart": "Restart help"}
        configure_if_changed(self.next_step_button, text=action_labels.get(readiness.action, "Review commands"))
        configure_if_changed(self.arm_button, text="Start preview" if requested_mode == "preview" else "Arm game control · 3 s")
        set_enabled(self.arm_button, readiness.ready)
        set_enabled(self.pause_button, snapshot.state in {"armed", "pending"})
        can_simulate = requested_mode == "preview" and snapshot.preview and snapshot.state == "armed"
        set_enabled(self.sim_button, can_simulate)
        sim_state = (requested_mode, snapshot.state, snapshot.preview)
        if getattr(self, "_last_sim_state", None) != sim_state:
            self._last_sim_state = sim_state
            if snapshot.state in {"armed", "pending"} and not snapshot.preview:
                simulator_hint = "Pause live control, then switch to Preview before using the simulator."
            elif can_simulate:
                simulator_hint = "Preview is running. Try a command and compare different viewer roles."
            else:
                simulator_hint = "Start Preview to try a command. No keyboard or mouse input will be sent."
            set_var_if_changed(self.sim_result_var, simulator_hint)
        triggers = tuple(command.trigger for command in self._profile.commands if command.enabled)
        configure_if_changed(self.sim_entry, values=triggers)
        if getattr(self, "_sim_commands", None) != self._profile.commands:
            self._sim_commands = self._profile.commands
            if normalize_command(self.sim_var.get()) not in triggers:
                self.sim_var.set(triggers[0] if triggers else "")
        self._update_sim_action()
        audience = AUDIENCE_LABELS.get(self._profile.audience, "Review audience")
        mouse_count = sum(command.enabled and command.is_mouse for command in self._profile.commands)
        mouse_summary = f" · {mouse_count} mouse" if mouse_count else ""
        set_var_if_changed(self.profile_summary_var, f"{self._profile.name}\n{enabled} enabled commands{mouse_summary} · {audience}")
        if mouse_count:
            self.mouse_hint.pack(anchor="w", pady=(8, 0))
        else:
            self.mouse_hint.pack_forget()
        allowed = f"{len(self._profile.allowed_user_ids)} allowed IDs" if self._profile.allowed_user_ids else "Allowed-user list off"
        set_var_if_changed(self.access_summary_var, f"Applied: {allowed} · {len(self._profile.blocked_user_ids)} blocked IDs. Queue: {self._profile.queue_size}; expiry: {self._profile.queue_ttl_ms} ms.")
        if not available:
            safety = "Preview works here. Live game control requires the Windows app and its native safety checks."
        elif safety_ready:
            safety = "F8 is ready. Focus, process, held-input, and mouse-target safeguards are active for live control."
        else:
            safety = "F8 or native safety checks are not ready. Free F8 in other apps and restart without administrator rights."
        set_var_if_changed(self.safety_var, safety)
        live = requested_mode == "live"
        show_connection = live or self.connection_setup_var.get()
        layout_key = (live, show_connection)
        if getattr(self, "_session_layout_key", None) != layout_key:
            self._session_layout_key = layout_key
            if live:
                self.preview_card.grid_remove()
                self.game_card.grid()
            else:
                self.preview_card.grid()
                self.game_card.grid_remove()
            if show_connection:
                self.connection_card.grid(columnspan=1 if live else 2, padx=(0, 7) if live else 0)
            else:
                self.connection_card.grid_remove()
        configure_if_changed(self.connection_setup_button, text="Hide Twitch setup" if show_connection else "Connect Twitch to test real chat...")
        self._update_profile_status()
        self._ui_signature = signature
        return readiness

    def _update_sim_action(self) -> None:
        trigger = normalize_command(self.sim_var.get())
        command = next((item for item in self._profile.commands if item.trigger == trigger and item.enabled), None)
        detail = f"Mapped action: {command.action_summary}." if command else "Choose an enabled command, or type one exact trigger to test it."
        set_var_if_changed(self.sim_action_var, detail)

    def _render_preview_event(self, event: EngineEvent) -> None:
        snapshot = self.engine.snapshot()
        if (snapshot.state != "armed" or not snapshot.preview or self.mode_var.get() != "preview"
                or event.generation != snapshot.generation):
            return
        if (event.kind == "preview" and type(event.action) is Command and event.action.enabled
                and event.command == event.action.trigger):
            self.preview_pad.show_command(event.action)
            result = f"Preview ran: {event.action.action_summary}. No keyboard or mouse input was sent."
        elif event.kind in {"rejected", "dropped"}:
            result = reason_text(event.reason)
            self.preview_pad.show_rejection(result)
        else:
            return
        set_var_if_changed(self.sim_result_var, result)
        set_var_if_changed(self.notice_var, result)

    def _copy_chat_commands(self) -> bool:
        # This explicit local action validates pending edits, then copies only
        # the approved profile reference. No credentials or runtime data enter
        # that helper. Copying does not reset policy history or grant input authority.
        self.command_editor.cancel_capture()
        if not self._apply_pending_profile():
            return False
        count = sum(command.enabled for command in self._profile.commands)
        if not count:
            self.notice_var.set("Add or enable a command before copying your chat controls.")
            self._go_to_step("commands")
            return False
        try:
            reference = command_reference(self._profile)
            self.root.clipboard_clear()
            self.root.clipboard_append(reference)
            self.root.update_idletasks()
        except (tk.TclError, TypeError, ValueError):
            self.notice_var.set("The command list could not be copied. Your profile is still available; try Copy chat commands again.")
            return False
        self.notice_var.set(f"Copied {count} enabled chat {'command' if count == 1 else 'commands'}. Paste the list where your viewers can see it.")
        return True

    def _log(self, text: str) -> None:
        # Existing callers supply only approved status strings and validated mappings.
        append_log_lines(self.log, [text])

    def _clear_log(self) -> None:
        clear_log(self.log)

    def _connect(self) -> None:
        self.connection_setup_var.set(True)
        with self._connection_lock:
            already_running = self._client is not None and self._client.running
        if already_running:
            self._disconnect()
            return
        with self._connection_lock:
            self._generation += 1
            generation = self._generation
            self.engine.set_connected(False)

            def status(kind: str, detail: str) -> None:
                with self._connection_lock:
                    if generation != self._generation or self._closed:
                        return
                    self.engine.set_connected(kind == "connected")
                    self._post("status", (generation, kind, detail))

            def chat(message: ChatMessage) -> None:
                with self._connection_lock:
                    if generation == self._generation and not self._closed:
                        self.engine.submit(message)

            def device(uri: str, code: str) -> None:
                with self._connection_lock:
                    if generation == self._generation and not self._closed:
                        self._post("device", (generation, uri, code))

            try:
                client = self._twitch_factory(
                    client_id=self.client_id_var.get().strip(),
                    channel=self.channel_var.get().strip(),
                    on_chat=chat, on_status=status, on_device=device,
                )
                self._client = client
                client.start()
            except ValueError:
                self._client = None
                messagebox.showerror("Check Twitch setup", "Enter your channel name and the Client ID of your own Twitch Public application.", parent=self.root)
                return
            except Exception:
                self.engine.set_connected(False)
                self._client = None
                messagebox.showerror("Connection unavailable", "Twitch sign-in could not start. Check your settings and try again.", parent=self.root)
                return
        configure_if_changed(self.connect_button, text="Disconnect Twitch")
        self.channel_entry.state(["disabled"])
        self.client_entry.state(["disabled"])

    def _disconnect(self) -> None:
        with self._connection_lock:
            self._generation += 1
            self.engine.set_connected(False)
            client, self._client = self._client, None
        if client is not None:
            client.stop()
        self._clear_device()
        set_var_if_changed(self.connection_var, "Twitch not connected")
        configure_if_changed(self.connect_button, text="Connect Twitch")
        self.channel_entry.state(["!disabled"])
        self.client_entry.state(["!disabled"])
        self.notice_var.set("Disconnected. Controls are disarmed; session authorization has been discarded.")

    def _show_device(self, uri: str, code: str) -> None:
        self._clear_device()
        if uri != ACTIVATE_URL:
            self._disconnect()
            return
        self.engine.disarm("authorization_prompt")
        dialog = tk.Toplevel(self.root)
        self._device_dialog = dialog
        dialog.title("Authorize your Twitch channel")
        dialog.geometry("500x310")
        dialog.configure(bg=BG)
        dialog.transient(self.root)
        dialog.protocol("WM_DELETE_WINDOW", self._disconnect)
        body = ttk.Frame(dialog, padding=24)
        body.pack(fill="both", expand=True)
        ttk.Label(body, text="Keep this window off your stream", style="Heading.TLabel").pack(anchor="w")
        ttk.Label(body, text="Open Twitch activation in your browser, sign in to your channel, and enter this temporary code:", wraplength=440, style="Muted.TLabel").pack(anchor="w", pady=(12, 16))
        self._device_value = tk.StringVar(value=code)
        ttk.Label(body, textvariable=self._device_value, font=(self._mono_family, 24, "bold"), foreground=ACCENT).pack(anchor="w")
        ttk.Button(body, text="Open Twitch activation", style="Accent.TButton", command=lambda: webbrowser.open(ACTIVATE_URL)).pack(anchor="w", pady=(20, 8))
        ttk.Button(body, text="Cancel sign-in", command=self._disconnect).pack(anchor="w")
        dialog.update_idletasks()
        width = min(max(500, dialog.winfo_reqwidth()), self.root.winfo_screenwidth() - 60)
        height = min(max(310, dialog.winfo_reqheight()), self.root.winfo_screenheight() - 60)
        dialog.geometry(f"{width}x{height}")

    def _clear_device(self) -> None:
        if self._device_value is not None:
            self._device_value.set("")
            self._device_value = None
        if self._device_dialog is not None:
            self._device_dialog.destroy()
            self._device_dialog = None

    def _refresh_windows(self) -> None:
        self.engine.disarm("window_list_refreshed")
        try:
            self._windows = self.backend.enumerate_windows()
        except Exception:
            self._windows = []
        labels = [str(getattr(window, "label", getattr(window, "title", "Game window"))) for window in self._windows]
        self.window_combo.configure(values=labels)
        self.window_var.set("Choose a game window" if labels else "No eligible game windows found")
        # A refresh requires selecting again; never retain an invisible binding.
        self.backend.select_target(None)

    def _select_window(self, _event: Any = None) -> None:
        self.engine.disarm("target_changed")
        index = self.window_combo.current()
        if 0 <= index < len(self._windows):
            try:
                self.backend.select_target(self._windows[index])
            except Exception:
                self.backend.select_target(None)
                self.notice_var.set("That window cannot be bound safely. Refresh and select a game running without administrator rights.")

    def _mode_changed(self) -> None:
        self.engine.disarm("mode_changed")
        self.ack_var.set(False)
        self.command_editor.cancel_capture()
        self.preview_pad.reset()
        self.notice_var.set("Mode changed. Controls are paused; start when you are ready.")
        self._refresh_session_ui()

    def _ack_changed(self) -> None:
        if not self.ack_var.get():
            self.engine.disarm("acknowledgment_cleared")
        self._refresh_session_ui()

    def _arm(self) -> None:
        if self._stop.is_set():
            messagebox.showerror("Safety worker stopped", "Restart the app before arming controls.", parent=self.root)
            return
        self.command_editor.cancel_capture()
        if not self._apply_pending_profile():
            return
        readiness = self._refresh_session_ui()
        if not readiness.ready:
            self.notice_var.set(readiness.headline + ". " + readiness.detail)
            return
        preview = self.mode_var.get() == "preview"
        if not preview and not self.ack_var.get():
            messagebox.showinfo("Review game focus", "Pause controls before opening game chat, consoles, overlays, purchases, or sensitive menus. Check the acknowledgment before enabling live input.", parent=self.root)
            return
        if not self.engine.arm(preview=preview, delay_seconds=3.0):
            self.notice_var.set(reason_text(self.engine.snapshot().reason))
        elif not preview:
            self.notice_var.set("Focus your selected game now. Live input starts only if it is in focus after 3 seconds.")
        else:
            self.preview_pad.reset()
            self.notice_var.set("Preview started. Try a command on Session; no keyboard or mouse input is sent.")
        self._refresh_session_ui()

    def _panic(self) -> None:
        self.command_editor.cancel_capture()
        self.engine.emergency_stop()
        self.notice_var.set("Emergency stop. All queued controls cleared; manual arming is required.")
        self._refresh_session_ui()

    def _simulate(self) -> None:
        snapshot = self.engine.snapshot()
        if self.mode_var.get() != "preview" or not snapshot.preview or snapshot.state != "armed":
            self.sim_result_var.set("Start Preview before trying a command. Live game input cannot use this simulator.")
            return
        role = self.sim_role.get()
        message = ChatMessage(
            message_id=f"preview-{uuid.uuid4()}", user_id="100000001", login="preview_viewer",
            text=self.sim_var.get(), roles=frozenset() if role == "viewer" else frozenset({role}),
            channel_id="100000002", received_at=time.monotonic(),
        )
        decision = self.engine.submit(message)
        if decision.accepted:
            result = f"Accepted as {ROLE_LABELS.get(role, 'Viewer')}. The mapped action is queued in Preview; no keyboard or mouse input will be sent."
        else:
            result = reason_text(decision.reason)
            self.preview_pad.show_rejection(result)
        self.sim_result_var.set(result)
        self.notice_var.set(result)

    def _profile_edited(self, *_args: Any) -> None:
        if not self._loading:
            self._dirty = True
            self.engine.disarm("profile_editing")
            self._update_profile_status()

    def _command_draft_edited(self) -> None:
        self.engine.disarm("command_editing")
        self._update_profile_status()

    def _has_unsaved_changes(self) -> bool:
        editor_dirty = getattr(getattr(self, "command_editor", None), "dirty", False)
        return self._dirty or editor_dirty or self._profile != self._saved_profile

    def _update_profile_status(self) -> None:
        if not hasattr(self, "profile_status_var"):
            return
        if self._profile_path is None:
            location = "No profile file selected"
        else:
            location = "File: " + "".join(c for c in self._profile_path.name if c.isprintable())[:80]
        state = "Unsaved changes" if self._has_unsaved_changes() else (
            "Saved" if self._profile_path is not None else "Built-in starter profile"
        )
        value = f"{state}  |  {location}  |  Save keeps commands and safety settings only."
        if self.profile_status_var.get() != value:
            self.profile_status_var.set(value)

    def _show_profile(self, profile: Profile, *, refresh_editor: bool = True) -> None:
        if profile != self._profile:
            self.preview_pad.reset()
        self._loading = True
        try:
            data = profile.to_dict()
            self.profile_name.set(data["name"])
            self.audience_var.set(data["audience"])
            for field, var in self.limit_vars.items():
                var.set(str(data[field]))
            self.allowed_var.set(", ".join(data["allowed_user_ids"]))
            self.blocked_var.set(", ".join(data["blocked_user_ids"]))
            if refresh_editor:
                self.command_editor.set_commands(profile.commands)
            self._profile = profile
            self._dirty = False
        finally:
            self._loading = False
        self._update_profile_status()

    def _read_profile_fields(self) -> Profile:
        data = self._profile.to_dict()
        data["name"] = self.profile_name.get()
        data["audience"] = self.audience_var.get()
        for field, var in self.limit_vars.items():
            value = var.get().strip()
            if not value.isascii() or not value.isdecimal() or len(value) > 6:
                raise ProfileError(f"{field.replace('_', ' ')} must be a whole number within the displayed range.")
            data[field] = int(value)
        for field, var in (("allowed_user_ids", self.allowed_var), ("blocked_user_ids", self.blocked_var)):
            raw = var.get().strip()
            data[field] = [part.strip() for part in raw.split(",")] if raw else []
        return Profile.from_dict(data)

    def _profile_error(self, error: Exception) -> None:
        detail = str(error) if isinstance(error, ProfileError) else "The profile could not be validated."
        field = next((name for name in self.safety_entries if name in detail or name.replace("_", " ") in detail), None)
        if "Profile name" in detail:
            self._last_profile_error_widget = self.profile_name_entry
        else:
            if field in {"queue_size", "queue_ttl_ms", "allowed_user_ids", "blocked_user_ids"} and not self._advanced_open:
                self._toggle_advanced()
            self._last_profile_error_widget = self.safety_entries.get(field, self.audience_combo)
        self._focus_widget(self._last_profile_error_widget)
        labels = {"global_cooldown_ms": "Between commands (ms)", "viewer_cooldown_ms": "Each viewer waits (ms)",
                  "max_actions_per_minute": "Actions per minute", "queue_size": "Queued actions",
                  "queue_ttl_ms": "Drop waiting input after (ms)", "allowed_user_ids": "Only these Twitch user IDs",
                  "blocked_user_ids": "Blocked Twitch user IDs"}
        if field is not None:
            detail = detail.replace(field, labels[field]).replace(field.replace("_", " "), labels[field])
        detail = detail.replace("–", " to ")
        messagebox.showerror("Check safety settings", detail, parent=self.root)
        self.notice_var.set("Correct the safety settings before applying commands, saving, or arming.")

    def _apply_profile(self) -> bool:
        self.engine.disarm("profile_apply")
        try:
            profile = self._read_profile_fields()
            self.engine.set_profile(profile)
        except (ValueError, TypeError) as error:
            self._profile_error(error)
            return False
        # Applying safety fields must not erase a pending command draft.
        self._show_profile(profile, refresh_editor=False)
        self.notice_var.set("Safety settings applied. Use Save profile to keep them for another session.")
        return True

    def _commands_changed(self, commands: tuple[Command, ...]) -> bool:
        self.engine.disarm("commands_changed")
        try:
            # Validate both sets of fields before changing either. Editor owns
            # its draft/selection until this callback confirms success.
            profile = replace(self._read_profile_fields(), commands=commands)
            self.engine.set_profile(profile)
        except (ValueError, TypeError) as error:
            self._profile_error(error)
            return False
        self._show_profile(profile, refresh_editor=False)
        self.notice_var.set("Command mapping applied. Use Save profile to keep it for another session.")
        return True

    def _apply_pending_profile(self) -> bool:
        self._last_profile_error_widget = None
        if not self.command_editor.apply_pending():
            self.engine.disarm("invalid_command_draft")
            if self._last_profile_error_widget is not None:
                self._focus_widget(self._last_profile_error_widget)
            else:
                self.notebook.select(1)
                self.root.update_idletasks()
                self.command_editor.focus_problem()
            return False
        return self._apply_profile() if self._dirty else True

    def _preview_command(self, command: Command) -> str:
        # A mapping preview never arms the connected-chat engine. Its backend
        # has no operating-system input API, even when a game is selected.
        self.engine.disarm("command_preview")
        self.command_editor.cancel_capture()
        self.mode_var.set("preview")
        self._mode_changed()
        if not self._apply_pending_profile():
            return "Preview stopped. Correct the command or safety settings first."
        if command not in self._profile.commands:
            return "Apply this command before previewing it."
        role = self.sim_role.get()
        events: list[EngineEvent] = []
        preview = SafetyEngine(PreviewBackend(), on_event=events.append, profile=self._profile, clock=time.monotonic)
        preview.arm(preview=True)
        message = ChatMessage(
            message_id=f"preview-{uuid.uuid4()}", user_id="100000001", login="preview_viewer",
            text=command.trigger, roles=frozenset() if role == "viewer" else frozenset({role}),
            channel_id="100000002", received_at=time.monotonic(),
        )
        decision = preview.submit(message)
        if decision.accepted:
            preview.tick()
            ran = any(event.kind == "preview" and event.action == command for event in events)
            if ran:
                self.preview_pad.show_command(command)
                result = f"Preview only: {command.trigger} -> {command.action_summary}. Allowed as {role}; no keyboard or mouse input sent. See the practice pad on Session."
            else:
                result = "Preview expired before it could run. Try again; no keyboard or mouse input was sent."
                self.preview_pad.show_rejection(reason_text("queue_expired"))
        else:
            result = f"Preview as {role}: {decision.reason.replace('_', ' ')}. No keyboard or mouse input sent. Change the preview role on Session if needed."
            self.preview_pad.show_rejection(reason_text(decision.reason))
        preview.disarm("preview_complete")
        self.notice_var.set(result)
        self._log(result)
        return result

    def _confirm_profile_replacement(self, action: str) -> bool:
        self.engine.disarm("profile_navigation")
        self.command_editor.cancel_capture()
        if not self._has_unsaved_changes():
            return True
        answer = messagebox.askyesnocancel(
            "Save profile changes?",
            f"Save your command mappings and safety settings before {action}?"
            "\n\nYes saves them. No discards them. Cancel keeps the current profile open.",
            parent=self.root,
        )
        if answer is None:
            return False
        if answer:
            return self._save_profile()
        return True

    def _new_profile(self) -> None:
        self.engine.disarm("new_profile")
        self.command_editor.cancel_capture()
        try:
            profile = new_profile(self.template_var.get())
        except ValueError:
            self.notice_var.set("Choose a starting profile from the list, then select New profile.")
            return
        if not self._confirm_profile_replacement("creating a new profile"):
            return
        self.engine.set_profile(profile)
        self._profile_path = None
        self._saved_profile = None
        self._show_profile(profile)
        self.notice_var.set("New profile created with moderator-only defaults. Name it, configure commands, and Save profile.")

    def _import_profile(self) -> None:
        self.engine.disarm("profile_open")
        self.command_editor.cancel_capture()
        path = filedialog.askopenfilename(title="Open command profile", filetypes=[("Command profiles", "*.json")], parent=self.root)
        if not path:
            return
        try:
            profile = load_profile(path)
        except (OSError, ValueError, TypeError):
            messagebox.showerror("Profile rejected", "The file could not be opened as a valid command profile. Your current profile and draft edits are unchanged.", parent=self.root)
            return
        if not self._confirm_profile_replacement("opening another profile"):
            return
        try:
            # Saving the current profile in the prompt may have updated the same
            # file. Read and validate again rather than loading stale contents.
            profile = load_profile(path)
        except (OSError, ValueError, TypeError):
            messagebox.showerror("Profile changed", "The selected file changed or became unreadable. The current profile remains open.", parent=self.root)
            return
        self.engine.set_profile(profile)
        self._profile_path = Path(path)
        self._saved_profile = profile
        self._show_profile(profile)
        self.notice_var.set("Profile opened. Review commands and audience before arming.")

    def _save_profile(self, *, save_as: bool = False) -> bool:
        self.engine.disarm("profile_save")
        self.command_editor.cancel_capture()
        path = self._profile_path
        if save_as or path is None:
            chosen = filedialog.asksaveasfilename(
                title="Save command profile", defaultextension=".json",
                initialfile=path.name if path is not None else "game-profile.json",
                filetypes=[("Command profiles", "*.json")], parent=self.root,
            )
            if not chosen:
                return False
            path = Path(chosen)
        if not self._apply_pending_profile():
            return False
        try:
            save_profile(self._profile, path)
        except (OSError, ValueError, TypeError):
            messagebox.showerror("Could not save profile", "The profile was not saved. Choose a writable location and try again; your changes remain in the app.", parent=self.root)
            return False
        self._profile_path = path
        self._saved_profile = self._profile
        self._update_profile_status()
        self.notice_var.set("Profile saved. Only command mappings and safety settings were written.")
        return True

    def _export_profile(self) -> None:
        self._save_profile(save_as=True)

    def close(self, *, force: bool = False) -> None:
        if self._closed:
            return
        self.engine.disarm("shutdown_requested")
        self.command_editor.cancel_capture()
        if not force and not self._confirm_profile_replacement("closing the app"):
            return
        self._closed = True
        if self._pump_id is not None:
            self.root.after_cancel(self._pump_id)
            self._pump_id = None
        self._stop.set()
        self._disconnect()
        self.client_id_var.set("")
        if self._worker is not None:
            self._worker.join(timeout=0.5)
        self.backend.close()
        self.root.destroy()


def main() -> None:
    parser = argparse.ArgumentParser(description="Twitch Chat Control desktop app")
    parser.add_argument("--smoke-test", action="store_true", help="Construct and close the GUI without network or native input")
    args = parser.parse_args()
    root = tk.Tk()
    app = App(root, start_workers=not args.smoke_test)
    if args.smoke_test:
        root.update_idletasks()
        app.close()
    else:
        root.mainloop()
