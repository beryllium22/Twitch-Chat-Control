"""GUI command configuration never reaches a native input backend."""

from types import SimpleNamespace

import pytest

tk = pytest.importorskip("tkinter")

from twitch_chat_control import control_editor as module  # noqa: E402
from twitch_chat_control.models import Command, Profile  # noqa: E402


@pytest.fixture
def editor(monkeypatch):
    try:
        root = tk.Tk()
    except tk.TclError:
        pytest.skip("Tk display required; run under Xvfb on Linux")
    root.geometry("950x520")
    state = SimpleNamespace(changes=[], edits=0, previews=[], panics=0, notices=[], accept=True)

    def change(commands):
        state.changes.append(commands)
        return state.accept

    def edit():
        state.edits += 1

    def preview(command):
        state.previews.append(command)
        return "Dry preview accepted; no key was sent."

    def panic():
        state.panics += 1

    widget = module.CommandEditor(root, on_change=change, on_edit=edit, on_preview=preview,
                                  on_panic=panic, on_notice=state.notices.append)
    widget.pack(fill="both", expand=True)
    widget.set_commands(Profile.default().commands)
    monkeypatch.setattr(module.messagebox, "askyesnocancel", lambda *args, **kwargs: None)
    monkeypatch.setattr(module.messagebox, "askyesno", lambda *args, **kwargs: True)
    root.update()
    try:
        yield SimpleNamespace(root=root, widget=widget, state=state)
    finally:
        root.destroy()


def focus_capture(editor):
    editor.root.focus_force()
    editor.widget.start_capture()
    editor.root.update_idletasks()
    editor.widget.capture_entry.focus_force()
    editor.root.update()
    assert editor.widget.capture_active


def key(editor, keysym, state=0, widget=None):
    return editor.widget._capture_keypress(SimpleNamespace(
        keysym=keysym, state=state, widget=widget or editor.widget.capture_entry,
    ))


def test_mapping_edits_pause_immediately_and_apply_one_validated_tuple(editor):
    widget, state = editor.widget, editor.state
    original = widget.commands
    assert not widget.dirty
    assert state.edits == 0
    widget.trigger_var.set("!walk")
    widget.key_var.set("Arrow Up")
    widget.hold_var.set("200")
    widget.cooldown_var.set("800")
    assert state.edits >= 4
    assert widget.dirty
    assert widget.commands == original
    assert not state.changes
    assert widget.apply_pending()
    assert widget.commands[0] == Command("!walk", "up", 200, 800)
    assert widget.commands[1:] == original[1:]
    assert len(state.changes) == 1
    assert not widget.dirty
    assert widget.selected_trigger == "!walk"


@pytest.mark.parametrize("field,value,reason", [
    ("trigger_var", "!jump", "already assigned"),
    ("trigger_var", "!forward 100", "Chat command"),
    ("trigger_var", "!FORWARD", "Chat command"),
    ("key_var", "Ctrl+R", "Game key"),
    ("hold_var", "451", "Hold"),
    ("hold_var", "1e2", "Hold"),
    ("cooldown_var", "-1", "Cooldown"),
    ("cooldown_var", "60001", "Cooldown"),
])
def test_invalid_draft_does_not_replace_active_mapping(editor, field, value, reason):
    original = editor.widget.commands
    getattr(editor.widget, field).set(value)
    assert not editor.widget.apply_pending()
    assert editor.widget.commands == original
    assert editor.widget.dirty
    assert reason in editor.widget.error_var.get()
    assert not editor.state.changes


def test_parent_rejection_keeps_draft_for_correction(editor):
    widget, state = editor.widget, editor.state
    original = widget.commands
    state.accept = False
    widget.key_var.set("R")
    assert not widget.apply_pending()
    assert widget.commands == original
    assert widget.key_var.get() == "R"
    assert widget.dirty
    state.accept = True
    assert widget.apply_pending()
    assert widget.commands[0].key == "r"


def test_parent_exception_does_not_expose_details_or_lose_draft(editor):
    def failing_change(_commands):
        raise RuntimeError("Synthetic private exception detail")

    widget = editor.widget
    widget._on_change = failing_change
    widget.hold_var.set("125")
    assert not widget.apply_pending()
    assert widget.dirty
    assert "Synthetic" not in widget.error_var.get()
    assert widget.commands[0].hold_ms == 150


def test_cancelled_selection_preserves_draft_and_original_row(editor):
    widget = editor.widget
    original = widget.selected_trigger
    widget.hold_var.set("225")
    widget.tree.selection_set("!jump")
    editor.root.update()
    assert widget.selected_trigger == original
    assert widget.tree.selection() == (original,)
    assert widget.hold_var.get() == "225"
    assert widget.dirty


