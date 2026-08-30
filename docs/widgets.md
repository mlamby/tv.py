# Widgets

`tv.py` includes a small set of widgets for terminal telemetry dashboards. The
application owns the data and update loop. Widgets keep presentation state such
as focus, selection, scrolling, and expansion.

All widgets render into a rectangle measured in terminal display cells. Text is
clipped at cell boundaries, so wide Unicode characters and combining marks do
not leave broken glyphs in the buffer.

## Common Concepts

### Data Access

Widgets do not require framework-specific row or node types. Most data access
is done through either:

- a string name, read from a dictionary key or object attribute,
- a callable accessor, called with the source object.

For example, both rows below work with `Column("Name", "name")`:

```python
{"name": "api-1"}
device.name
```

If a named value is missing, `tv.py` renders it as an empty string.

### Formatting

Descriptor objects such as `Column` and `Property` can take a `formatter`
callable. The formatter receives the raw value returned by the accessor and
must return display text.

```python
Column("Latency", "latency_ms", formatter=lambda value: f"{value:.0f} ms")
```

### Styling

Widgets write semantic style names such as `"normal"`, `"muted"`, `"ok"`, or
`"error"` into the screen buffer. The final ANSI colors come from
`DEFAULT_STYLES` plus any application overrides. See [styling.md](styling.md)
for the style map.

Data-bearing widgets follow a consistent callback rule:

```python
accessor(source) -> raw
formatter(raw) -> text
style(source) -> style_name
```

For `PropertyGrid`, `Property.style(raw_value)` receives the raw property
value. For `DataTable`, `TreeView`, and `LogView`, the source is a row, node,
or log entry.

## Text

`Text` displays one or more lines of text. It is useful for status lines,
headings, help text, or static messages.

```python
Text("Tab changes focus | q exits", style="muted")
Text(
    lambda: f"connected clients: {len(clients)}",
    style=lambda: "warning" if reconnecting else "normal",
)
```

### Data

`Text` accepts either a string or a zero-argument callable. If a callable is
provided, it is evaluated every render, which makes it suitable for live status
values derived from application state.

### Formatting

`Text` converts the value to `str` and splits it into lines. It does not support
per-line formatters or accessors.

### Styling

The `style` argument is a single semantic style name or a zero-argument
callable returning one. Dynamic styles are evaluated each render.

### Behavior

`Text` is not focusable and does not handle keys.

## PropertyGrid

`PropertyGrid` displays key/value rows for one source object. It is commonly
used for details about a selected table row or tree node.

```python
details = PropertyGrid(
    source=device,
    properties=[
        Property("Name", "name"),
        Property("Status", "status", style=status_style),
        Property("Rate/s", "rate", align="right", formatter=lambda v: f"{v:.1f}"),
    ],
)
```

### Data

The grid reads from `source`, which may be an object, dictionary, `None`, or a
zero-argument callable that returns one. Callable sources are resolved each
time the grid renders:

```python
details = PropertyGrid(
    source=lambda: table.selected_item,
    properties=[Property("Status", "status")],
)
```

Applications can also replace `details.source` between renders:

```python
details.source = table.selected_item
```

Each `Property` describes one row:

- `label`: text shown in the label column.
- `value`: dictionary key, attribute name, or callable receiving `source`.

### Formatting

`Property.formatter` receives the raw value and returns display text.
`Property.align` controls alignment of the value column and may be `"left"`,
`"right"`, or `"center"`.

`PropertyGrid(label_width=...)` sets a fixed label width. When omitted, the
widest property label determines the label column width.

### Styling

Property labels always use the `"muted"` style. Property values use
`Property.style`, which can be either a style name or a callable. Callable
styles receive the raw property value before formatting.

### Behavior

`PropertyGrid` is not focusable and does not handle keys.

## DataTable

`DataTable` displays a scrollable list of application rows with a header and
zero or one selected row.

```python
table = DataTable(
    columns=[
        Column("Name", "name", width=Size.auto()),
        Column("Status", "status", width=Size.fixed(10), style=lambda row: row.status),
        Column("Type", "type", width=Size.fixed(8), style="muted"),
        Column("Rate", "rate", width=Size.flex(1), align="right"),
    ],
    rows=devices,
)
```

### Data

`rows` is a mutable list owned by the application, or a zero-argument callable
returning the current list. Rows may be dictionaries or objects. The application
may mutate row objects, append rows, remove rows, or replace `table.rows`
between renders.

Each `Column` describes one visible column:

- `title`: header text.
- `value`: dictionary key, attribute name, or callable receiving the row.
- `width`: a `Size` descriptor.
- `align`: header and cell alignment.
- `formatter`: optional raw-value formatter.
- `style`: style name or optional callable receiving the row and returning a
  style name. The default is `"normal"`.

### Formatting

Column text is produced by reading the raw value and applying `formatter` when
one is configured. Alignment applies to both the header and body cells.

Column widths use the same `Size` model as layouts:

- `Size.fixed(n)`: reserve exactly `n` columns, clipped to available width.
- `Size.flex(weight)`: share remaining width by weight.
- `Size.auto()`: use a preferred width based on the header and sampled rows.

### Styling

