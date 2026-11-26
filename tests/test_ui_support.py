"""GUI rendering and wheel regression checks; no network or native input."""

from datetime import datetime
from types import SimpleNamespace

import pytest

tk = pytest.importorskip("tkinter")
from tkinter import ttk  # noqa: E402

from twitch_chat_control.ui_support import (  # noqa: E402
    append_log_lines,
    clear_log,
    configure_if_changed,
    install_page_wheel_routing,
    set_enabled,
    set_var_if_changed,
)


class TrackedText(tk.Text):
    def __init__(self, *args, **kwargs):
        self.operations = []
        super().__init__(*args, **kwargs)

    def configure(self, *args, **kwargs):
        self.operations.append(("configure", kwargs))
        return super().configure(*args, **kwargs)

    def insert(self, *args):
        self.operations.append(("insert", args))
        return super().insert(*args)

    def delete(self, *args):
        self.operations.append(("delete", args))
        return super().delete(*args)

    def see(self, *args):
        self.operations.append(("see", args))
        return super().see(*args)


@pytest.fixture
def root():
    try:
        window = tk.Tk()
    except tk.TclError:
        pytest.skip("Tk display required; use Xvfb on Linux")
    window.geometry("520x340")
    yield window
    window.destroy()


@pytest.fixture
def log(root):
    widget = TrackedText(root, state="disabled", height=5, wrap="none")
    widget.pack(fill="both", expand=True)
    root.update()
    return widget


def test_activity_batch_uses_one_insert_and_two_state_writes(log):
    append_log_lines(log, [f"approved action {number}" for number in range(60)],
                     timestamp=datetime(2026, 1, 1, 12, 34, 56))
    assert [name for name, _ in log.operations].count("insert") == 1
    assert [name for name, _ in log.operations].count("configure") == 2
    assert len(log.get("1.0", "end-1c").splitlines()) == 60
    assert "12:34:56  approved action 59" in log.get("1.0", "end")
    assert log.cget("state") == "disabled"


def test_activity_history_and_incoming_batch_are_bounded(log):
    append_log_lines(log, [f"old event {number}" for number in range(50)])
    append_log_lines(log, (f"new event {number}" for number in range(1000)))
    lines = log.get("1.0", "end-1c").splitlines()
    assert len(lines) == 200
    assert lines[0].endswith("new event 800")
    assert lines[-1].endswith("new event 999")
    assert "old event" not in "\n".join(lines)


def test_activity_messages_remain_single_bounded_lines(log):
    append_log_lines(log, ["first\nsecond\r\nthird", "x" * 1000])
    lines = log.get("1.0", "end-1c").splitlines()
    assert lines[0].endswith("first second third")
    assert lines[1].endswith("x" * 350)
    assert len(lines[1]) == 360


def test_activity_does_not_pull_reader_away_from_history(log, root):
    append_log_lines(log, [f"event {number}" for number in range(100)])
    root.update_idletasks()
    log.yview_moveto(0.2)
    root.update_idletasks()
    before = log.index("@0,0")
    log.operations.clear()

    append_log_lines(log, ["newest event"])
    root.update_idletasks()

    assert not any(name == "see" for name, _ in log.operations)
    assert log.index("@0,0") == before
    assert log.yview()[1] < 0.999


def test_activity_follows_tail_when_already_at_bottom(log, root):
    append_log_lines(log, [f"event {number}" for number in range(50)])
    root.update_idletasks()
    log.see("end")
    root.update_idletasks()
    log.operations.clear()

    append_log_lines(log, ["newest event"])
    root.update_idletasks()

    assert any(name == "see" for name, _ in log.operations)
    assert log.yview()[1] >= 0.999


def test_empty_activity_batch_performs_no_widget_mutations(log):
    append_log_lines(log, [])
    assert log.operations == []


def test_clear_activity_preserves_read_only_state(log):
    append_log_lines(log, ["event"])
    clear_log(log)
    assert log.get("1.0", "end-1c") == ""
    assert log.cget("state") == "disabled"


def test_invalid_activity_batch_does_not_partially_write(log):
    with pytest.raises(TypeError):
        append_log_lines(log, ["valid", None])
    assert log.operations == []
    with pytest.raises(ValueError):
        append_log_lines(log, ["valid"], max_lines=0)
    assert log.operations == []


def test_unchanged_variable_avoids_write_trace(root):
    variable = tk.StringVar(root, value="Ready")
    writes = []
    variable.trace_add("write", lambda *_: writes.append(variable.get()))
    assert not set_var_if_changed(variable, "Ready")
    assert not writes
    assert set_var_if_changed(variable, "Paused")
    assert writes == ["Paused"]


def test_unchanged_widget_options_avoid_configure_call(root, monkeypatch):
    button = ttk.Button(root, text="Preview")
    writes = []
    original = button.configure
    monkeypatch.setattr(button, "configure", lambda **values: (writes.append(values), original(**values)))
    assert not configure_if_changed(button, text="Preview")
    assert not writes
    assert configure_if_changed(button, text="Start preview")
    assert writes == [{"text": "Start preview"}]


