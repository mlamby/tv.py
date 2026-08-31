from __future__ import annotations

import ctypes
import importlib
import sys

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
        "PathMatch",
        "Property",
        "PropertyPattern",
        "PropertyGrid",
        "Column",
        "DataTable",
        "TreeView",
        "LogView",
        "Widget",
        "enable_emoji_support",
        "iter_path_children",
        "match_paths",
        "path",
    ]:
        assert namespace[name] is getattr(tv, name)


def test_unicode_cell_width_and_alignment() -> None:
    assert tv.display_width("abc") == 3
    assert tv.display_width("表") == 2
    assert tv.clip_cells("a表b", 3) == "a表"
    assert tv.align_text("ok", 4, "right") == "  ok"


def test_default_mode_keeps_dependency_free_unicode_measurement() -> None:
    tv.enable_emoji_support(False)

    assert tv.display_width("abc") == 3
    assert tv.display_width("表") == 2
    assert tv.clip_cells("a表b", 3) == "a表"
    assert tv.align_text("ok", 4, "right") == "  ok"


def test_path_accessor_reads_nested_mapping_object_and_index_values() -> None:
    class Status:
        state = "ok"

    source = {
        "header": {"message_id": "nav-001"},
        "sensors": [{"quality": {"hdop": 0.9}}],
        "status": Status(),
    }

    assert tv.path("header.message_id")(source) == "nav-001"
    assert tv.path("sensors[0].quality.hdop")(source) == 0.9
    assert tv.path("status.state")(source) == "ok"


def test_path_accessor_uses_default_and_transform() -> None:
    source = {"navigation": {"speed_ms": 10.0}, "status": {"health": ""}}

    knots = tv.path(
        "navigation.speed_ms",
        default=0.0,
        transform=lambda value: float(value) * 1.943844,
    )

    assert knots(source) == pytest.approx(19.43844)
    assert tv.path("missing.field", default="unknown")(source) == "unknown"
    assert tv.path("navigation.speed_ms[0]", default="bad")(source) == "bad"
    assert tv.path("status.health", default="unknown")(source) == ""


def test_match_paths_returns_leaf_matches_by_default() -> None:
    source = {
        "payload": {
            "health": {
                "api_status": "ok",
                "fault_count": 0,
            },
            "sensors": [
                {"state": "ok"},
                {"state": "warning"},
            ],
        }
    }

    matches = tv.match_paths(source, "payload.**")

    rendered = [
        (match.path, match.name, match.type_name, match.value) for match in matches
    ]

    assert rendered == [
        ("payload.health.api_status", "api_status", "str", "ok"),
        ("payload.health.fault_count", "fault_count", "int", 0),
        ("payload.sensors[0].state", "state", "str", "ok"),
        ("payload.sensors[1].state", "state", "str", "warning"),
    ]


def test_iter_path_children_returns_immediate_children() -> None:
    class Payload:
        status = "ok"

        def helper(self) -> str:
            return "ignored"

    payload = Payload()
    history = [{"state": "old"}]
    source = {
        "payload": payload,
        "history": history,
    }

    root_children = tv.iter_path_children(source)
    payload_children = tv.iter_path_children(source["payload"], prefix="payload")
    history_children = tv.iter_path_children(source["history"], prefix="history")

    rendered_root = [
        (child.path, child.name, child.type_name, child.value)
        for child in root_children
    ]

    assert rendered_root == [
        ("payload", "payload", "Payload", payload),
        ("history", "history", "list", history),
    ]
    assert [(child.path, child.name, child.value) for child in payload_children] == [
        ("payload.status", "status", "ok")
    ]
    assert [(child.path, child.name, child.value) for child in history_children] == [
        ("history[0]", "[0]", history[0])
    ]


def test_match_paths_can_include_intermediate_matches() -> None:
    source = {"payload": {"health": {"api_status": "ok"}}}

    matches = tv.match_paths(source, "payload.**", leaves_only=False)

    assert [match.path for match in matches] == [
        "payload",
        "payload.health",
        "payload.health.api_status",
    ]


