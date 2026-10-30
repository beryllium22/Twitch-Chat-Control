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
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Any, Callable

from . import __version__
from .engine import SafetyEngine
from .models import AUDIENCES, SAFE_KEYS, Command, Profile
from .profile_io import load_profile, save_profile
from .twitch import ACTIVATE_URL, ChatMessage, TwitchClient
from .windows import WindowsBackend

BG = "#101722"
PANEL = "#182334"
TEXT = "#edf2f8"
MUTED = "#a6b8cb"
ACCENT = "#65e0c1"


class ScrollPage(ttk.Frame):
    """Keep all local controls reachable on smaller or scaled displays."""

    def __init__(self, parent: ttk.Notebook) -> None:
        super().__init__(parent)
        self.canvas = tk.Canvas(self, bg=BG, highlightthickness=0, borderwidth=0)
        scroll = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)
        self.content = ttk.Frame(self.canvas, padding=20)
        self._window = self.canvas.create_window((0, 0), window=self.content, anchor="nw")
        self.content.bind("<Configure>", self._resize_content)
        self.canvas.bind("<Configure>", self._resize_canvas)
        self.bind_all("<MouseWheel>", self._wheel, add="+")
        self.bind_all("<Button-4>", self._wheel, add="+")
        self.bind_all("<Button-5>", self._wheel, add="+")

    def _resize_content(self, _event: Any) -> None:
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _resize_canvas(self, event: Any) -> None:
        self.canvas.itemconfigure(self._window, width=event.width)

    def _wheel(self, event: Any) -> None:
        if not self.winfo_ismapped() or isinstance(event.widget, ttk.Combobox):
            return
        x, y = self.winfo_rootx(), self.winfo_rooty()
        if not (x <= event.x_root < x + self.winfo_width() and y <= event.y_root < y + self.winfo_height()):
            return
        if self.content.winfo_reqheight() <= self.canvas.winfo_height():
            return
        if getattr(event, "num", 0) == 4:
            direction = -1
        elif getattr(event, "num", 0) == 5:
            direction = 1
        else:
            direction = -1 if event.delta > 0 else 1
        self.canvas.yview_scroll(direction * 3, "units")


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
        self._windows: list[Any] = []
        self._device_dialog: tk.Toplevel | None = None
        self._device_value: tk.StringVar | None = None
        self._loading = True
        self._dirty = False
        self._worker: threading.Thread | None = None
        self.engine = SafetyEngine(self.backend, on_event=lambda event: self._post("decision", event))

        self.root.title(f"Twitch Chat Control · {__version__}")
        self.root.geometry("1140x820")
        self.root.minsize(980, 740)
        self.root.configure(bg=BG)
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.root.bind("<F8>", lambda _event: self._panic())
        self._style()
        self._build()
        self._show_profile(self._profile)
        self._loading = False
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
        self.root.after(50, self._pump)
        self._post("notice", "Ready in preview. Review commands, then arm to try the simulator.")

    def _style(self) -> None:
        families = {name.lower() for name in tkfont.families(self.root)}
        self._font_family = "Segoe UI" if "segoe ui" in families else tkfont.nametofont("TkDefaultFont").actual("family")
        self._mono_family = "Consolas" if "consolas" in families else "Courier"
        style = ttk.Style(self.root)
        style.theme_use("clam")
        style.configure(".", background=BG, foreground=TEXT, font=(self._font_family, 10))
        style.configure("TFrame", background=BG)
        style.configure("Card.TFrame", background=PANEL)
        style.configure("TLabel", background=BG, foreground=TEXT)
        style.configure("Muted.TLabel", foreground=MUTED)
        style.configure("Title.TLabel", font=(self._font_family, 24, "bold"))
        style.configure("Heading.TLabel", font=(self._font_family, 13, "bold"))
        style.configure("Status.TLabel", foreground=ACCENT, font=(self._font_family, 12, "bold"))
        style.configure("TButton", padding=(14, 9), background="#293a50", foreground=TEXT)
        style.map("TButton", background=[("active", "#354b67")])
        style.configure("Accent.TButton", background="#237f70", foreground="white")
        style.map("Accent.TButton", background=[("active", "#299984")])
        style.configure("Danger.TButton", background="#9b3446", foreground="white", font=(self._font_family, 11, "bold"))
        style.map("Danger.TButton", background=[("active", "#bd4157")])
        style.configure("TEntry", fieldbackground="#233146", foreground=TEXT, insertcolor=TEXT, padding=6)
        style.configure("TCombobox", fieldbackground="#233146", foreground=TEXT, padding=6)
        style.map("TCombobox", fieldbackground=[("readonly", "#233146")], foreground=[("readonly", TEXT)])
        style.configure("TNotebook", background=BG, borderwidth=0)
        style.configure("TNotebook.Tab", padding=(18, 11), background=PANEL)
        style.map("TNotebook.Tab", background=[("selected", "#2b4058")])
        style.configure("Treeview", background=PANEL, fieldbackground=PANEL, foreground=TEXT, rowheight=31)
        style.configure("Treeview.Heading", background="#26374e", foreground=TEXT, padding=7)
        style.map("Treeview", background=[("selected", "#355c72")])
        style.configure("TRadiobutton", padding=(0, 5))
        style.configure("TCheckbutton", padding=(0, 5))

    def _build(self) -> None:
        outer = ttk.Frame(self.root, padding=24)
        outer.pack(fill="both", expand=True)
        heading = ttk.Frame(outer)
        heading.pack(fill="x")
        title = ttk.Frame(heading)
        title.pack(side="left", fill="x", expand=True)
        ttk.Label(title, text="Twitch Chat Control", style="Title.TLabel").pack(anchor="w")
        ttk.Label(title, text="Your chat. Your game. Your rules.", style="Muted.TLabel").pack(anchor="w", pady=(4, 0))
        ttk.Button(heading, text="STOP ALL · F8", style="Danger.TButton", command=self._panic).pack(side="right")

        status_row = ttk.Frame(outer, padding=(0, 18, 0, 14))
        status_row.pack(fill="x")
        self.state_var = tk.StringVar(value="DISARMED · PREVIEW")
        self.connection_var = tk.StringVar(value="Twitch: disconnected")
        ttk.Label(status_row, textvariable=self.state_var, style="Status.TLabel").pack(side="left")
        ttk.Label(status_row, textvariable=self.connection_var, style="Muted.TLabel").pack(side="right")

        book = ttk.Notebook(outer)
        self.notebook = book
        book.pack(fill="both", expand=True)
        session_page = ScrollPage(book)
        self.session_tab = session_page.content
        self.commands_tab = ttk.Frame(book, padding=20)
        safety_page = ScrollPage(book)
        self.safety_tab = safety_page.content
        self.activity_tab = ttk.Frame(book, padding=20)
        for frame, label in (
            (session_page, "Session"),
            (self.commands_tab, "Commands"),
            (safety_page, "Safety & access"),
            (self.activity_tab, "Activity"),
        ):
            book.add(frame, text=label)
        self._build_session()
        self._build_commands()
        self._build_safety()
        self._build_activity()

        footer = ttk.Frame(outer, padding=(0, 12, 0, 0))
        footer.pack(fill="x")
        self.notice_var = tk.StringVar(value="")
        ttk.Label(footer, textvariable=self.notice_var, style="Muted.TLabel", wraplength=1030).pack(anchor="w")

    def _build_session(self) -> None:
        frame = self.session_tab
        frame.columnconfigure(1, weight=1)
        ttk.Label(frame, text="1  Connect your channel", style="Heading.TLabel").grid(row=0, column=0, columnspan=3, sticky="w")
        ttk.Label(frame, text="Authorize your own channel with a Twitch Public application. No client secret.", style="Muted.TLabel").grid(row=1, column=0, columnspan=3, sticky="w", pady=(3, 12))
        self.channel_var = tk.StringVar()
        self.client_id_var = tk.StringVar()
        ttk.Label(frame, text="Channel name or URL").grid(row=2, column=0, sticky="w", padx=(0, 14))
        self.channel_entry = ttk.Entry(frame, textvariable=self.channel_var)
        self.channel_entry.grid(row=2, column=1, sticky="ew", pady=4)
        ttk.Button(frame, text="Twitch app setup", command=lambda: webbrowser.open("https://dev.twitch.tv/console/apps")).grid(row=2, column=2, padx=(12, 0))
        ttk.Label(frame, text="Your Public Client ID").grid(row=3, column=0, sticky="w")
        self.client_entry = ttk.Entry(frame, textvariable=self.client_id_var, show="*")
        self.client_entry.grid(row=3, column=1, sticky="ew", pady=4)
        self.connect_button = ttk.Button(frame, text="Connect", command=self._connect)
        self.connect_button.grid(row=3, column=2, sticky="ew", padx=(12, 0))
        ttk.Label(frame, text="Authorization lives in memory and is discarded when you disconnect or quit.", style="Muted.TLabel").grid(row=4, column=0, columnspan=3, sticky="w", pady=(4, 17))

        ttk.Separator(frame).grid(row=5, column=0, columnspan=3, sticky="ew", pady=(0, 15))
        ttk.Label(frame, text="2  Choose a game window", style="Heading.TLabel").grid(row=6, column=0, columnspan=3, sticky="w")
        self.window_var = tk.StringVar(value="Select a game window for live input")
        self.window_combo = ttk.Combobox(frame, textvariable=self.window_var, state="readonly")
        self.window_combo.grid(row=7, column=0, columnspan=2, sticky="ew", pady=(10, 4))
        self.window_combo.bind("<<ComboboxSelected>>", self._select_window)
        ttk.Button(frame, text="Refresh windows", command=self._refresh_windows).grid(row=7, column=2, sticky="ew", padx=(12, 0))
        ttk.Label(frame, text="Controls stop when the selected game loses focus. Browsers and terminals are excluded.", style="Muted.TLabel").grid(row=8, column=0, columnspan=3, sticky="w", pady=(4, 17))

        ttk.Separator(frame).grid(row=9, column=0, columnspan=3, sticky="ew", pady=(0, 15))
        ttk.Label(frame, text="3  Review, then arm", style="Heading.TLabel").grid(row=10, column=0, columnspan=3, sticky="w")
        self.mode_var = tk.StringVar(value="preview")
        modes = ttk.Frame(frame)
        modes.grid(row=11, column=0, columnspan=3, sticky="w", pady=(5, 0))
        ttk.Radiobutton(modes, text="Preview · no input", variable=self.mode_var, value="preview", command=self._mode_changed).pack(side="left", padx=(0, 30))
        self.live_radio = ttk.Radiobutton(modes, text="Live game input · Windows", variable=self.mode_var, value="live", command=self._mode_changed)
        self.live_radio.pack(side="left")
        if not self.backend.available:
            self.live_radio.state(["disabled"])
        self.ack_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            frame, variable=self.ack_var,
            text="I will pause before opening game chat, console, overlays, or sensitive menus.",
            command=self._ack_changed,
        ).grid(row=12, column=0, columnspan=3, sticky="w")
        controls = ttk.Frame(frame)
        controls.grid(row=13, column=0, columnspan=3, sticky="ew", pady=(6, 0))
        self.arm_button = ttk.Button(controls, text="Arm preview", style="Accent.TButton", command=self._arm)
        self.arm_button.pack(side="left", padx=(0, 10))
        ttk.Button(controls, text="Disarm", command=lambda: self.engine.disarm("stopped_locally")).pack(side="left")
        ttk.Label(controls, text="Live arming gives you 3 seconds to focus your game.", style="Muted.TLabel").pack(side="left", padx=(18, 0))

        simulator = ttk.Frame(frame, padding=(0, 17, 0, 0))
        simulator.grid(row=14, column=0, columnspan=3, sticky="ew")
        ttk.Label(simulator, text="Preview command").pack(side="left", padx=(0, 10))
        self.sim_var = tk.StringVar(value="!jump")
        self.sim_entry = ttk.Entry(simulator, textvariable=self.sim_var, width=18)
        self.sim_entry.pack(side="left", padx=(0, 8))
        self.sim_role = tk.StringVar(value="moderator")
        ttk.Combobox(simulator, textvariable=self.sim_role, state="readonly", width=13, values=("viewer", "subscriber", "vip", "moderator", "broadcaster")).pack(side="left", padx=(0, 8))
        self.sim_button = ttk.Button(simulator, text="Try command", command=self._simulate)
        self.sim_button.pack(side="left")
        self.sim_entry.bind("<Return>", lambda _event: self._simulate())

    def _build_commands(self) -> None:
        frame = self.commands_tab
        top = ttk.Frame(frame)
        top.pack(fill="x", pady=(0, 12))
        ttk.Label(top, text="Approved game commands", style="Heading.TLabel").pack(side="left")
        ttk.Button(top, text="Import profile", command=self._import_profile).pack(side="right", padx=(8, 0))
        ttk.Button(top, text="Export profile", command=self._export_profile).pack(side="right")
        ttk.Label(frame, text="One exact command maps to one short key press. No chat arguments, scripts, mouse input, or key combinations.", style="Muted.TLabel", wraplength=990).pack(anchor="w", pady=(0, 12))
        self.command_tree = ttk.Treeview(frame, columns=("trigger", "key", "hold", "cooldown", "enabled"), show="headings", height=10, selectmode="browse")
        for name, title, width in (("trigger", "Chat command", 220), ("key", "Game key", 160), ("hold", "Hold (ms)", 130), ("cooldown", "Cooldown (ms)", 160), ("enabled", "Enabled", 100)):
            self.command_tree.heading(name, text=title)
            self.command_tree.column(name, width=width, anchor="w")
        self.command_tree.pack(fill="both", expand=True)
        self.command_tree.bind("<Double-1>", lambda _event: self._edit_command())
        row = ttk.Frame(frame, padding=(0, 12, 0, 12))
        row.pack(fill="x")
        ttk.Button(row, text="Add command", command=lambda: self._edit_command(new=True)).pack(side="left", padx=(0, 8))
        ttk.Button(row, text="Edit selected", command=self._edit_command).pack(side="left", padx=(0, 8))
        ttk.Button(row, text="Remove selected", command=self._remove_command).pack(side="left")
        ttk.Label(frame, text="Editing or importing a profile disarms controls. Imported profiles use the same safety limits as the editor.", style="Muted.TLabel", wraplength=990).pack(anchor="w")

    def _build_safety(self) -> None:
        frame = self.safety_tab
        frame.columnconfigure(1, weight=1)
        ttk.Label(frame, text="Participation and input limits", style="Heading.TLabel").grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 10))
        self.profile_name = tk.StringVar()
        self.audience_var = tk.StringVar()
        self.allowed_var = tk.StringVar()
        self.blocked_var = tk.StringVar()
        self.limit_vars: dict[str, tk.StringVar] = {}
        self._field(frame, 1, "Profile name", self.profile_name)
        ttk.Label(frame, text="Who can control").grid(row=2, column=0, sticky="w", padx=(0, 16))
        ttk.Combobox(frame, textvariable=self.audience_var, state="readonly", values=tuple(AUDIENCES)).grid(row=2, column=1, sticky="ew", pady=5)
        ttk.Label(frame, text="Moderators + broadcaster by default", style="Muted.TLabel").grid(row=2, column=2, sticky="w", padx=(16, 0))
        for row, (field, label, hint) in enumerate((
            ("global_cooldown_ms", "Between any commands (ms)", "100-60,000"),
            ("viewer_cooldown_ms", "Per viewer cooldown (ms)", "250-60,000"),
            ("max_actions_per_minute", "Maximum actions / minute", "1-300"),
            ("queue_size", "Maximum queued actions", "1-8"),
            ("queue_ttl_ms", "Discard queued input after (ms)", "100-750"),
        ), start=3):
            var = tk.StringVar()
            self.limit_vars[field] = var
            self._field(frame, row, label, var, hint)
        self._field(frame, 8, "Only these Twitch user IDs", self.allowed_var, "Optional, comma separated numeric IDs")
        self._field(frame, 9, "Blocked Twitch user IDs", self.blocked_var, "Blocked IDs always win")
        ttk.Label(frame, text="Allow lists further restrict the selected audience. Names typed into chat cannot grant roles.", style="Muted.TLabel", wraplength=950).grid(row=10, column=0, columnspan=3, sticky="w", pady=(8, 8))
        ttk.Button(frame, text="Apply safety settings", style="Accent.TButton", command=self._apply_profile).grid(row=11, column=0, sticky="w", pady=8)
        self.safety_var = tk.StringVar(value="Checking local input safety...")
        ttk.Label(frame, textvariable=self.safety_var, style="Muted.TLabel", wraplength=960).grid(row=12, column=0, columnspan=3, sticky="w", pady=(8, 0))
        for var in (self.profile_name, self.audience_var, self.allowed_var, self.blocked_var, *self.limit_vars.values()):
            var.trace_add("write", self._profile_edited)

    def _field(self, frame: ttk.Frame, row: int, label: str, var: tk.StringVar, hint: str = "") -> None:
        ttk.Label(frame, text=label).grid(row=row, column=0, sticky="w", padx=(0, 16))
        ttk.Entry(frame, textvariable=var).grid(row=row, column=1, sticky="ew", pady=5)
        if hint:
            ttk.Label(frame, text=hint, style="Muted.TLabel").grid(row=row, column=2, sticky="w", padx=(16, 0))

    def _build_activity(self) -> None:
        ttk.Label(self.activity_tab, text="Recent decisions", style="Heading.TLabel").pack(anchor="w")
        ttk.Label(self.activity_tab, text="A bounded, in-memory log of approved commands and reasons. Raw chat, sign-in codes, and tokens are never logged.", style="Muted.TLabel", wraplength=980).pack(anchor="w", pady=(5, 12))
        frame = ttk.Frame(self.activity_tab)
        frame.pack(fill="both", expand=True)
        self.log = tk.Text(frame, bg=PANEL, fg=TEXT, insertbackground=TEXT, relief="flat", wrap="word", font=(self._mono_family, 10), height=15, state="disabled", padx=12, pady=12)
        scroll = ttk.Scrollbar(frame, command=self.log.yview)
        self.log.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.log.pack(side="left", fill="both", expand=True)
        ttk.Button(self.activity_tab, text="Clear activity", command=self._clear_log).pack(anchor="w", pady=(12, 0))

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
        self._last_ui_heartbeat = time.monotonic()
        for _ in range(60):
            try:
                kind, value = self._events.get_nowait()
            except queue.Empty:
                break
            if kind == "decision":
                self._log(f"{value.kind}: {value.reason}" + (f" · {value.command}" if value.command else ""))
            elif kind == "notice":
                self.notice_var.set(value)
                self._log(value)
            elif kind == "status":
                generation, status, detail = value
                if generation != self._generation:
                    continue
                self.connection_var.set(f"Twitch: {status}")
                self.notice_var.set(detail)
                self._log(detail)
                if status in {"connected", "disconnected", "error"}:
                    self._clear_device()
                if status in {"disconnected", "error"}:
                    self.connect_button.configure(text="Connect")
                    self.channel_entry.state(["!disabled"])
                    self.client_entry.state(["!disabled"])
            elif kind == "device":
                generation, uri, code = value
                if generation == self._generation:
                    self._show_device(uri, code)
        snapshot = self.engine.snapshot()
        mode = "PREVIEW" if snapshot.preview else "LIVE GAME INPUT"
        state_text = f"ARMING {snapshot.pending_seconds:.1f}s" if snapshot.state == "pending" else snapshot.state.upper()
        self.state_var.set(f"{state_text} · {mode}")
        self.arm_button.configure(text="Arm preview" if self.mode_var.get() == "preview" else "Arm live · 3 seconds")
        can_simulate = self.mode_var.get() == "preview" and snapshot.preview
        self.sim_button.state(["!disabled"] if can_simulate else ["disabled"])
        if not self.backend.available:
            self.safety_var.set("Preview available. Live game input requires Windows 10/11.")
        elif self.backend.safety_ready:
            self.safety_var.set("F8 emergency hotkey is registered. Foreground, process identity, modifier, and held-key checks are active.")
        else:
            self.safety_var.set("Live input unavailable: the F8 hotkey or native safety checks are not ready. Run without administrator rights and free F8 in other apps.")
        self.root.after(50, self._pump)

    def _log(self, text: str) -> None:
        # Only internal reason codes, validated command names and fixed status
        # strings reach this widget. Never interpolate untrusted chat or errors.
        self.log.configure(state="normal")
        self.log.insert("end", f"{datetime.now():%H:%M:%S}  {text[:350]}\n")
        lines = int(self.log.index("end-1c").split(".")[0])
        if lines > 201:
            self.log.delete("1.0", f"{lines - 200}.0")
        self.log.see("end")
        self.log.configure(state="disabled")

    def _clear_log(self) -> None:
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")

    def _connect(self) -> None:
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
        self.connect_button.configure(text="Disconnect")
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
        self.connection_var.set("Twitch: disconnected")
        self.connect_button.configure(text="Connect")
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
        self.notice_var.set("Mode changed. Review your commands and arm locally when ready.")

    def _ack_changed(self) -> None:
        if not self.ack_var.get():
            self.engine.disarm("acknowledgment_cleared")

    def _arm(self) -> None:
        if self._stop.is_set():
            messagebox.showerror("Safety worker stopped", "Restart the app before arming controls.", parent=self.root)
            return
        if self._dirty and not self._apply_profile():
            return
        preview = self.mode_var.get() == "preview"
        if not preview and not self.ack_var.get():
            messagebox.showinfo("Review game focus", "Pause controls before opening game chat, consoles, overlays, purchases, or sensitive menus. Check the acknowledgment before enabling live input.", parent=self.root)
            return
        if not self.engine.arm(preview=preview, delay_seconds=3.0):
            self.notice_var.set("Cannot arm. Check the connection, selected game, F8 hotkey, and safety settings.")
        elif not preview:
            self.notice_var.set("Focus your selected game now. Live input starts only if it is in focus after 3 seconds.")
        else:
            self.notice_var.set("Preview armed. Try a command below or connect Twitch. No keys are sent.")

    def _panic(self) -> None:
        self.engine.emergency_stop()
        self.notice_var.set("Emergency stop. All queued controls cleared; manual arming is required.")

    def _simulate(self) -> None:
        snapshot = self.engine.snapshot()
        if self.mode_var.get() != "preview" or not snapshot.preview:
            return
        role = self.sim_role.get()
        message = ChatMessage(
            message_id=f"preview-{uuid.uuid4()}", user_id="100000001", login="preview_viewer",
            text=self.sim_var.get(), roles=frozenset() if role == "viewer" else frozenset({role}),
            channel_id="100000002", received_at=time.monotonic(),
        )
        decision = self.engine.submit(message)
        if decision.reason in {"not_an_exact_command", "unknown_or_disabled_command"}:
            self.notice_var.set("Enter one exact enabled command from the Commands tab, with no spaces or arguments.")
        elif not decision.accepted:
            self.notice_var.set("Preview rejected: " + decision.reason.replace("_", " ") + ".")

    def _profile_edited(self, *_args: Any) -> None:
        if not self._loading:
            self._dirty = True
            self.engine.disarm("profile_editing")

    def _show_profile(self, profile: Profile) -> None:
        self._loading = True
        data = profile.to_dict()
        self.profile_name.set(data["name"])
        self.audience_var.set(data["audience"])
        for field, var in self.limit_vars.items():
            var.set(str(data[field]))
        self.allowed_var.set(", ".join(data["allowed_user_ids"]))
        self.blocked_var.set(", ".join(data["blocked_user_ids"]))
        for item in self.command_tree.get_children():
            self.command_tree.delete(item)
        for index, command in enumerate(data["commands"]):
            self.command_tree.insert("", "end", iid=str(index), values=(command["trigger"], command["key"], command["hold_ms"], command["cooldown_ms"], "Yes" if command["enabled"] else "No"))
        self._profile = profile
        self._loading = False
        self._dirty = False

    def _read_profile_fields(self) -> Profile:
        data = self._profile.to_dict()
        data["name"] = self.profile_name.get()
        data["audience"] = self.audience_var.get()
        for field, var in self.limit_vars.items():
            value = var.get().strip()
            if not value.isascii() or not value.isdecimal():
                raise ValueError("Limits must be whole numbers.")
            data[field] = int(value)
        for field, var in (("allowed_user_ids", self.allowed_var), ("blocked_user_ids", self.blocked_var)):
            raw = var.get().strip()
            data[field] = [part.strip() for part in raw.split(",")] if raw else []
        return Profile.from_dict(data)

    def _apply_profile(self) -> bool:
        self.engine.disarm("profile_apply")
        try:
            profile = self._read_profile_fields()
            self.engine.set_profile(profile)
        except (ValueError, TypeError):
            messagebox.showerror("Invalid safety settings", "Check the displayed limits, profile name, audience, and comma-separated numeric Twitch IDs. No settings were applied.", parent=self.root)
            return False
        self._show_profile(profile)
        self.notice_var.set("Safety settings applied. Controls remain disarmed.")
        return True

    def _edit_command(self, *, new: bool = False) -> None:
        self.engine.disarm("command_editing")
        if self._dirty and not self._apply_profile():
            return
        selection = self.command_tree.selection()
        if not new and not selection:
            return
        index = None if new else int(selection[0])
        data = self._profile.to_dict()
        current = {"trigger": "!action", "key": "space", "hold_ms": 100, "cooldown_ms": 1000, "enabled": True} if new else data["commands"][index]
        dialog = tk.Toplevel(self.root)
        dialog.title("Add command" if new else "Edit command")
        dialog.configure(bg=BG)
        dialog.transient(self.root)
        dialog.grab_set()
        body = ttk.Frame(dialog, padding=24)
        body.pack(fill="both", expand=True)
        trigger = tk.StringVar(value=current["trigger"])
        key = tk.StringVar(value=current["key"])
        hold = tk.StringVar(value=str(current["hold_ms"]))
        cooldown = tk.StringVar(value=str(current["cooldown_ms"]))
        enabled = tk.BooleanVar(value=current["enabled"])
        self._field(body, 0, "Exact chat command", trigger, "Example: !jump")
        ttk.Label(body, text="Game key").grid(row=1, column=0, sticky="w")
        ttk.Combobox(body, textvariable=key, values=tuple(sorted(SAFE_KEYS)), state="readonly").grid(row=1, column=1, sticky="ew", pady=5)
        self._field(body, 2, "Hold (ms)", hold, "10-450")
        self._field(body, 3, "Command cooldown (ms)", cooldown, "0-60,000")
        ttk.Checkbutton(body, text="Enabled", variable=enabled).grid(row=4, column=1, sticky="w")

        def save() -> None:
            try:
                candidate = {"trigger": trigger.get(), "key": key.get(), "hold_ms": int(hold.get()), "cooldown_ms": int(cooldown.get()), "enabled": enabled.get()}
                command = Command.from_dict(candidate)
                if index is None:
                    data["commands"].append(command.to_dict())
                else:
                    data["commands"][index] = command.to_dict()
                profile = Profile.from_dict(data)
                self.engine.set_profile(profile)
            except (ValueError, TypeError):
                messagebox.showerror("Invalid command", "Use a unique !command, an allowed game key, a 10-450 ms hold, and a 0-60,000 ms cooldown. Maximum 64 commands.", parent=dialog)
                # Rebuild next attempt from the original profile, never a failed mutation.
                data["commands"] = self._profile.to_dict()["commands"]
                return
            self._show_profile(profile)
            dialog.destroy()

        ttk.Button(body, text="Save command", style="Accent.TButton", command=save).grid(row=5, column=1, sticky="w", pady=(12, 0))

    def _remove_command(self) -> None:
        self.engine.disarm("command_removed")
        if self._dirty and not self._apply_profile():
            return
        selection = self.command_tree.selection()
        if not selection:
            return
        data = self._profile.to_dict()
        del data["commands"][int(selection[0])]
        try:
            profile = Profile.from_dict(data)
        except ValueError:
            self.notice_var.set("The updated profile could not be validated. Controls remain disarmed.")
            return
        self.engine.set_profile(profile)
        self._show_profile(profile)

    def _import_profile(self) -> None:
        self.engine.disarm("profile_import")
        path = filedialog.askopenfilename(title="Import command profile", filetypes=[("JSON profile", "*.json")], parent=self.root)
        if not path:
            return
        try:
            profile = load_profile(path)
            self.engine.set_profile(profile)
        except (OSError, ValueError, TypeError):
            messagebox.showerror("Profile rejected", "The file is not a valid, bounded command profile. Unknown fields, duplicates, unsafe keys, and out-of-range limits are not allowed.", parent=self.root)
            return
        self._show_profile(profile)
        self.notice_var.set("Profile imported. Review commands and audience before arming.")

    def _export_profile(self) -> None:
        self.engine.disarm("profile_export")
        if self._dirty and not self._apply_profile():
            return
        path = filedialog.asksaveasfilename(title="Export command profile", defaultextension=".json", initialfile="game-profile.json", filetypes=[("JSON profile", "*.json")], parent=self.root)
        if path:
            try:
                save_profile(self._profile, Path(path))
            except (OSError, ValueError):
                messagebox.showerror("Could not export", "Choose a writable location and try again.", parent=self.root)
                return
            self.notice_var.set("Profile exported. Authorization, window selection, and armed state are never exported.")

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self.engine.disarm("shutdown")
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