def test_discarded_selection_moves_to_requested_mapping(editor, monkeypatch):
    widget = editor.widget
    monkeypatch.setattr(module.messagebox, "askyesnocancel", lambda *args, **kwargs: False)
    widget.hold_var.set("225")
    widget.tree.selection_set("!jump")
    editor.root.update()
    assert widget.selected_trigger == "!jump"
    assert widget.hold_var.get() == "100"
    assert not widget.dirty
    assert not editor.state.changes


def test_filter_does_not_discard_or_retarget_a_draft(editor):
    widget = editor.widget
    widget.hold_var.set("225")
    widget.filter_var.set("jump")
    editor.root.update()
    assert widget.tree.get_children() == ("!jump",)
    assert widget.selected_trigger == "!forward"
    assert widget.dirty
    assert widget.apply_pending()
    assert widget.commands[0].hold_ms == 225
    assert widget.commands[-1].hold_ms == 100


def test_duplicate_long_command_is_unique_and_remains_a_draft_until_apply(editor):
    widget = editor.widget
    command = Command("!abcdefghijklmnopqrstuvwx", "w", 200, 700)
    widget.set_commands((command,))
    widget.duplicate_command()
    assert widget.dirty
    assert widget.commands == (command,)
    first_name = widget.trigger_var.get()
    assert first_name != command.trigger
    assert len(first_name) <= 25
    assert widget.apply_pending()
    widget.duplicate_command()
    second_name = widget.trigger_var.get()
    assert second_name not in {first_name, command.trigger}
    assert widget.apply_pending()
    assert len(widget.commands) == 3
    assert all(item.key == "w" and item.hold_ms == 200 for item in widget.commands)


def test_empty_profile_can_be_created_then_removed_entirely(editor):
    widget = editor.widget
    widget.set_commands(())
    assert "disabled" in widget.trigger_entry.state()
    widget.new_command()
    assert widget.trigger_var.get() == "!action"
    assert widget.dirty
    assert not widget.commands
    assert widget.apply_pending()
    assert len(widget.commands) == 1
    widget.remove_command()
    assert not widget.commands
    assert not widget.dirty
    assert "disabled" in widget.preview_button.state()


def test_toggle_applies_only_selected_enabled_flag(editor):
    widget = editor.widget
    original = widget.commands
    widget.toggle_command()
    assert widget.commands[0] == Command("!forward", "w", 150, 300, False)
    assert widget.commands[1:] == original[1:]
    assert editor.state.edits


def test_command_limit_rejects_creation_without_discarding_existing_data(editor):
    widget = editor.widget
    commands = tuple(Command(f"!action{index}", "w") for index in range(64))
    widget.set_commands(commands)
    widget.new_command()
    assert widget.commands == commands
    assert not widget.dirty
    assert "64" in widget.error_var.get()


@pytest.mark.parametrize("keysym,state,expected", [
    ("w", 0, "w"), ("W", 2, "w"), ("Up", 0, "up"), ("space", 0, "space"),
    ("9", 0, "9"), ("W", 1, None), ("r", 4, None), ("w", 8, None),
    ("w", 0x20000, None), ("w", 64, None), ("Control_L", 0, None),
    ("Shift_R", 0, None), ("Return", 0, None), ("Tab", 0, None),
    ("F8", 0, None), ("Escape", 0, None), ("semicolon", 0, None),
])
def test_capture_only_recognizes_unmodified_approved_keys(keysym, state, expected):
    assert module.captured_key(keysym, state) == expected


def test_capture_blocks_modified_keys_and_commits_only_safe_selection(editor):
    widget = editor.widget
    original = widget.key_var.get()
    focus_capture(editor)
    assert key(editor, "R", 1) == "break"
    assert widget.capture_active
    assert widget.key_var.get() == original
    assert key(editor, "r", 0) == "break"
    assert widget.key_var.get() == "R"
    assert not widget.capture_active
    assert widget.dirty
    assert not editor.state.changes
    assert editor.state.edits


def test_f8_capture_preserves_panic_and_escape_only_cancels(editor):
    widget = editor.widget
    original = widget.key_var.get()
    focus_capture(editor)
    assert key(editor, "F8", 4) == "break"
    assert editor.state.panics == 1
    assert not widget.capture_active
    assert widget.key_var.get() == original
    focus_capture(editor)
    assert key(editor, "Escape") == "break"
    assert not widget.capture_active
    assert editor.state.panics == 1


def test_capture_focus_loss_or_wrong_widget_cannot_change_binding(editor):
    widget = editor.widget
    original = widget.key_var.get()
    focus_capture(editor)
    widget.trigger_entry.focus_force()
    editor.root.update()
    assert not widget.capture_active
    assert key(editor, "z") is None
    assert widget.key_var.get() == original
    focus_capture(editor)
    assert key(editor, "z", widget=widget.trigger_entry) == "break"
    assert not widget.capture_active
    assert widget.key_var.get() == original