Headers use the `"title"` style. Cells use `Column.style(row)` when a callable
is provided, otherwise the configured static style name.

When the table has focus, the selected row uses the `"selected"` style across
all cells. That selected-row style takes precedence over column styles.

### Behavior

`DataTable` is focusable. It owns:

- `selected_index`: the selected row index, or `None` for no selection.
- `selected_item`: the selected row object, or `None`.
- `scroll_offset`: the first visible body row.

Handled keys:

- `up`: move selection up.
- `down`: move selection down.
- `home`: select the first row.
- `end`: select the last row.

Rendering automatically scrolls to keep the selected row visible.

## TreeView

`TreeView` displays arbitrary application objects as a collapsible tree.

```python
tree = TreeView(
    roots=devices,
    id=lambda node: node.path,
    label="name",
    children="children",
    style=lambda node: status_style(node.status),
)
```

### Data

`roots` is the top-level list of application nodes, or a zero-argument callable
returning the current list. It defaults to a new empty list when omitted. Nodes
do not need to inherit from any framework class.

Accessors configure the tree:

- `id`: returns a stable identity for preserving expansion state.
- `label`: returns display text for one node.
- `children`: returns the node's child list.
- `style`: style name or callable receiving a node and returning a style name.

When `id` is omitted, Python object identity is used. Prefer stable application
IDs when node objects may be recreated between refreshes.

`id`, `label`, and `children` can be either string names or callables. String
names are read from dictionary keys or object attributes. Missing or empty
children values are treated as an empty list.

### Formatting

Tree labels come from the `label` accessor. The widget converts labels to text
and adds Unicode tree prefixes and expand/collapse markers before the label.

### Styling

Normal rows use the configured static style or `TreeView.style(node)` when a
callable is provided. When the tree has focus, the selected visible node uses
`"selected"`.

### Behavior

`TreeView` is focusable. It owns:

- `selected_index`: index in the current visible node list.
- `selected_node`: the selected visible node, or `None` when empty.
- `scroll_offset`: the first visible row.
- `expanded_ids`: the set of expanded node IDs.

Handled keys:

- `up`: move selection up.
- `down`: move selection down.
- `right` or `enter`: expand the selected node when it has children.
- `left`: collapse the selected node when it is expanded.

Rendering automatically scrolls to keep the selected visible node in view.

## LogView

`LogView` displays append-only log lines or log entry objects. It follows the
end by default and supports manual scrollback.

```python
log = LogView(
    entries=model.logs,
    text="message",
    style=lambda entry: "error" if entry.level == "error" else "normal",
)
```

### Data

`entries` is a mutable list owned by the application. Entries may be strings,
dictionaries, objects, or any other application type.

The optional `text` argument can be a string accessor or a callable. When
omitted, entries are converted with `str`.

### Formatting

`LogView` has no descriptor object. Formatting is handled by the `text`
accessor, and the resolved value is converted with `str`.

### Styling

The `style` argument is a semantic style name or callable receiving the raw
entry and returning a semantic style name. When omitted, log lines use
`"normal"`.

### Behavior

`LogView` is focusable. It owns:

- `scroll_offset`: the first visible entry.
- `follow`: whether the view should follow the newest entries.

Handled keys:

- `up`: enter scrollback mode and scroll up one line.
- `down`: scroll down one line; following resumes at the bottom.
- `home`: enter scrollback mode and jump to the first entry.
- `end`: resume following the end.

## Panel

`Panel` wraps another widget with optional border, title, and padding.

```python
Panel(table, title="Devices")
Panel(Text("plain"), border=False, padding=1)
```

### Data

`Panel` does not own data. It renders its child inside the remaining interior
rectangle.

### Formatting

`title` is rendered in the top border when `border=True`. If `border=False`,
the title is ignored. `padding` adds blank cells between the border and child,
or around the child when borderless.

`Panel(size=...)` can provide a default layout size when the panel is added to a
layout without an explicit size.

### Styling

Borders use `"border"`. A bordered panel uses `"focus_border"` when its child or
one of its descendants contains focus. Titles use `"title"`.

### Behavior

`Panel` is not focusable. Focus traversal passes through to the wrapped child.

## VBox and HBox

`VBox` and `HBox` are layout containers. They are widgets, but they are not
content widgets and do not receive focus.

```python
root = VBox()
root.add(Panel(status, title="Status"), Size.fixed(3))
root.add(Panel(table, title="Devices"), Size.flex(1))
root.add(Panel(log, title="Log"), Size.fixed(8))
```

### Data

Layouts own child widget references and their layout sizes. They do not own
application data.

### Formatting

Children are sized with `Size`:

- In `VBox`, fixed sizes are rows.
- In `HBox`, fixed sizes are columns.
- Flexible children share remaining space by weight.
- Automatic sizes use child preferred sizes.

Layouts also provide builder helpers:

```python
with root.hbox(Size.flex(1)) as row:
    row.panel(table, Size.flex(2), title="Devices")
    row.panel(details, Size.flex(1), title="Details")
```

### Styling

Layouts do not draw their own styles. Child widgets fill the allocated regions.

### Behavior

Layouts are not focusable. Focus traversal walks their children in object-tree
order.
