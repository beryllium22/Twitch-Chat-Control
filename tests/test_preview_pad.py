"""Illustrative Preview feedback stays local, bounded, and readable."""

from types import SimpleNamespace

import pytest

tk = pytest.importorskip("tkinter")

from twitch_chat_control.models import MOUSE_BUTTONS, Command  # noqa: E402
from twitch_chat_control.preview_pad import PreviewPad  # noqa: E402


@pytest.fixture
def pad():
    try:
        root = tk.Tk()
    except tk.TclError:
        pytest.skip("Tk display required; use Xvfb on Linux")
    root.geometry("520x400")
    widget = PreviewPad(root)
    widget.pack(fill="both", expand=True, padx=10, pady=10)
    root.update()
    try:
        yield SimpleNamespace(root=root, widget=widget)
    finally:
        root.destroy()


def test_initial_pad_is_only_an_illustration_and_local_pointer_events_do_not_run_actions(pad):
    widget = pad.widget
    assert widget.virtual_position == (200, 100)
    assert widget.trail == ((200, 100),)
    assert "Preview only" in widget.preview_label.cget("text")
    assert "sensitivity can differ" in widget.scale_label.cget("text")
    assert "center" in widget.position_var.get()
    widget.canvas.event_generate("<Motion>", x=90, y=60)
    widget.canvas.event_generate("<Button-1>", x=90, y=60)
    widget.canvas.event_generate("<ButtonRelease-1>", x=90, y=60)
    pad.root.update()
    assert widget.virtual_position == (200, 100)
    assert widget.trail == ((200, 100),)
    assert "Try a command" in widget.summary_var.get()


@pytest.mark.parametrize("dx,dy,position,words", [
    (-25, 0, (175, 100), ("25 left",)),
    (25, 0, (225, 100), ("25 right",)),
    (0, -10, (200, 90), ("10 up",)),
    (0, 10, (200, 110), ("10 down",)),
    (100, -100, (300, 0), ("100 right", "100 up")),
])
def test_signed_and_diagonal_movement_has_text_equivalent(pad, dx, dy, position, words):
    command = Command("!look", action_type="mouse_move", dx=dx, dy=dy)
    pad.widget.show_command(command)
    pad.root.update()
    assert pad.widget.virtual_position == position
    assert pad.widget.trail[-1] == position
    assert all(word in pad.widget.position_var.get() for word in words)
    assert command.action_summary in pad.widget.summary_var.get()
    assert pad.widget.canvas.find_withtag("pointer")


def test_virtual_edge_is_explained_without_claiming_game_confinement(pad):
    command = Command("!look", action_type="mouse_move", dx=100, dy=100)
    for _ in range(4):
        pad.widget.show_command(command)
    pad.root.update()
    assert pad.widget.virtual_position == (400, 200)
    assert "Practice edge reached" in pad.widget.summary_var.get()
    assert "reset the pad" in pad.widget.summary_var.get()
    assert command.action_summary in pad.widget.summary_var.get()
    assert "200 right" in pad.widget.position_var.get()
    assert "100 down" in pad.widget.position_var.get()
    assert len(pad.widget.trail) == 3  # repeated attempts at the edge do not grow a trail


def test_recent_trail_and_canvas_items_remain_bounded_under_many_commands(pad):
    commands = (Command("!right", action_type="mouse_move", dx=25),
                Command("!left", action_type="mouse_move", dx=-25))
    for number in range(100):
        pad.widget.show_command(commands[number % 2])
    pad.root.update()
    assert pad.widget.virtual_position == (200, 100)
    assert len(pad.widget.trail) == PreviewPad.MAX_TRAIL_POINTS
    assert isinstance(pad.widget.trail, tuple)
    assert len(pad.widget.canvas.find_all()) < 20
    assert len(pad.widget.canvas.coords("trail")) == PreviewPad.MAX_TRAIL_POINTS * 2