def test_match_paths_can_preserve_source_order_and_match_exact_indexes() -> None:
    source = {
        "payload": {
            "z_status": "ok",
            "a_status": "warning",
            "history": ({"state": "old"}, {"state": "new"}),
        }
    }

    unsorted = tv.match_paths(source, "payload.*_status", sort=False)
    exact_index = tv.match_paths(source, "payload.history[1].state")

    assert [match.path for match in unsorted] == [
        "payload.z_status",
        "payload.a_status",
    ]
    assert [(match.path, match.value) for match in exact_index] == [
        ("payload.history[1].state", "new")
    ]


def test_match_paths_can_prefix_returned_paths() -> None:
    source = [{"state": "ok"}, {"state": "warning"}]

    matches = tv.match_paths(source, prefix="streams.navigation")
    root_match = tv.match_paths("online", "", prefix="status")

    assert [match.path for match in matches] == [
        "streams.navigation[0].state",
        "streams.navigation[1].state",
    ]
    assert [(match.path, match.name, match.value) for match in root_match] == [
        ("status", "", "online")
    ]


def test_match_paths_supports_object_attributes_globs_and_indexes() -> None:
    class Health:
        api_status = "ok"
        db_status = "warning"

    class Payload:
        health = Health()
        sensors = [{"state": "ok"}, {"state": "error"}]

    source = {"payload": Payload()}

    status_matches = tv.match_paths(source, "payload.health.[a]*_status")
    sensor_matches = tv.match_paths(source, "payload.sensors[*].state")

    assert [match.path for match in status_matches] == ["payload.health.api_status"]
    assert [match.value for match in sensor_matches] == ["ok", "error"]


def test_match_paths_supports_ctypes_structures_and_arrays() -> None:
    class Status(ctypes.Structure):
        _fields_ = [
            ("overall_health", ctypes.c_uint8),
            ("fault_count", ctypes.c_uint16),
        ]

    StatusArray = Status * 2

    class Payload(ctypes.Structure):
        _fields_ = [
            ("status", Status),
            ("history", StatusArray),
        ]

    source = Payload(Status(1, 2), StatusArray(Status(0, 0), Status(2, 3)))

    matches = tv.match_paths(source, "**.*_count")

    rendered = [
        (match.path, match.name, match.type_name, match.value) for match in matches
    ]

    assert rendered == [
        ("history[0].fault_count", "fault_count", "int", 0),
        ("history[1].fault_count", "fault_count", "int", 3),
        ("status.fault_count", "fault_count", "int", 2),
    ]


def test_property_grid_styles_raw_values_before_formatting() -> None:
    grid = tv.PropertyGrid(
        {"health": "warning"},
        [
            tv.Property(
                "Health",
                "health",
                formatter=lambda value: str(value).upper(),
                style=lambda value: str(value),
            )
        ],
    )
    buffer = tv.ScreenBuffer(20, 1)

    grid.render(tv.Painter(buffer), tv.RenderContext(20, 1, False, None))

    assert buffer.line_text(0).rstrip() == "Health WARNING"
    assert buffer._cells[0][7].style == "warning"


def test_property_grid_resolves_callable_source_when_rendering() -> None:
    source = {"name": "alpha"}
    grid = tv.PropertyGrid(lambda: source, [tv.Property("Name", "name")])
    buffer = tv.ScreenBuffer(20, 1)

    grid.render(tv.Painter(buffer), tv.RenderContext(20, 1, False, None))
    assert buffer.line_text(0).rstrip() == "Name alpha"

    source = {"name": "bravo"}
    buffer = tv.ScreenBuffer(20, 1)
    grid.render(tv.Painter(buffer), tv.RenderContext(20, 1, False, None))

    assert buffer.line_text(0).rstrip() == "Name bravo"


def test_property_grid_expands_direct_property_pattern_matches() -> None:
    source = {
        "payload": {
            "health": {
                "api_status": "ok",
                "db_status": "warning",
                "latency_ms": 42,
            }
        }
    }
    grid = tv.PropertyGrid(
        source,
        [tv.PropertyPattern("payload.health.*_status")],
    )
    buffer = tv.ScreenBuffer(24, 2)

    grid.render(tv.Painter(buffer), tv.RenderContext(24, 2))

    assert buffer.line_text(0).rstrip() == "api_status ok"
    assert buffer.line_text(1).rstrip() == "db_status  warning"


