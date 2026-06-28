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
- Built-in widgets: `Text`, `PropertyGrid`, `DataTable`, `TreeView`,
  `LogView`, and `Panel`.

## Requirements

- Python 3.9+
- A modern terminal with ANSI support, such as Windows Terminal or a common
  Linux/macOS terminal.

There are no runtime third-party dependencies.

## Quick Start

```python
from tv import App, Column, DataTable, LogView, Panel, Size, Text, VBox

devices = [
    {"name": "api-1", "status": "ok", "rate": 420.0},
    {"name": "api-2", "status": "warning", "rate": 370.0},
]
logs = ["dashboard started"]

table = DataTable(
    columns=[
        Column("Name", "name", width=Size.auto()),
        Column("Status", "status", width=Size.fixed(10)),
        Column("Rate", "rate", width=Size.flex(1), align="right"),
    ],
    rows=devices,
)

root = VBox()
root.add(Panel(Text("Tab changes focus | q exits"), title="Status"), Size.fixed(3))
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
can work in application labels, but they may flicker or misalign in some
terminals because emoji width and presentation are not consistent across
terminal/font combinations.

## Project Documentation

- [docs/design.md](docs/design.md) preserves the original design intent and
  scope for the framework.
- [docs/styling.md](docs/styling.md) explains semantic styles and ANSI style
  numbers.
- Public types and functions are documented inline in [tv.py](tv.py).

## Development

Run the tests with:

```sh
pytest -q
```

Optional static tooling is configured in [pyproject.toml](pyproject.toml).