def test_profile_replacement_cancels_capture_and_draft(editor):
    widget = editor.widget
    focus_capture(editor)
    replacement = (Command("!go", "up"),)
    widget.set_commands(replacement)
    assert not widget.capture_active
    assert widget.commands == replacement
    assert not widget.dirty


def test_preview_uses_only_the_parent_dry_run_callback(editor):
    widget = editor.widget
    widget.preview_command()
    assert editor.state.previews == [widget.commands[0]]
    assert editor.state.edits
    assert not editor.state.changes
    assert "no key" in widget.preview_var.get()
    widget.hold_var.set("5000")
    widget.preview_command()
    assert len(editor.state.previews) == 1


def test_empty_and_no_match_states_explain_next_step_without_losing_draft(editor):
    widget = editor.widget
    widget.hold_var.set("225")
    widget.filter_var.set("nothing-matches")
    editor.root.update()
    assert not widget.tree.get_children()
    assert "No matches" in widget.empty_var.get()
    assert widget.empty_table.winfo_ismapped()
    assert widget.selected_trigger == "!forward"
    assert widget.dirty
    widget.clear_search_button.invoke()
    editor.root.update()
    assert widget.tree.selection() == ("!forward",)
    assert widget.hold_var.get() == "225"
    assert not widget.empty_table.winfo_ismapped()
    assert "disabled" in widget.clear_search_button.state()

    widget.set_commands(())
    editor.root.update()
    assert "No commands yet" in widget.empty_var.get()
    assert widget.empty_detail.winfo_ismapped()
    assert not widget.form.winfo_ismapped()
    widget.new_button.invoke()
    editor.root.update()
    assert widget.form.winfo_ismapped()
    assert not widget.empty_detail.winfo_ismapped()
    assert widget.dirty


def test_invalid_apply_focuses_problem_and_correction_clears_field_state(editor):
    widget = editor.widget
    editor.root.focus_force()
    widget.hold_var.set("900")
    widget.trigger_entry.focus_force()
    assert not widget.error_var.get()
    assert "invalid" not in widget.hold_entry.state()
    assert not widget.apply_pending()
    editor.root.update()
    assert editor.root.focus_get() is widget.hold_entry
    assert "invalid" in widget.hold_entry.state()
    assert widget.error_label.winfo_ismapped()
    widget.hold_var.set("200")
    editor.root.update()
    assert "invalid" not in widget.hold_entry.state()
    assert not widget.error_label.winfo_ismapped()
    assert widget.apply_pending()
    assert "disabled" in widget.apply_button.state()
    assert "disabled" in widget.revert_button.state()
    assert "Applied" in widget.draft_var.get()


@pytest.mark.parametrize("field,variable,incomplete,corrected,reason", [
    ("trigger_entry", "trigger_var", "!", "!walk", "Chat command"),
    ("hold_entry", "hold_var", "", "200", "Hold"),
])
def test_initial_typing_waits_for_blur_before_feedback_then_corrections_clear_it(
    editor, field, variable, incomplete, corrected, reason,
):
    widget = editor.widget
    original = widget.commands
    entry, value = getattr(widget, field), getattr(widget, variable)
    editor.root.focus_force()
    entry.focus_force()
    editor.root.update()
    value.set(incomplete)
    editor.root.update()
    assert widget.dirty
    assert editor.state.edits
    assert not widget.error_var.get()
    assert "invalid" not in entry.state()
    assert not widget.error_label.winfo_ismapped()

    widget.search_entry.focus_force()
    editor.root.update()
    assert reason in widget.error_var.get()
    assert "invalid" in entry.state()
    assert widget.error_label.winfo_ismapped()

    entry.focus_force()
    value.set(corrected)
    editor.root.update()
    assert not widget.error_var.get()
    assert "invalid" not in entry.state()
    assert not widget.error_label.winfo_ismapped()
    assert widget.commands == original
    assert not editor.state.changes


def test_keyboard_navigation_opens_selected_mapping_and_applies_local_draft(editor):
    widget = editor.widget
    editor.root.focus_force()
    widget.tree.focus_force()
    editor.root.update()
    widget.tree.event_generate("<Return>")
    editor.root.update()
    assert editor.root.focus_get() is widget.trigger_entry
    widget.trigger_var.set("!walk")
    widget.trigger_entry.event_generate("<Return>")
    editor.root.update()
    assert widget.selected_trigger == "!walk"
    assert widget.commands[0].trigger == "!walk"
    assert len(editor.state.changes) == 1
    assert not editor.state.previews
    assert not widget.dirty