def test_property_grid_pattern_supports_glob_character_classes() -> None:
    source = {
        "payload": {
            "health": {
                "api_status": "ok",
                "db_status": "warning",
                "cache_status": "ok",
            }
        }
    }
    grid = tv.PropertyGrid(
        source,
        [tv.PropertyPattern("payload.health.[ad]*_status")],
    )
    buffer = tv.ScreenBuffer(24, 2)

    grid.render(tv.Painter(buffer), tv.RenderContext(24, 2))

    assert buffer.line_text(0).rstrip() == "api_status ok"
    assert buffer.line_text(1).rstrip() == "db_status  warning"


def test_property_grid_expands_recursive_and_sequence_patterns() -> None:
    source = {
        "payload": {
            "sensors": [
                {"health": {"state": "ok"}},
                {"health": {"state": "error"}},
            ],
            "subsystem": {"nested": {"db_status": "warning"}},
        }
    }
    grid = tv.PropertyGrid(
        source,
        [
            tv.PropertyPattern("payload.sensors[*].health.state"),
            tv.PropertyPattern("payload.**.*_status"),
        ],
    )
    buffer = tv.ScreenBuffer(36, 3)

    grid.render(tv.Painter(buffer), tv.RenderContext(36, 3))

    assert buffer.line_text(0).startswith("[0].health.state")
    assert buffer.line_text(0).rstrip().endswith("ok")
    assert buffer.line_text(1).startswith("[1].health.state")
    assert buffer.line_text(1).rstrip().endswith("error")
    assert buffer.line_text(2).rstrip() == "subsystem.nested.db_status warning"


def test_property_grid_pattern_labels_full_leaf_and_empty_matches() -> None:
    source = {"payload": {"health": {"api_status": "ok"}}}
    grid = tv.PropertyGrid(
        source,
        [
            tv.PropertyPattern("payload.health.*_status", label="full"),
            tv.PropertyPattern("payload.health.*_status", label="leaf"),
            tv.PropertyPattern("payload.health.missing_*"),
        ],
    )
    buffer = tv.ScreenBuffer(40, 3)

    grid.render(tv.Painter(buffer), tv.RenderContext(40, 3))

    assert buffer.line_text(0).rstrip() == "payload.health.api_status ok"
    assert buffer.line_text(1).rstrip() == "api_status                ok"
    assert buffer.line_text(2).rstrip() == ""


def test_property_grid_pattern_formatter_and_style_receive_matches() -> None:
    seen: list[tv.PathMatch] = []
    source = {"payload": {"health": {"api_status": "warning"}}}

    def record_seen(match: tv.PathMatch) -> str:
        seen.append(match)
        return str(match.value)

    grid = tv.PropertyGrid(
        source,
        [
            tv.PropertyPattern(
                "payload.health.*_status",
                formatter=lambda match: f"{match.name}:{str(match.value).upper()}",
                style=record_seen,
            )
        ],
    )
    buffer = tv.ScreenBuffer(32, 1)

    grid.render(tv.Painter(buffer), tv.RenderContext(24, 1))

    assert buffer.line_text(0).rstrip() == "api_status api_status:WARNING"
    assert buffer._cells[0][11].style == "warning"
    assert [(match.path, match.value) for match in seen] == [
        ("payload.health.api_status", "warning")
    ]


def test_property_grid_pattern_expands_public_object_attributes() -> None:
    class Health:
        api_status = "ok"

        def helper(self) -> str:
            return "ignored"

    class Payload:
        health = Health()

    source = {"payload": Payload()}
    grid = tv.PropertyGrid(source, [tv.PropertyPattern("payload.health.*_status")])
    buffer = tv.ScreenBuffer(20, 1)

    grid.render(tv.Painter(buffer), tv.RenderContext(20, 1))

    assert buffer.line_text(0).rstrip() == "api_status ok"


def test_property_grid_recursive_pattern_treats_scalars_as_leaves() -> None:
    source = {
        "payload": {
            "health": {
                "api_status": "ok",
                "count": 1,
            }
        }
    }
    grid = tv.PropertyGrid(source, [tv.PropertyPattern("payload.**.real")])
    buffer = tv.ScreenBuffer(24, 1)

    grid.render(tv.Painter(buffer), tv.RenderContext(24, 1))

    assert buffer.line_text(0).rstrip() == ""


