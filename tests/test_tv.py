from __future__ import annotations

import pytest

import tv


def test_public_api_is_exported_for_copyable_framework_use() -> None:
    namespace: dict[str, object] = {}
    exec("from tv import *", namespace)
    for name in [
        "App",
        "Size",
        "VBox",
        "HBox",
        "Panel",
        "Text",
        "Property",
        "PropertyGrid",
        "Column",
        "DataTable",
        "TreeView",
        "LogView",
        "Widget",
    ]:
        assert namespace[name] is getattr(tv, name)


def test_unicode_cell_width_and_alignment() -> None:
    assert tv.display_width("abc") == 3
    assert tv.display_width("表") == 2
    assert tv.clip_cells("a表b", 3) == "a表"
    assert tv.align_text("ok", 4, "right") == "  ok"


def test_painter_clips_writes_and_boxes() -> None:
    buffer = tv.ScreenBuffer(6, 3)
    painter = tv.Painter(buffer)
    painter.box(0, 0, 6, 3, "X")
    painter.write(1, 1, "abcdef", width=4)
    assert buffer.line_text(0) == "┌─ X─┐"
    assert buffer.line_text(1) == "│abcd│"


def test_screen_buffer_overwrites_wide_character_halves() -> None:
    buffer = tv.ScreenBuffer(5, 2)

    buffer.write(0, 0, "表")
    buffer.write(1, 0, "x")
    assert buffer.line_text(0) == " x   "

    buffer.write(2, 0, "表")
    buffer.write(2, 0, "y")
    assert buffer.line_text(0) == " xy  "

    buffer.write(0, 1, "a表")
    buffer.fill(2, 1, 1, 1)
    assert buffer.line_text(1) == "a    "


def test_child_painter_clips_negative_origin_to_parent() -> None:
    buffer = tv.ScreenBuffer(10, 1)
    parent = tv.Painter(buffer, x=5, y=0, width=3, height=1)
    child = parent.child(-2, 0, 4, 1)

    child.write(0, 0, "abcd")

    assert child.x == 5
    assert child.width == 2
    assert buffer.line_text(0) == "     ab   "


def test_box_title_does_not_erase_trailing_border() -> None:
    buffer = tv.ScreenBuffer(12, 3)
    tv.Painter(buffer).box(0, 0, 12, 3, "CPU")
    assert buffer.line_text(0) == "┌─ CPU ────┐"


def test_layout_fixed_and_flex_sizing() -> None:
    root = tv.VBox()
    root.add(tv.Text("top"), tv.Size.fixed(1))
    root.add(tv.Text("middle"), tv.Size.flex(1))
    root.add(tv.Text("bottom"), tv.Size.fixed(1))
    buffer = tv.ScreenBuffer(10, 5)
    root.render(tv.Painter(buffer), tv.RenderContext(10, 5))
    assert buffer.line_text(0).startswith("top")
    assert buffer.line_text(4).startswith("bottom")


def test_builder_layout_matches_explicit_object_tree() -> None:
    status = tv.Text("ready")
    table = tv.DataTable([tv.Column("Name", "name")], [{"name": "api"}])
    log = tv.LogView(["started"])
    app = tv.App()

    with app.screen("overview") as screen:  # noqa: SIM117
        with screen.vbox() as root:
            root.panel(status, tv.Size.fixed(3), title="Status")
            with root.hbox(tv.Size.flex(1)) as row:
                row.panel(table, tv.Size.flex(2), title="Devices")
            root.panel(log, tv.Size.fixed(8), title="Log")

    built = app.screens["overview"]
    assert isinstance(built, tv.VBox)
    assert len(built.children) == 3
    assert built.children[0].size == tv.Size.fixed(3)
    assert built.children[1].size == tv.Size.flex(1)
    assert built.children[2].size == tv.Size.fixed(8)
    assert isinstance(built.children[0].widget, tv.Panel)
    assert built.children[0].widget.title == "Status"
    assert built.children[0].widget.child is status
    assert isinstance(built.children[1].widget, tv.HBox)
    assert isinstance(built.children[1].widget.children[0].widget, tv.Panel)
    assert built.children[1].widget.children[0].size == tv.Size.flex(2)
    assert built.children[1].widget.children[0].widget.child is table
    assert isinstance(built.children[2].widget, tv.Panel)
    assert built.children[2].widget.child is log


def test_screen_builder_registers_root_on_successful_exit() -> None:
    app = tv.App()

    with app.screen("main") as screen:
        root = screen.set_root(tv.Text("hello"))

    assert app.screens["main"] is root
    assert app.current_screen == "main"


def test_screen_builder_requires_exactly_one_root() -> None:
    app = tv.App()

    with pytest.raises(ValueError, match="no root widget"):  # noqa: SIM117
        with app.screen("empty"):
            pass

    with pytest.raises(ValueError, match="already has a root widget"):  # noqa: SIM117
        with app.screen("double") as screen:
            screen.vbox()
            screen.hbox()

    explicit = tv.Text("explicit")
    with pytest.raises(ValueError, match="already has a root widget"):  # noqa: SIM117
        with app.screen("also-double") as screen:
            screen.set_root(explicit)
            screen.set_root(tv.Text("other"))

    assert app.screens == {}