def test_visible_capture_cancel_remains_canceled_after_focus_out(editor):
    widget = editor.widget
    original = widget.key_var.get()
    focus_capture(editor)
    assert widget.cancel_capture_button.winfo_ismapped()
    # A mouse click can move focus before the button command runs. Cancellation
    # must not restart capture when that command follows the FocusOut callback.
    widget.cancel_capture_button.focus_force()
    editor.root.update()
    widget.cancel_capture_button.invoke()
    editor.root.update()
    assert not widget.capture_active
    assert not widget.capture_row.winfo_ismapped()
    assert widget.key_var.get() == original
    assert not widget.dirty
    assert not editor.state.changes


def test_unchanged_rows_are_not_recreated_during_profile_refresh_or_search(editor, monkeypatch):
    widget = editor.widget
    inserts, deletes = [], []
    original_insert, original_delete = widget.tree.insert, widget.tree.delete

    def insert(*args, **kwargs):
        inserts.append(args)
        return original_insert(*args, **kwargs)

    def delete(*args):
        deletes.append(args)
        return original_delete(*args)

    monkeypatch.setattr(widget.tree, "insert", insert)
    monkeypatch.setattr(widget.tree, "delete", delete)
    widget.set_commands(widget.commands)
    widget.filter_var.set("!")  # Every existing row still matches.
    editor.root.update()
    assert not inserts
    assert not deletes
    assert widget.tree.selection() == ("!forward",)


def test_pause_failure_keeps_draft_visible_and_blocks_apply(editor):
    widget = editor.widget
    original = widget.commands

    def fail_pause():
        raise RuntimeError("Synthetic internal detail")

    widget._on_edit = fail_pause
    widget.hold_var.set("200")
    assert widget.dirty
    assert "Restart the app" in widget.error_var.get()
    assert "Synthetic" not in widget.error_var.get()
    assert "disabled" in widget.apply_button.state()
    assert not widget.apply_pending()
    assert widget.commands == original
    assert not editor.state.changes


def test_cards_reflow_without_losing_draft_selection_or_capture(editor):
    widget = editor.widget
    required = widget._left_card.winfo_reqwidth() + widget._right_card.winfo_reqwidth() + 10
    narrow = max(widget._left_card.winfo_reqwidth(), widget._right_card.winfo_reqwidth()) + 20
    editor.root.geometry(f"{required + 80}x1000")
    # Keep the top-level window focused while changing the editor's available
    # space. Some headless window managers genuinely drop application focus on
    # a top-level resize; that must still cancel capture for safety.
    widget.pack_configure(fill="y", expand=False, anchor="nw")
    widget.grid_propagate(False)
    widget.configure(width=required + 80, height=1000)
    editor.root.update()
    widget.hold_var.set("225")
    focus_capture(editor)
    assert widget._layout_mode == "columns"
    widget.configure(width=narrow)
    editor.root.update()
    assert widget._layout_mode == "stacked"
    assert widget._left_card.grid_info()["row"] == 0
    assert widget._right_card.grid_info()["row"] == 1
    assert widget.capture_active
    assert editor.root.focus_get() is widget.capture_entry
    assert widget.dirty
    assert widget.hold_var.get() == "225"
    assert widget.selected_trigger == "!forward"
    assert widget.tree.selection() == ("!forward",)

    widget.configure(width=required + 80)
    editor.root.update()
    assert widget._layout_mode == "columns"
    assert widget.capture_active
    assert editor.root.focus_get() is widget.capture_entry
    assert widget._layout_job is None


def test_larger_fonts_leave_command_button_and_table_labels_readable(editor):
    widget = editor.widget
    style = module.ttk.Style(editor.root)
    family = module.tkfont.nametofont("TkDefaultFont").actual("family")
    style.configure("TButton", font=(family, 16))
    style.configure("Treeview", font=(family, 16))
    style.configure("Treeview.Heading", font=(family, 16, "bold"))
    widget.event_generate("<<ThemeChanged>>")
    editor.root.update()
    body = module.tkfont.Font(root=editor.root, font=style.lookup("Treeview", "font"))
    heading = module.tkfont.Font(root=editor.root, font=style.lookup("Treeview.Heading", "font"))
    button = module.tkfont.Font(root=editor.root, font=style.lookup("TButton", "font"))

    assert widget.new_button.winfo_width() >= button.measure("New command") + 8
    assert widget.tree.column("key", "minwidth") >= max(heading.measure("Game key"), body.measure("Arrow Right")) + 8
    assert widget.tree.column("enabled", "minwidth") >= max(heading.measure("Status"), body.measure("Disabled")) + 8
    assert widget.tree.column("trigger", "minwidth") >= heading.measure("Chat command") + 8
    required = widget._left_card.winfo_reqwidth() + widget._right_card.winfo_reqwidth() + 10
    if required > widget.winfo_width():
        assert widget._layout_mode == "stacked"
