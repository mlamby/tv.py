# tv.py

`tv.py` is a small, single-file Python terminal dashboard framework for
real-time engineering telemetry.

It is designed to be copied into a project like `bottle.py`: one file, no
runtime dependencies, direct ANSI rendering, and a compact object-tree API for
status screens, tables, trees, property views, and logs.

## Features

- Direct ANSI terminal rendering, no curses.
- Alternate-screen terminal sessions with clean restore on exit.
- Full redraw rendering through an in-memory `ScreenBuffer`.
- Unicode box drawing and display-cell-aware clipping.
- Vertical and horizontal layouts with `Size.fixed`, `Size.flex`, and
  `Size.auto`.
- Multiple named screens with preserved widget state.
- Focus traversal and light keyboard handling.
- Built-in widgets: `Text`, `StatusLine`, `PropertyGrid`, `DataTable`,
  `TreeView`, `LogView`, and `Panel`.

## Requirements

- Python 3.9+
- A modern terminal with ANSI support, such as Windows Terminal or a common
  Linux/macOS terminal.

There are no required runtime third-party dependencies. Optional emoji-aware
measurement can use `regex` and `wcwidth`.

## Quick Start

```python
from tv import App, Column, DataTable, LogView, Panel, Size, StatusItem, StatusLine, VBox

devices = [
    {"name": "api-1", "status": "ok", "tier": "edge", "rate": 420.0},
    {"name": "api-2", "status": "warning", "tier": "core", "rate": 370.0},
]
logs = ["dashboard started"]

table = DataTable(
    columns=[
        Column("Name", "name", width=Size.auto()),
        Column("Status", "status", width=Size.fixed(10)),
        Column("Tier", "tier", width=Size.fixed(8), style="muted"),
        Column("Rate", "rate", width=Size.flex(1), align="right"),
    ],
    rows=devices,
)

status = StatusLine(
    [
        StatusItem("Devices", lambda: len(devices), width=Size.fixed(12), align="right"),
        StatusItem("", "Tab changes focus | q exits", width=Size.flex(1), style="muted"),
    ],
    style="muted",
)

root = VBox()
root.add(Panel(status, title="Status"), Size.fixed(3))
root.add(Panel(table, title="Devices"), Size.flex(1))
root.add(Panel(LogView(logs), title="Log"), Size.fixed(8))

app = App(refresh_hz=10)
app.add_screen("overview", root)

with app.session():
    while app.running:
        key = app.poll_key()
        if key:
            app.handle_key(key)

        app.render()
        app.sleep_until_next_frame()
```

Run the fuller demo with:

```sh
python example.py
```

## Optional Builder API

The explicit object tree is the underlying layout model. For readability, apps
can also build the same tree from explicit parent methods:

```python
app = App(refresh_hz=10)

with app.screen("overview") as screen:
    with screen.vbox() as root:
        root.panel(Text("Tab changes focus | q exits"), Size.fixed(3), title="Status")
        root.panel(table, Size.flex(1), title="Devices")
        root.panel(LogView(logs), Size.fixed(8), title="Log")
```

Each child is still created from its parent, such as ``root.panel(...)`` or
``root.hbox(...)``. Constructors remain side-effect free.

Use ``Panel(widget, border=False)`` when you want panel sizing/padding without
drawing a border or title.

## Application Model

The application owns the main loop and domain data. Widgets own presentation
state such as selection, scroll offsets, and tree expansion. Accessors connect
application objects to widgets.

`Column` values, `Property` values, and `TreeView` `id`, `label`, and
`children` accessors may be attribute/key names or callables. Use a field name
for direct reads such as `"status"` or `"children"`, use `path()` for nested
reads, and use a callable when the display value is derived from more than one
field.

`PropertyGrid.source`, `DataTable.rows`, and `TreeView.roots` may be concrete
values or zero-argument callables that return the current value. Callable
sources are resolved when the widget renders, handles keys, or reports its
selected item/node:

```python
latest_message = {}

tree = TreeView(
    roots=lambda: build_tree_roots(latest_message),
    id="path",
    label="name",
    children="children",
)

table = DataTable(
    columns=[Column("Path", "path"), Column("Value", "value")],
    rows=lambda: leaf_fields_under(tree.selected_node),
)

details = PropertyGrid(
    source=lambda: selected_message(tree.selected_node),
    properties=[
        Property("Health", path("message.status.overall_health")),
        PropertyPattern("message.payload.health.*_status", label="leaf"),
    ],
)
```

Use `path()` for nested field access in dictionaries, objects, and indexed
lists. It can be passed anywhere a widget expects an accessor:

```python
health = path("status.overall_health", default="unknown")
wrapped_health = path("message.status.overall_health", default="unknown")
speed_knots = path(
    "navigation.speed_ms",
    default=0.0,
    transform=lambda value: float(value) * 1.943844,
)
```

Use `PropertyPattern` when a `PropertyGrid` should render multiple properties
from a nested object. Pattern segments use shell-style glob matching:

```python
details = PropertyGrid(
    source=lambda: latest_message,
    properties=[
        Property("Message", path("header.message_id")),
        PropertyPattern(
            "payload.health.*_status",
            label="leaf",
            style=lambda match: str(match.value),
        ),
        PropertyPattern("payload.sensors[*].health.state"),
        PropertyPattern("payload.**.*_status"),
    ],
)
```

`*`, `?`, and character classes such as `[ab]` match within one field name.
`**` as a full path segment matches across nested dictionaries, objects, and
sequences. `[*]` expands list or tuple indexes. Pattern rows are sorted by
resolved path by default and render no rows when nothing matches.
Python `Enum` instances are treated as leaf values, so a glob such as
`*_status` matches the enum field itself rather than expanding into `.name` and
`.value`.

Use `match_paths()` when the same traversal should feed another widget:

```python
leaves = DataTable(
    columns=[
        Column("Path", "path"),
        Column("Type", "type_name"),
        Column("Value", "value"),
    ],
    rows=lambda: match_paths(latest_message["payload"], prefix="latest"),
)
```

`Property` style callables receive the raw value before formatting.
`PropertyPattern` style and formatter callables receive a `PathMatch`, so they
can use `match.path`, `match.name`, `match.type_name`, and `match.value`.
Pass `prefix=...` to mount returned paths under a display path.
Use `iter_path_children()` when an application needs immediate child fields,
for example to build a tree view model with the same traversal rules.

A typical loop looks like:

```python
with app.session():
    while app.running:
        telemetry.service()
        commands.service()

        key = app.poll_key()
        if key:
            app.handle_key(key)

        update_widgets_from_application_state()

        app.render()
        app.sleep_until_next_frame()
```

## Keyboard Handling

`App.handle_key()` dispatches keys in this order:

1. Framework global keys.
2. Application-defined bindings.
3. Focused widget key handling.
4. Optional application fallback.

Built-in global keys:

- `q` and Ctrl-C request exit.
- Tab moves focus forward.
- Shift-Tab moves focus backward.

Example screen bindings:

```python
app.bind("alt+1", lambda: app.show_screen("overview"))
app.bind("alt+2", lambda: app.show_screen("health"))
```

## Unicode Notes

The built-in icons are text symbols rather than colorful emoji. This keeps
terminal cell widths predictable and lets ANSI styles color the glyphs. Emoji
can work in application labels, but terminal rendering still depends on the
terminal and font.

For emoji-aware grapheme clustering, clipping, and display-width measurement,
install the optional dependencies and enable support explicitly:

```sh
python -m pip install regex wcwidth
```

Run the install command with the same Python interpreter or virtual environment
that runs your dashboard.

```python
import tv

tv.enable_emoji_support()
```

If the optional dependencies are missing, `enable_emoji_support()` raises a
clear error with the install command.

## Project Documentation

- [docs/design.md](docs/design.md) preserves the original design intent and
  scope for the framework.
- [docs/styling.md](docs/styling.md) explains semantic styles and ANSI style
  numbers.
- Public types and functions are documented inline in [tv.py](tv.py).

## AI Assistance

This codebase has been developed with assistance from Codex. Changes are
reviewed, tested, and maintained by the project author.

## Development

Run the tests with:

```sh
pytest -q
```

Optional static tooling is configured in [pyproject.toml](pyproject.toml).