@pytest.mark.parametrize("button", MOUSE_BUTTONS)
def test_all_buttons_show_last_action_and_duration_without_latched_hold(pad, button):
    command = Command("!button", action_type="mouse_button", button=button, hold_ms=180)
    pad.widget.show_command(command)
    pad.root.update()
    assert command.action_summary in pad.widget.summary_var.get()
    assert "Last preview" in pad.widget.summary_var.get()
    assert pad.widget.canvas.itemcget("action-badge", "text") == command.action_label
    assert pad.widget.canvas.find_withtag("button-mark")
    assert pad.widget.virtual_position == (200, 100)
    assert pad.widget.trail == ((200, 100),)
    assert pad.widget._draw_job is None  # no ongoing timer pretends to hold a real button


@pytest.mark.parametrize("steps", [-3, -1, 1, 3])
def test_wheel_direction_and_count_are_readable_and_do_not_move_the_pointer(pad, steps):
    command = Command("!scroll", action_type="mouse_wheel", wheel=steps)
    pad.widget.show_command(command)
    pad.root.update()
    assert command.action_summary in pad.widget.summary_var.get()
    assert pad.widget.canvas.itemcget("action-badge", "text") == command.action_label
    line = pad.widget.canvas.coords("wheel-mark")
    assert (line[3] < line[1]) is (steps > 0)
    assert pad.widget.virtual_position == (200, 100)


@pytest.mark.parametrize("key", ["w", "space", "left", "0"])
def test_keyboard_preview_uses_a_badge_and_textual_duration(pad, key):
    command = Command("!key", key, hold_ms=100)
    pad.widget.show_command(command)
    pad.root.update()
    assert command.action_summary in pad.widget.summary_var.get()
    assert pad.widget.canvas.itemcget("action-badge", "text") == "Key: " + command.action_label
    assert pad.widget.canvas.find_withtag("key-mark")
    assert not pad.widget.canvas.find_withtag("button-mark")
    assert pad.widget.virtual_position == (200, 100)


def test_rejected_command_cannot_move_pointer_or_append_to_trail(pad):
    pad.widget.show_command(Command("!look", action_type="mouse_move", dx=-20, dy=15))
    before = pad.widget.virtual_position, pad.widget.trail
    pad.widget.show_rejection("This viewer needs to wait before sending another command.")
    pad.root.update()
    assert (pad.widget.virtual_position, pad.widget.trail) == before
    assert pad.widget.summary_var.get().startswith("Not run: ")
    assert "needs to wait" in pad.widget.summary_var.get()
    assert pad.widget.canvas.itemcget("action-badge", "text") == "Not run"
    assert not pad.widget.canvas.find_withtag("button-mark")
    assert not pad.widget.canvas.find_withtag("wheel-mark")


def test_feedback_is_bounded_single_line_and_empty_feedback_has_a_useful_default(pad):
    pad.widget.show_rejection("Wait\nfor\r\nnext command.\u202E\x00" + "x" * 1000)
    result = pad.widget.summary_var.get()
    assert len(result) <= PreviewPad.MAX_REJECTION_LENGTH + len("Not run: ")
    assert "\n" not in result and "\r" not in result and "\x00" not in result and "\u202E" not in result
    pad.widget.show_rejection("")
    assert pad.widget.summary_var.get() == "Not run: This command was not accepted."


@pytest.mark.parametrize("invalid", [None, "!move", {"dx": 100}, object()])
def test_pad_refuses_unvalidated_values_without_changing_its_state(pad, invalid):
    before = pad.widget.virtual_position, pad.widget.trail, pad.widget.summary_var.get()
    with pytest.raises(TypeError):
        pad.widget.show_command(invalid)
    with pytest.raises(TypeError):
        pad.widget.show_rejection(invalid if not isinstance(invalid, str) else None)
    assert (pad.widget.virtual_position, pad.widget.trail, pad.widget.summary_var.get()) == before