def test_screen_builder_does_not_register_when_block_raises() -> None:
    app = tv.App()

    with pytest.raises(RuntimeError, match="boom"):  # noqa: SIM117
        with app.screen("main") as screen:
            screen.vbox()
            raise RuntimeError("boom")

    assert "main" not in app.screens


def test_layout_builder_methods_return_created_widgets() -> None:
    root = tv.VBox()
    status = tv.Text("ready")

    panel = root.panel(status, tv.Size.fixed(3), title="Status", padding=1)
    row = root.hbox(tv.Size.flex(1))
    column = row.vbox(tv.Size.flex(2))
    child = column.add_child(tv.Text("child"), tv.Size.fixed(1))

    assert isinstance(panel, tv.Panel)
    assert panel.padding == 1
    assert panel.child is status
    assert isinstance(row, tv.HBox)
    assert isinstance(column, tv.VBox)
    assert isinstance(child, tv.Text)
    assert root.children[0].widget is panel
    assert root.children[0].size == tv.Size.fixed(3)
    assert root.children[1].widget is row
    assert root.children[1].size == tv.Size.flex(1)
    assert row.children[0].widget is column
    assert row.children[0].size == tv.Size.flex(2)
    assert column.children[0].widget is child
    assert column.children[0].size == tv.Size.fixed(1)


def test_layout_context_manager_only_returns_existing_layout() -> None:
    root = tv.VBox()
    row = root.hbox(tv.Size.flex(1))

    with row as scoped:
        assert scoped is row
        assert len(root.children) == 1
        assert root.children[0].widget is row

    assert len(root.children) == 1
    assert root.children[0].widget is row


def test_panel_can_render_without_border_or_title() -> None:
    panel = tv.Panel(tv.Text("plain"), border=False)
    buffer = tv.ScreenBuffer(10, 3)

    panel.render(tv.Painter(buffer), tv.RenderContext(10, 3))

    assert buffer.line_text(0).startswith("plain")
    assert "┌" not in buffer.line_text(0)
    assert "│" not in buffer.line_text(1)


def test_borderless_panel_ignores_title() -> None:
    panel = tv.Panel(tv.Text("plain"), title="Hidden", border=False)
    buffer = tv.ScreenBuffer(12, 2)

    panel.render(tv.Painter(buffer), tv.RenderContext(12, 2))

    assert panel.title is None
    assert "Hidden" not in buffer.line_text(0)
    assert buffer.line_text(0).startswith("plain")


def test_borderless_panel_padding_still_applies() -> None:
    panel = tv.Panel(tv.Text("pad"), border=False, padding=1)
    buffer = tv.ScreenBuffer(8, 3)

    panel.render(tv.Painter(buffer), tv.RenderContext(8, 3))

    assert buffer.line_text(0) == "        "
    assert buffer.line_text(1) == " pad    "


def test_screen_switching_preserves_widget_state() -> None:
    first = tv.DataTable([tv.Column("Name", "name")], [{"name": "a"}, {"name": "b"}])
    second = tv.DataTable([tv.Column("Name", "name")], [{"name": "c"}])
    app = tv.App()
    app.add_screen("one", tv.Panel(first, title="One"))
    app.add_screen("two", tv.Panel(second, title="Two"))
    app.handle_key("down")
    app.show_screen("two")
    app.show_screen("one")
    assert first.selected_index == 1


def test_focus_traversal_skips_containers() -> None:
    table = tv.DataTable([tv.Column("Name", "name")], [{"name": "a"}])
    log = tv.LogView(["hello"])
    root = tv.VBox()
    root.add(tv.Panel(table, title="Table"))
    root.add(tv.Panel(log, title="Log"))
    app = tv.App()
    app.add_screen("main", root)
    assert app._focused_widget is table
    app.handle_key("tab")
    assert isinstance(app._focused_widget, tv.LogView)
    app.handle_key("shift+tab")
    assert isinstance(app._focused_widget, tv.DataTable)


def test_key_priority_order() -> None:
    events: list[str] = []
    table = tv.DataTable([tv.Column("Name", "name")], [{"name": "a"}, {"name": "b"}])

    def fallback(key: str) -> bool:
        events.append(f"fallback:{key}")
        return True

    app = tv.App(fallback=fallback)
    app.add_screen("main", table)
    app.bind("down", lambda: events.append("binding"))
    assert app.handle_key("down")
    assert events == ["binding"]
    assert table.selected_index == 0
    assert app.handle_key("q")
    assert not app.running