def test_enable_emoji_support_reports_missing_optional_dependencies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tv.enable_emoji_support(False)
    real_import_module = importlib.import_module

    def missing_optional_dependency(name: str, package: object = None) -> object:
        del package
        if name == "regex":
            raise ImportError(name)
        return real_import_module(name)

    monkeypatch.setattr(importlib, "import_module", missing_optional_dependency)

    with pytest.raises(RuntimeError, match="pip install regex wcwidth") as exc_info:
        tv.enable_emoji_support()

    assert sys.executable in str(exc_info.value)
    assert "this interpreter" in str(exc_info.value)
    assert tv.display_width("表") == 2


def test_optional_emoji_support_measures_and_clips_graphemes() -> None:
    pytest.importorskip("regex")
    pytest.importorskip("wcwidth")

    tv.enable_emoji_support()
    try:
        assert tv.display_width("✅") == 2
        assert tv.display_width("👍🏽") == 2
        assert tv.display_width("👨‍👩‍👧‍👦") == 2
        assert tv.clip_cells("a✅b", 3) == "a✅"
        assert tv.clip_cells("👍🏽ok", 2) == "👍🏽"
        assert tv.clip_cells("👨‍👩‍👧‍👦ok", 2) == "👨‍👩‍👧‍👦"
        assert tv.align_text("✅", 4, "right") == "  ✅"

        buffer = tv.ScreenBuffer(5, 1)
        buffer.write(0, 0, "✅x")
        assert buffer.line_text(0) == "✅x  "
        assert [cell.char for cell in buffer._cells[0]] == ["✅", "", "x", " ", " "]
    finally:
        tv.enable_emoji_support(False)


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


def test_text_accepts_static_and_dynamic_styles() -> None:
    static = tv.Text("warning", style="warning")
    dynamic = tv.Text("error", style=lambda: "error")
    buffer = tv.ScreenBuffer(8, 2)

    static.render(tv.Painter(buffer).child(0, 0, 8, 1), tv.RenderContext(8, 1))
    dynamic.render(tv.Painter(buffer).child(0, 1, 8, 1), tv.RenderContext(8, 1))

    assert buffer.line_text(0).startswith("warning")
    assert buffer._cells[0][0].style == "warning"
    assert buffer.line_text(1).startswith("error")
    assert buffer._cells[1][0].style == "error"


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


def test_data_table_resolves_callable_rows_for_render_and_selection() -> None:
    rows = [{"name": "alpha"}]
    table = tv.DataTable([tv.Column("Name", "name")], lambda: rows)

    buffer = tv.ScreenBuffer(12, 3)
    table.render(tv.Painter(buffer), tv.RenderContext(12, 3, True, table))
    assert "alpha" in buffer.line_text(1)
    assert table.selected_item == rows[0]

    rows[:] = [{"name": "beta"}, {"name": "gamma"}]
    table.handle_key("end")
    buffer = tv.ScreenBuffer(12, 3)
    table.render(tv.Painter(buffer), tv.RenderContext(12, 3, True, table))

    assert "beta" in buffer.line_text(1)
    assert "gamma" in buffer.line_text(2)
    assert table.selected_item == rows[1]


def test_property_grid_static_and_callable_styles_use_raw_values() -> None:
    seen: list[object] = []

    def record_seen(value: object) -> str:
        seen.append(value)
        return "ok"

    grid = tv.PropertyGrid(
        {"state": "warning", "count": 3},
        [
            tv.Property("State", "state", style="muted"),
            tv.Property(
                "Count",
                "count",
                formatter=lambda value: f"{value} items",
                style=record_seen,
            ),
        ],
    )
    buffer = tv.ScreenBuffer(16, 2)

    grid.render(tv.Painter(buffer), tv.RenderContext(16, 2))

    assert buffer.line_text(0).startswith("State warning")
    assert buffer._cells[0][6].style == "muted"
    assert buffer.line_text(1).startswith("Count 3 items")
    assert buffer._cells[1][6].style == "ok"
    assert seen == [3]


def test_column_static_and_callable_styles() -> None:
    rows = [{"name": "api", "state": "error"}]
    table = tv.DataTable(
        [
            tv.Column("Name", "name", style="muted"),
            tv.Column("State", "state", style=lambda row: row["state"]),
        ],
        rows,
        selected_index=None,
    )
    buffer = tv.ScreenBuffer(20, 2)

    table.render(tv.Painter(buffer), tv.RenderContext(20, 2, False, table))

    assert buffer.line_text(1).startswith("api")
    assert buffer._cells[1][0].style == "muted"
    assert buffer._cells[1][10].style == "error"