@pytest.mark.parametrize("values", [(), [], ("One two", "Three"), ["One two", "Three"], (1, 2), [1, 2]])
def test_unchanged_combobox_choices_are_normalized_without_extra_writes(root, monkeypatch, values):
    combo = ttk.Combobox(root, values=values)
    writes = []
    original = combo.configure
    monkeypatch.setattr(combo, "configure", lambda **options: (writes.append(options), original(**options)))

    assert not configure_if_changed(combo, values=values)
    assert not configure_if_changed(combo, values=tuple(values))
    assert not configure_if_changed(combo, values=list(values))
    assert writes == []


def test_combobox_choice_changes_write_once_and_do_not_hide_other_updates(root, monkeypatch):
    combo = ttk.Combobox(root, values=("Existing",), state="readonly")
    writes = []
    original = combo.configure
    monkeypatch.setattr(combo, "configure", lambda **options: (writes.append(options), original(**options)))

    assert configure_if_changed(combo, values=())
    assert not configure_if_changed(combo, values=[])
    assert configure_if_changed(combo, values=[], state="disabled")
    assert configure_if_changed(combo, values=["New choice"])
    assert not configure_if_changed(combo, values=("New choice",))
    assert writes == [{"values": ()}, {"state": "disabled"}, {"values": ["New choice"]}]


def test_enabled_helper_preserves_readonly_state_and_avoids_repeated_writes(root, monkeypatch):
    combo = ttk.Combobox(root, state="readonly")
    writes = []
    original = combo.state
    monkeypatch.setattr(combo, "state", lambda values: (writes.append(values), original(values)))
    assert not set_enabled(combo, True)
    assert set_enabled(combo, False)
    assert not set_enabled(combo, False)
    assert combo.instate(("readonly", "disabled"))
    assert set_enabled(combo, True)
    assert combo.instate(("readonly", "!disabled"))
    assert len(writes) == 2


@pytest.fixture
def page(root):
    frame = ttk.Frame(root)
    frame.pack(fill="both", expand=True)
    canvas = tk.Canvas(frame, yscrollincrement=10)
    canvas.pack(fill="both", expand=True)
    content = ttk.Frame(canvas)
    canvas.create_window((0, 0), window=content, anchor="nw")
    value = tk.StringVar(root, value="100")
    spin = ttk.Spinbox(content, from_=10, to=450, increment=10, textvariable=value)
    spin.pack()
    combo = ttk.Combobox(content, state="readonly", values=("One", "Two", "Three"))
    combo.current(1)
    combo.pack()
    tree = ttk.Treeview(content, height=3)
    tree.pack()
    for index in range(20):
        tree.insert("", "end", text=str(index))
    for _ in range(30):
        ttk.Label(content, text="Page content").pack()
    root.update()
    canvas.configure(scrollregion=canvas.bbox("all"))
    router = install_page_wheel_routing(frame, canvas, content)
    yield SimpleNamespace(root=root, frame=frame, canvas=canvas, content=content,
                          spin=spin, value=value, combo=combo, tree=tree, router=router)


@pytest.mark.parametrize("control", ["spin", "combo"])
def test_wheel_over_fields_scrolls_page_without_changing_value(page, control):
    widget = getattr(page, control)
    before = widget.get()
    widget.event_generate("<MouseWheel>", delta=-120)
    page.root.update()
    assert widget.get() == before
    assert page.canvas.yview()[0] > 0
    tags = widget.bindtags()
    assert tags.index(page.router._tag) < tags.index(widget.winfo_class())


def test_wheel_over_nested_list_does_not_also_scroll_page(page):
    before = page.canvas.yview()
    page.tree.event_generate("<MouseWheel>", delta=-120)
    page.root.update()
    assert page.canvas.yview() == before
    assert page.tree.yview()[0] > 0


def test_small_page_still_prevents_wheel_changing_fields(root):
    frame = ttk.Frame(root)
    frame.pack(fill="both", expand=True)
    canvas = tk.Canvas(frame)
    canvas.pack(fill="both", expand=True)
    content = ttk.Frame(canvas)
    canvas.create_window((0, 0), window=content, anchor="nw")
    spin = ttk.Spinbox(content, from_=0, to=100)
    spin.set("50")
    spin.pack()
    root.update()
    install_page_wheel_routing(frame, canvas, content)
    spin.event_generate("<MouseWheel>", delta=-120)
    root.update()
    assert spin.get() == "50"


def test_wheel_router_refresh_is_idempotent_and_includes_new_fields(page):
    extra = ttk.Spinbox(page.content, from_=0, to=100)
    extra.set("50")
    extra.pack()
    page.router.refresh()
    page.router.refresh()
    assert extra.bindtags().count(page.router._tag) == 1
    assert page.spin.bindtags().count(page.router._tag) == 1


def test_wheel_binding_removed_when_page_is_destroyed(page):
    tag = page.router._tag
    assert page.root.bind_class(tag, "<MouseWheel>")
    page.frame.destroy()
    assert page.root.bind_class(tag, "<MouseWheel>") == ""
