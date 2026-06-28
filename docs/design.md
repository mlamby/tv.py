# Design Brief

Create a small single-file Python 3.9+ terminal dashboard framework for displaying real-time telemetry data.

The framework should be implemented in one Python file, in the style of `bottle.py`: easy to copy into a project, simple public API, minimal assumptions, and no third-party dependencies unless absolutely necessary.

The framework is not intended to be a general-purpose terminal UI toolkit. It is specifically for engineering/telemetry dashboards: displaying changing data, logs, status information, tables, and trees, with light keyboard navigation.

Core requirements:

* Use direct ANSI terminal rendering.
* Do not use curses.
* Target modern terminals only, including Windows Terminal and common Linux terminals.
* Always use the terminal alternate screen buffer.
* Restore the terminal cleanly on exit, including cursor visibility and terminal modes.
* Use a full redraw strategy for v1.
* Render into an intermediate in-memory screen buffer, then flush that buffer to the terminal.
* Unicode should be assumed and supported by default.
* Use Unicode box-drawing characters for borders.
* Treat layout and clipping in terms of terminal display cell width, not Python string length.

Application model:

* The application owns the main loop.
* The framework provides helpers for:

  * entering/exiting the terminal session,
  * rendering,
  * keyboard polling,
  * standard key handling,
  * focus management,
  * screen switching,
  * sleeping until the next frame.
* Do not force telemetry handling into a background thread.
* A typical usage should look like:

```python
app = App(refresh_hz=10)

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

Screens:

* Support multiple named screens.
* Screens are built once at startup and retained.
* Switching screens only changes which screen is currently rendered.
* Widget state such as scroll position, selection, and tree expansion should be preserved when switching screens.
* Support application-defined key bindings, for example:

```python
app.bind("ctrl+1", lambda: app.show_screen("overview"))
app.bind("ctrl+2", lambda: app.show_screen("health"))
```

Keyboard handling:

Use this priority order:

1. Framework global keys.
2. Application-defined key bindings.
3. Focused widget key handling.
4. Optional application fallback.

Initial global keys:

* `q` and Ctrl-C request exit.
* Tab moves focus forward.
* Shift-Tab moves focus backward.

Focus model:

* Only focusable content widgets receive focus.
* Containers such as `Panel`, `VBox`, and `HBox` do not receive focus.
* `Panel` may visually indicate focus when its child has focus.

Layout:

* Support vertical and horizontal layouts.
* Support fixed-size and flexible-size sections.
* Reuse a common sizing concept across layouts and table columns.
* The internal model may use something like:

```python
Size.fixed(10)
Size.flex(1)
Size.auto()
```

* `VBox` lays out children vertically.
* `HBox` lays out children horizontally.
* A fixed size in `VBox` means rows.
* A fixed size in `HBox` means columns.
* Flexible sections share remaining space by weight.

Provide an explicit object-tree API first, for example:

```python
root = VBox()
root.add(Panel(status, title="Status", size=Size.fixed(3)))

row = HBox()
row.add(Panel(device_tree, title="Devices", size=Size.flex(1)))
row.add(Panel(health_table, title="Health", size=Size.flex(2)))

root.add(row, size=Size.flex(1))
root.add(Panel(log_view, title="Log", size=Size.fixed(8)))