def test_screen_switch_bindings_use_alt_numbers() -> None:
    app = tv.App()
    app.add_screen("overview", tv.Text("overview"))
    app.add_screen("health", tv.Text("health"))
    app.bind("alt+1", lambda: app.show_screen("overview"))
    app.bind("alt+2", lambda: app.show_screen("health"))
    app.handle_key("alt+2")
    assert app.current_screen == "health"
    app.handle_key("alt+1")
    assert app.current_screen == "overview"


def test_csi_key_sequence_parsing() -> None:
    assert tv._parse_csi_sequence("A") == "up"
    assert tv._parse_csi_sequence("B") == "down"
    assert tv._parse_csi_sequence("Z") == "shift+tab"
    assert tv._parse_csi_sequence("1;3A") == "alt+up"


def test_windows_key_name_preserves_alt_modifier() -> None:
    # Values mirror Windows KEY_EVENT_RECORD constants.
    assert tv._windows_key_name("1", ord("1"), 0x0002) == "alt+1"
    assert tv._windows_key_name("Q", ord("Q"), 0) == "q"
    assert tv._windows_key_name("", 0x09, 0x0010) == "shift+tab"
    assert tv._windows_key_name("", 0x28, 0) == "down"


def test_data_table_selection_and_scrolling() -> None:
    rows = [{"name": f"row-{index}"} for index in range(5)]
    table = tv.DataTable([tv.Column("Name", "name")], rows)
    for _ in range(4):
        table.handle_key("down")
    buffer = tv.ScreenBuffer(10, 3)
    table.render(tv.Painter(buffer), tv.RenderContext(10, 3, True, table))
    assert table.selected_index == 4
    assert table.scroll_offset == 3
    assert table.selected_item == rows[4]


def test_selected_table_style_resets_before_newline() -> None:
    table = tv.DataTable([tv.Column("Name", "name")], [{"name": "alpha"}])
    row = tv.HBox()
    row.add(tv.Panel(table, title="Table"), tv.Size.flex(1))
    row.add(tv.Panel(tv.PropertyGrid(), title="Details"), tv.Size.flex(1))
    buffer = tv.ScreenBuffer(40, 4)
    row.render(tv.Painter(buffer), tv.RenderContext(40, 4, True, table))
    ansi = buffer.render_ansi()
    assert f"{tv.CSI}0;30;47m\r\n" not in ansi
    assert f"{tv.CSI}0;37m\r\n" in ansi


def test_style_switches_reset_previous_attributes() -> None:
    buffer = tv.ScreenBuffer(2, 1)
    buffer.write(0, 0, "a", "selected")
    buffer.write(1, 0, "b", "border")
    ansi = buffer.render_ansi()
    assert f"{tv.CSI}7m" not in ansi
    assert f"{tv.CSI}0;30;47m" in ansi
    assert f"{tv.CSI}0;90m" in ansi


def test_render_ansi_ends_with_true_reset() -> None:
    buffer = tv.ScreenBuffer(3, 1)
    buffer.write(0, 0, "a", "normal")
    buffer.write(1, 0, "b", "border")
    buffer.write(2, 0, "c", "error")

    ansi = buffer.render_ansi()

    assert ansi.endswith(f"{tv.CSI}0m")


def test_tree_view_expansion_and_navigation() -> None:
    root = {
        "id": "root",
        "label": "root",
        "children": [{"id": "child", "label": "child"}],
    }
    tree = tv.TreeView(
        [root],
        id=lambda node: node["id"],
        label=lambda node: node["label"],
        children=lambda node: node.get("children", []),
    )
    assert tree.selected_node is root
    tree.handle_key("right")
    tree.handle_key("down")
    assert tree.selected_node["id"] == "child"
    tree.handle_key("up")
    tree.handle_key("left")
    assert "root" not in tree.expanded_ids


def test_log_view_follow_and_scrollback() -> None:
    logs = ["one", "two", "three"]
    view = tv.LogView(logs)
    buffer = tv.ScreenBuffer(10, 2)
    view.render(tv.Painter(buffer), tv.RenderContext(10, 2, True, view))
    assert view.scroll_offset == 1
    view.handle_key("up")
    logs.append("four")
    view.render(tv.Painter(buffer), tv.RenderContext(10, 2, True, view))
    assert not view.follow
    assert view.scroll_offset == 0
    view.handle_key("end")
    view.render(tv.Painter(buffer), tv.RenderContext(10, 2, True, view))
    assert view.follow
    assert view.scroll_offset == 2


def test_log_view_resumes_follow_when_scrolled_to_visible_bottom() -> None:
    logs = [f"line-{index}" for index in range(6)]
    view = tv.LogView(logs)
    buffer = tv.ScreenBuffer(10, 3)
    view.render(tv.Painter(buffer), tv.RenderContext(10, 3, True, view))
    assert view.scroll_offset == 3

    view.handle_key("up")
    assert not view.follow
    assert view.scroll_offset == 2

    view.handle_key("down")
    assert view.follow
    assert view.scroll_offset == 3