def test_disabled_command_cannot_be_displayed_as_successful(pad):
    before = pad.widget.virtual_position, pad.widget.trail, pad.widget.summary_var.get()
    with pytest.raises(ValueError):
        pad.widget.show_command(Command("!look", action_type="mouse_move", dx=100, enabled=False))
    assert (pad.widget.virtual_position, pad.widget.trail, pad.widget.summary_var.get()) == before


def test_reset_button_restores_only_pad_state_and_remains_keyboard_focusable(pad):
    pad.widget.show_command(Command("!look", action_type="mouse_move", dx=100))
    pad.widget.show_rejection("Wait before trying again.")
    pad.widget.reset_button.invoke()
    pad.root.update()
    assert pad.widget.virtual_position == (200, 100)
    assert pad.widget.trail == ((200, 100),)
    assert "session limits are unchanged" in pad.widget.summary_var.get()
    assert pad.widget.position_var.get() == "Practice pointer: center."
    assert not pad.widget.reset_button.instate(("disabled",))
    pad.root.focus_force()
    pad.widget.reset_button.focus_set()
    pad.root.update()
    assert pad.root.focus_get() is pad.widget.reset_button


def test_resize_changes_drawing_scale_without_changing_virtual_state(pad):
    pad.widget.show_command(Command("!look", action_type="mouse_move", dx=-30, dy=-20))
    pad.root.update()
    position, trail = pad.widget.virtual_position, pad.widget.trail
    drawing = pad.widget.canvas.coords("pointer")
    pad.root.geometry("900x400")
    pad.root.update()
    assert (pad.widget.virtual_position, pad.widget.trail) == (position, trail)
    assert pad.widget.canvas.coords("pointer") != drawing
    assert "30 left" in pad.widget.position_var.get() and "20 up" in pad.widget.position_var.get()


def test_redundant_draws_do_not_rebuild_canvas_and_destroy_cancels_pending_work(pad, monkeypatch):
    operations = []
    original = pad.widget.canvas.delete
    monkeypatch.setattr(pad.widget.canvas, "delete", lambda *args: (operations.append(args), original(*args)))
    for _ in range(20):
        pad.widget._request_draw()
    pending = pad.widget._draw_job
    assert pending is not None
    pad.root.update()
    assert not operations
    pad.widget.show_command(Command("!look", action_type="mouse_move", dx=25))
    pending = pad.widget._draw_job
    assert pending is not None
    pad.widget.destroy()
    assert pending not in pad.root.tk.call("after", "info")
    pad.root.update()


def test_root_wide_callback_cancellation_then_child_destruction_is_safe(pad):
    pad.widget.show_command(Command("!look", action_type="mouse_move", dx=25))
    pending = pad.widget._draw_job
    assert pending is not None
    for callback in pad.root.tk.call("after", "info"):
        pad.root.after_cancel(callback)
    assert pending not in pad.root.tk.call("after", "info")

    # App shutdown/tests may cancel every root callback first. Destroying the
    # component must not try to delete an already removed child Tcl command.
    pad.widget.destroy()
    pad.root.update()
    assert pad.widget._closed
    assert pad.widget._draw_job is None


def test_compact_pad_with_enlarged_text_has_no_horizontal_overflow():
    try:
        root = tk.Tk()
    except tk.TclError:
        pytest.skip("Tk display required; use Xvfb on Linux")
    scaling = root.tk.call("tk", "scaling")
    try:
        root.tk.call("tk", "scaling", 2.0)
        root.geometry("500x650")
        widget = PreviewPad(root)
        widget.pack(fill="x", padx=8, pady=8)
        widget.show_command(Command("!diagonal", action_type="mouse_move", dx=-100, dy=100))
        root.update()
        for control in (widget.reset_button, widget.canvas, widget.preview_label,
                        widget.summary_label, widget.position_label, widget.scale_label):
            assert control.winfo_ismapped()
            assert control.winfo_rootx() >= root.winfo_rootx()
            assert control.winfo_rootx() + control.winfo_width() <= root.winfo_rootx() + root.winfo_width()
        assert widget.winfo_reqheight() <= 650
    finally:
        root.tk.call("tk", "scaling", scaling)
        root.destroy()