app.add_screen("overview", root)
```

Optionally provide a lightweight builder/context-manager DSL as syntactic sugar, but the explicit API should be the real underlying model.

Widgets:

Widgets are stateful. They own presentation state, while the application owns domain data.

Design principle:

* The application owns telemetry data.
* Widgets own presentation state.
* Accessors connect application objects to widgets.

Built-in widgets for v1:

1. `Text`
2. `PropertyGrid`
3. `DataTable`
4. `TreeView`
5. `LogView`
6. `Panel`

`Panel`:

* A container/decorator widget.
* Adds a Unicode border, optional title, and optional padding.
* Can render without border or title, for example `Panel(widget, border=False)`.
* Passes the remaining interior rectangle to its child.
* Should be usable around any widget.

`DataTable`:

* Displays a list of application objects.
* Rows should be objects or dictionaries, not framework-specific row types.
* Columns should be strongly defined using `Column` objects.
* Columns describe presentation.
* A column should support:

  * title,
  * attribute name or accessor function,
  * fixed/auto/flexible width,
  * alignment,
  * optional formatter,
  * optional style accessor.
* The table should support:

  * header row,
  * vertical scrolling,
  * zero or one selected row,
  * arrow-key navigation,
  * automatic scrolling to keep selection visible,
  * `selected_item`,
  * `selected_index`.
* Do not implement multi-select, cell selection, editing, or sorting in v1.

Example:

```python
table = DataTable(
    columns=[
        Column("Name", "name", width=Size.auto()),
        Column("Status", "status", width=Size.fixed(10)),
        Column("Rate", "rate", width=Size.flex(1), align="right"),
    ],
    rows=devices,
)
```

`TreeView`:

* Displays arbitrary application objects as a tree.
* Do not require application objects to inherit from framework classes.
* Configure the tree with accessors:

```python
tree = TreeView(
    roots=devices,
    id=lambda node: node.path,
    label=lambda node: node.name,
    children=lambda node: node.children,
)
```

* The `id` accessor should provide stable identity for preserving expanded/collapsed state.
* Default identity may be Python object identity, but documentation should recommend stable IDs.
* TreeView should own:

  * selected node,
  * scroll offset,
  * expanded/collapsed node IDs.
* Support Unicode tree glyphs.
* Support keyboard navigation and expand/collapse.

`PropertyGrid`:

* Displays key/value properties for one object or a list of property descriptors.
* Should support labels, values, alignment, optional formatting, and optional semantic style.
* Useful for showing details of a selected object.

`LogView`:

* Displays append-only log lines or log entry objects.
* Owns scroll state.
* Supports following the end by default.
* Allows user scrollback when focused.
* Should support a text accessor and optional style accessor.

`Text`:

* Displays one or more lines of text.
* Useful for instructions, status lines, headings, and static messages.
* Usually not focusable.

Rendering API:

Widgets should render through a small `Painter` API and a `RenderContext`.

Example:

```python
class Widget:
    def render(self, painter, context):
        ...
```

`RenderContext` should initially include:

* width
* height
* focused

The `Painter` should provide a small API such as:

```python
p.write(x, y, text, style="normal", width=None, align="left")
p.fill(x, y, width, height, char=" ", style="normal")
p.hline(x, y, width, char="─", style="border")
p.vline(x, y, height, char="│", style="border")
p.box(x, y, width, height, title=None, style="border")
```

Painter requirements:

* Coordinates are relative to the widget’s allocated rectangle.
* Automatically clip output to the painter bounds.
* Correctly handle Unicode display cell widths.
* Do not expose ANSI escape sequences to widgets.
* Support left, right, and center alignment.
* Do not implement text wrapping in v1; clipping is sufficient.

Styling:

Use semantic style names, not hard-coded colours throughout widget code.

Initial semantic styles:

* `normal`
* `muted`
* `title`
* `border`
* `focus_border`
* `selected`
* `ok`
* `warning`
* `error`

Applications should be able to override the mapping from semantic style names to terminal attributes.

Unicode:

* Unicode is the native/default mode.
* Use Unicode box-drawing characters for panels.
* Use Unicode tree glyphs for TreeView.
* Provide a small `Icons` namespace or class with semantic icons such as:

  * `Icons.OK`
  * `Icons.WARNING`
  * `Icons.ERROR`
  * `Icons.EXPANDED`
  * `Icons.COLLAPSED`

Non-goals for v1:

* No curses.
* No mouse support.
* No forms.
* No text editing.
* No menus or dialogs.
* No charts or graphs.
* No multi-select.
* No cell editing.
* No general-purpose widget lifecycle system.
* No reactive data-binding framework.
* No dependency on Textual, Rich, Urwid, Blessed, or similar TUI frameworks.

Deliverables:

1. A single Python file containing the framework.
2. A small example application in the same file under `if __name__ == "__main__":`.
3. The example should demonstrate:

   * alternate screen buffer,
   * manual application-owned loop,
   * two named screens,
   * Ctrl-1 / Ctrl-2 screen switching,
   * layout with `VBox`, `HBox`, fixed and flexible sizes,
   * `Panel`,
   * `DataTable`,
   * `TreeView`,
   * `PropertyGrid`,
   * `LogView`,
   * semantic styles,
   * Unicode borders and tree glyphs.
4. Keep the implementation readable and compact rather than overly abstract.
5. Prefer clear, explicit code over clever metaprogramming.
6. Use mypy strict mode, and ruff to ensure code quality.
7. Write unit tests and run pytest.