def test_column_style_does_not_override_focused_selected_row() -> None:
    table = tv.DataTable(
        [tv.Column("Name", "name", style="error")],
        [{"name": "api"}],
    )
    buffer = tv.ScreenBuffer(10, 2)

    table.render(tv.Painter(buffer), tv.RenderContext(10, 2, True, table))

    assert buffer._cells[1][0].style == "selected"


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


def test_tree_view_accepts_field_accessors() -> None:
    root = {
        "id": "root",
        "label": "root",
        "children": [{"id": "child", "label": "child", "children": []}],
    }
    tree = tv.TreeView([root], id="id", label="label", children="children")

    tree.handle_key("right")
    tree.handle_key("down")

    assert tree.selected_node["id"] == "child"


def test_tree_view_resolves_callable_roots_for_render_and_selection() -> None:
    roots = [
        {
            "id": "root",
            "label": "root",
            "children": [{"id": "child", "label": "child"}],
        }
    ]
    tree = tv.TreeView(
        lambda: roots,
        id=lambda node: node["id"],
        label=lambda node: node["label"],
        children=lambda node: node.get("children", []),
    )

    tree.handle_key("right")
    tree.handle_key("down")
    assert tree.selected_node["id"] == "child"

    roots[:] = [{"id": "next", "label": "next", "children": []}]
    buffer = tv.ScreenBuffer(12, 2)
    tree.render(tv.Painter(buffer), tv.RenderContext(12, 2, True, tree))

    assert "next" in buffer.line_text(0)
    assert tree.selected_node["id"] == "next"


def test_tree_view_string_accessors_and_styles() -> None:
    root = {
        "id": "root",
        "label": "root",
        "state": "warning",
        "children": [
            {"id": "child", "label": "child", "state": "ok", "children": []}
        ],
    }
    tree = tv.TreeView(
        [root],
        id="id",
        label="label",
        children="children",
        style=lambda node: node["state"],
    )
    buffer = tv.ScreenBuffer(12, 2)

    tree.render(tv.Painter(buffer), tv.RenderContext(12, 2, False, tree))
    assert buffer.line_text(0).startswith("▸ root")
    assert buffer._cells[0][0].style == "warning"

    tree.handle_key("right")
    tree.render(tv.Painter(buffer), tv.RenderContext(12, 2, False, tree))
    assert buffer.line_text(1).startswith("  └─  child")
    assert buffer._cells[1][0].style == "ok"


def test_tree_view_static_style_and_focused_selection_override() -> None:
    tree = tv.TreeView([{"name": "root"}], label="name", style="muted")
    buffer = tv.ScreenBuffer(10, 1)

    tree.render(tv.Painter(buffer), tv.RenderContext(10, 1, False, tree))
    assert buffer._cells[0][0].style == "muted"

    tree.render(tv.Painter(buffer), tv.RenderContext(10, 1, True, tree))
    assert buffer._cells[0][0].style == "selected"


def test_tree_view_without_roots_renders_empty_safely() -> None:
    tree = tv.TreeView()
    buffer = tv.ScreenBuffer(8, 2)

    tree.render(tv.Painter(buffer), tv.RenderContext(8, 2, True, tree))

    assert tree.selected_node is None
    assert buffer.lines() == ["        ", "        "]


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


def test_log_view_string_text_accessor_and_styles() -> None:
    logs = [
        {"message": "started", "level": "ok"},
        {"message": "failed", "level": "error"},
    ]
    view = tv.LogView(logs, text="message", style=lambda entry: entry["level"])
    buffer = tv.ScreenBuffer(10, 2)

    view.render(tv.Painter(buffer), tv.RenderContext(10, 2, False, view))

    assert buffer.line_text(0).startswith("started")
    assert buffer._cells[0][0].style == "ok"
    assert buffer.line_text(1).startswith("failed")
    assert buffer._cells[1][0].style == "error"


def test_log_view_static_style() -> None:
    view = tv.LogView(["one"], style="muted")
    buffer = tv.ScreenBuffer(10, 1)

    view.render(tv.Painter(buffer), tv.RenderContext(10, 1, False, view))

    assert buffer.line_text(0).startswith("one")
    assert buffer._cells[0][0].style == "muted"


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
