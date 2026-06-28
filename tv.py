"""Small single-file terminal dashboard framework.
This module is intentionally kept as the copyable framework file.
"""

from __future__ import annotations

import contextlib
import ctypes
import os
import select
import shutil
import sys
import time
import unicodedata
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from typing import Any, Callable, Optional, TextIO, Union, cast

__version__ = "0.1.0"


ESC = "\x1b"
CSI = f"{ESC}["


class Icons:
    """Semantic Unicode icons for status and tree widgets.

    The constants are plain strings so applications can reuse them in labels,
    table formatters, and log lines without depending on widget internals.
    """

    OK = "✓"
    WARNING = "⚠"
    ERROR = "✗"
    EXPANDED = "▾"
    COLLAPSED = "▸"


DEFAULT_STYLES: dict[str, str] = {
    "normal": "37",
    "muted": "90",
    "title": "1;97",
    "border": "90",
    "focus_border": "1;97",
    "selected": "30;47",
    "ok": "32",
    "warning": "33",
    "error": "31",
}


@dataclass(frozen=True)
class RenderContext:
    """Render-time information passed to every widget.

    Attributes:
        width: Width of the widget's drawing area in terminal cells.
        height: Height of the widget's drawing area in terminal cells.
        focused: True when this widget, or a child of this widget, has focus.
        focused_widget: The concrete focused widget for the current screen.
    """

    width: int
    height: int
    focused: bool = False
    focused_widget: Optional["Widget"] = None


@dataclass
class Cell:
    """One terminal display cell in a :class:`ScreenBuffer`.

    Attributes:
        char: The rendered cluster for this cell. Continuation cells for
            double-width characters store an empty string.
        style: Symbolic style name resolved through ``DEFAULT_STYLES`` or an
            application-provided style map.
    """

    char: str = " "
    style: str = "normal"


def cell_width(text: str) -> int:
    """Return the terminal display width of one Unicode cluster.

    The function returns 0 for combining/control-format clusters, 2 for East
    Asian full-width or wide clusters, and 1 otherwise. If a longer string is
    passed, only the first code point is used.
    """
    if not text:
        return 0
    first = text[0]
    category = unicodedata.category(first)
    if category in {"Mn", "Me", "Cf"}:
        return 0
    if unicodedata.east_asian_width(first) in {"F", "W"}:
        return 2
    return 1


def iter_clusters(text: str) -> Iterator[str]:
    """Yield a lightweight approximation of terminal display clusters.

    Base characters are grouped with following zero-width combining characters.
    This is intentionally small rather than a full Unicode grapheme
    implementation, but it is enough for safe clipping in common telemetry
    dashboards.
    """
    cluster = ""
    for char in text:
        if not cluster:
            cluster = char
            continue
        if cell_width(char) == 0:
            cluster += char
            continue
        yield cluster
        cluster = char
    if cluster:
        yield cluster


def display_width(text: str) -> int:
    """Return the number of terminal cells needed to display ``text``."""
    return sum(cell_width(cluster) for cluster in iter_clusters(text))


def clip_cells(text: str, width: int) -> str:
    """Clip ``text`` to at most ``width`` terminal display cells.

    The returned string never ends halfway through a wide character or a
    combining sequence. Non-positive widths return an empty string.
    """
    if width <= 0:
        return ""
    used = 0
    clipped: list[str] = []
    for cluster in iter_clusters(text):
        cluster_width = cell_width(cluster)
        if used + cluster_width > width:
            break
        clipped.append(cluster)
        used += cluster_width
    return "".join(clipped)


def align_text(text: str, width: int, align: str = "left") -> str:
    """Clip and pad ``text`` to exactly ``width`` display cells.

    ``align`` may be ``"left"``, ``"right"``, or ``"center"``. Unknown values
    fall back to left alignment.
    """
    clipped = clip_cells(text, width)
    extra = width - display_width(clipped)
    if extra <= 0:
        return clipped
    if align == "right":
        return (" " * extra) + clipped
    if align == "center":
        left = extra // 2
        return (" " * left) + clipped + (" " * (extra - left))
    return clipped + (" " * extra)


def terminal_size(fallback: tuple[int, int] = (80, 24)) -> tuple[int, int]:
    """Return the current terminal size as ``(columns, rows)``.

    ``fallback`` is used when the process is not attached to a real terminal.
    """
    size = shutil.get_terminal_size(fallback=fallback)
    return size.columns, size.lines


class ScreenBuffer:
    """In-memory terminal screen used for full redraw rendering.

    Widgets draw into this buffer through :class:`Painter`. The buffer stores
    symbolic style names and Unicode display clusters, then converts the whole
    screen to ANSI text with :meth:`render_ansi`.
    """

    def __init__(self, width: int, height: int, style: str = "normal") -> None:
        self.width = max(0, width)
        self.height = max(0, height)
        self._cells: list[list[Cell]] = [
            [Cell(style=style) for _ in range(self.width)] for _ in range(self.height)
        ]

    def clear(self, style: str = "normal") -> None:
        """Clear every cell in the buffer to spaces using ``style``."""
        self.fill(0, 0, self.width, self.height, " ", style)

    def fill(
        self,
        x: int,
        y: int,
        width: int,
        height: int,
        char: str = " ",
        style: str = "normal",
    ) -> None:
        """Fill a rectangular region in absolute buffer coordinates.

        Coordinates are clipped to the buffer. ``char`` is clipped to one
        terminal cell before drawing.
        """
        if width <= 0 or height <= 0:
            return
        draw_char = clip_cells(char, 1) or " "
        x_start = max(0, x)
        y_start = max(0, y)
        x_end = min(self.width, x + width)
        y_end = min(self.height, y + height)
        for row in range(y_start, y_end):
            for col in range(x_start, x_end):
                self._cells[row][col] = Cell(draw_char, style)

    def write(self, x: int, y: int, text: str, style: str = "normal") -> None:
        """Write ``text`` into the buffer at absolute coordinates.

        Text is measured in terminal display cells and clipped at the right
        edge. Negative ``x`` values skip off-screen cells until text enters the
        buffer.
        """
        if y < 0 or y >= self.height or x >= self.width:
            return
        col = x
        for cluster in iter_clusters(text):
            width = cell_width(cluster)
            if width == 0:
                continue
            if col + width <= 0:
                col += width
                continue
            if col < 0:
                col += width
                continue
            if col + width > self.width:
                break
            self._cells[y][col] = Cell(cluster, style)
            if width == 2:
                self._cells[y][col + 1] = Cell("", style)
            col += width

    def line_text(self, y: int) -> str:
        """Return one rendered line without ANSI style sequences."""
        if y < 0 or y >= self.height:
            return ""
        return "".join(cell.char for cell in self._cells[y])

    def lines(self) -> list[str]:
        """Return every rendered line without ANSI style sequences."""
        return [self.line_text(y) for y in range(self.height)]

    def render_ansi(self, styles: Optional[dict[str, str]] = None) -> str:
        """Render the buffer as ANSI text starting at the terminal origin.

        ``styles`` maps symbolic style names to SGR fragments such as ``"31"``
        or ``"1;36"``. Missing names resolve to ``"normal"``.
        """
        theme = DEFAULT_STYLES.copy()
        if styles:
            theme.update(styles)

        parts: list[str] = [f"{CSI}H"]
        current_style: Optional[str] = None
        for y, row in enumerate(self._cells):
            if y:
                if current_style != "normal":
                    parts.append(_style_sequence("normal", theme))
                    current_style = "normal"
                parts.append("\r\n")
            for cell in row:
                if cell.char == "":
                    continue
                if cell.style != current_style:
                    parts.append(_style_sequence(cell.style, theme))
                    current_style = cell.style
                parts.append(cell.char)
        parts.append(f"{CSI}0m")
        return "".join(parts)


class Painter:
    """Clipped drawing API exposed to widgets.

    A painter represents a rectangular viewport into a :class:`ScreenBuffer`.
    All coordinates passed to its methods are relative to that viewport and are
    clipped to it.
    """

    def __init__(
        self,
        buffer: ScreenBuffer,
        x: int = 0,
        y: int = 0,
        width: Optional[int] = None,
        height: Optional[int] = None,
    ) -> None:
        self._buffer = buffer
        self.x = x
        self.y = y
        self.width = buffer.width - x if width is None else max(0, width)
        self.height = buffer.height - y if height is None else max(0, height)

    def child(self, x: int, y: int, width: int, height: int) -> "Painter":
        """Return a painter clipped to a child rectangle."""
        child_x = self.x + x
        child_y = self.y + y
        child_width = min(max(0, width), max(0, self.width - x))
        child_height = min(max(0, height), max(0, self.height - y))
        return Painter(self._buffer, child_x, child_y, child_width, child_height)

    def fill(
        self,
        x: int,
        y: int,
        width: int,
        height: int,
        char: str = " ",
        style: str = "normal",
    ) -> None:
        """Fill a rectangle relative to this painter."""
        clipped = self._clip_rect(x, y, width, height)
        if clipped is None:
            return
        draw_x, draw_y, draw_width, draw_height = clipped
        self._buffer.fill(draw_x, draw_y, draw_width, draw_height, char, style)

    def write(
        self,
        x: int,
        y: int,
        text: str,
        style: str = "normal",
        width: Optional[int] = None,
        align: str = "left",
    ) -> None:
        """Write clipped, optionally aligned text relative to this painter."""
        if y < 0 or y >= self.height:
            return
        available = self.width - x if width is None else width
        if available <= 0:
            return
        text_width = max(0, min(available, self.width - x))
        if text_width <= 0:
            return
        visible = align_text(text, text_width, align)
        self._buffer.write(self.x + x, self.y + y, visible, style)

    def hline(
        self,
        x: int,
        y: int,
        width: int,
        char: str = "─",
        style: str = "border",
    ) -> None:
        """Draw a horizontal line using a one-cell character."""
        self.write(x, y, char * max(0, width), style, width=width)

    def vline(
        self,
        x: int,
        y: int,
        height: int,
        char: str = "│",
        style: str = "border",
    ) -> None:
        """Draw a vertical line using a one-cell character."""
        for offset in range(max(0, height)):
            self.write(x, y + offset, char, style, width=1)

    def box(
        self,
        x: int,
        y: int,
        width: int,
        height: int,
        title: Optional[str] = None,
        style: str = "border",
    ) -> None:
        """Draw a Unicode box with an optional title."""
        if width <= 0 or height <= 0:
            return
        if width == 1:
            self.vline(x, y, height, "│", style)
            return
        if height == 1:
            self.hline(x, y, width, "─", style)
            return

        self.write(x, y, "┌", style, width=1)
        self.hline(x + 1, y, width - 2, "─", style)
        self.write(x + width - 1, y, "┐", style, width=1)
        self.vline(x, y + 1, height - 2, "│", style)
        self.vline(x + width - 1, y + 1, height - 2, "│", style)
        self.write(x, y + height - 1, "└", style, width=1)
        self.hline(x + 1, y + height - 1, width - 2, "─", style)
        self.write(x + width - 1, y + height - 1, "┘", style, width=1)

        if title and width > 4:
            title_text = clip_cells(f" {title} ", width - 4)
            self.write(x + 2, y, title_text, "title", width=display_width(title_text))

    def _clip_rect(
        self, x: int, y: int, width: int, height: int
    ) -> Optional[tuple[int, int, int, int]]:
        x_start = max(0, x)
        y_start = max(0, y)
        x_end = min(self.width, x + width)
        y_end = min(self.height, y + height)
        if x_end <= x_start or y_end <= y_start:
            return None
        return self.x + x_start, self.y + y_start, x_end - x_start, y_end - y_start


@dataclass(frozen=True)
class Size:
    """Shared sizing descriptor for layouts and table columns.

    ``Size.fixed(n)`` reserves an exact number of rows or columns. ``flex``
    entries share remaining space by weight. ``auto`` asks the widget or column
    for a preferred size and then clips to available space.
    """

    kind: str
    value: int = 0

    @staticmethod
    def fixed(value: int) -> "Size":
        """Create a fixed size measured in rows or columns."""
        return Size("fixed", max(0, value))

    @staticmethod
    def flex(weight: int = 1) -> "Size":
        """Create a flexible size that shares remaining space by ``weight``."""
        return Size("flex", max(1, weight))

    @staticmethod
    def auto() -> "Size":
        """Create an automatic size based on preferred content size."""
        return Size("auto", 0)


@dataclass(frozen=True)
class Rect:
    """A terminal-cell rectangle.

    This is a small value type for APIs that need to pass around explicit
    geometry. Coordinates use the same ``x, y, width, height`` convention as
    :class:`Painter`.
    """

    x: int
    y: int
    width: int
    height: int


class Widget:
    """Base class for dashboard widgets.

    Subclasses usually override :meth:`render`, optionally :meth:`handle_key`,
    and set ``focusable = True`` when they should receive keyboard focus.
    Containers should delegate focus traversal through
    :meth:`focusable_widgets`.
    """

    focusable = False

    def preferred_size(self, axis: str) -> int:
        """Return the widget's preferred size on ``"vertical"`` or ``"horizontal"``."""
        return 1

    def render(self, painter: Painter, context: RenderContext) -> None:
        """Draw the widget into ``painter`` using ``context``."""
        del painter, context

    def handle_key(self, key: str) -> bool:
        """Handle a normalized key name and return True if it was consumed."""
        del key
        return False

    def focusable_widgets(self) -> list["Widget"]:
        """Return focusable content widgets contained by this widget."""
        if self.focusable:
            return [self]
        return []

    def contains_focus(self, focused_widget: Optional["Widget"]) -> bool:
        """Return True when ``focused_widget`` is this widget or one of its children."""
        return focused_widget is self


@dataclass
class _LayoutItem:
    widget: Widget
    size: Size


class _LinearLayout(Widget):
    axis = "vertical"

    def __init__(self) -> None:
        self.children: list[_LayoutItem] = []

    def __enter__(self) -> "_LinearLayout":
        """Return this layout for readable builder-style ``with`` blocks."""
        return self

    def __exit__(
        self,
        exc_type: Optional[type[BaseException]],
        exc: Optional[BaseException],
        traceback: Optional[Any],
    ) -> None:
        """Leave a layout ``with`` block without performing registration."""
        del exc_type, exc, traceback

    def add(self, widget: Widget, size: Optional[Size] = None) -> Widget:
        """Add ``widget`` to the layout and return it.

        ``size`` overrides the child's own ``size`` attribute, if present.
        Returning the widget makes it convenient to keep references to stateful
        children while building a tree.
        """
        default_size = getattr(widget, "size", Size.flex(1))
        self.children.append(_LayoutItem(widget, size or default_size))
        return widget

    def add_child(self, widget: Widget, size: Optional[Size] = None) -> Widget:
        """Add ``widget`` to this layout and return it.

        This is a builder-style alias for :meth:`add`; it exists for code that
        wants child creation and attachment to read uniformly from the parent.
        """
        return self.add(widget, size)

    def panel(
        self,
        child: Widget,
        size: Optional[Size] = None,
        title: Optional[str] = None,
        border: bool = True,
        padding: int = 0,
    ) -> "Panel":
        """Create a :class:`Panel`, add it to this layout, and return it."""
        panel = Panel(child, title=title, border=border, padding=padding)
        return cast(Panel, self.add(panel, size))

    def vbox(self, size: Optional[Size] = None) -> "VBox":
        """Create a child :class:`VBox`, add it to this layout, and return it."""
        return cast(VBox, self.add(VBox(), size))

    def hbox(self, size: Optional[Size] = None) -> "HBox":
        """Create a child :class:`HBox`, add it to this layout, and return it."""
        return cast(HBox, self.add(HBox(), size))

    def preferred_size(self, axis: str) -> int:
        if not self.children:
            return 1
        if axis == self.axis:
            return sum(item.widget.preferred_size(axis) for item in self.children)
        return max(item.widget.preferred_size(axis) for item in self.children)

    def render(self, painter: Painter, context: RenderContext) -> None:
        if self.axis == "vertical":
            total = painter.height
            cross = painter.width
        else:
            total = painter.width
            cross = painter.height
        extents = _allocate_sizes(
            total,
            [item.size for item in self.children],
            [item.widget.preferred_size(self.axis) for item in self.children],
        )
        offset = 0
        for item, extent in zip(self.children, extents):
            if self.axis == "vertical":
                child_painter = painter.child(0, offset, cross, extent)
                child_context = RenderContext(
                    cross,
                    extent,
                    item.widget.contains_focus(context.focused_widget),
                    context.focused_widget,
                )
            else:
                child_painter = painter.child(offset, 0, extent, cross)
                child_context = RenderContext(
                    extent,
                    cross,
                    item.widget.contains_focus(context.focused_widget),
                    context.focused_widget,
                )
            item.widget.render(child_painter, child_context)
            offset += extent

    def focusable_widgets(self) -> list[Widget]:
        widgets: list[Widget] = []
        for item in self.children:
            widgets.extend(item.widget.focusable_widgets())
        return widgets

    def contains_focus(self, focused_widget: Optional[Widget]) -> bool:
        return any(item.widget.contains_focus(focused_widget) for item in self.children)


class VBox(_LinearLayout):
    """Lay child widgets out from top to bottom.

    Add children with ``add(widget, size=...)``. Fixed sizes are measured in
    rows, and flexible sizes share remaining vertical space.
    """

    axis = "vertical"


class HBox(_LinearLayout):
    """Lay child widgets out from left to right.

    Add children with ``add(widget, size=...)``. Fixed sizes are measured in
    columns, and flexible sizes share remaining horizontal space.
    """

    axis = "horizontal"


class Panel(Widget):
    """Container around any widget with optional border and title.

    Args:
        child: Widget rendered inside the panel.
        title: Optional text shown in the top border when ``border`` is true.
        border: Whether to draw a Unicode border around the child. Use
            ``Panel(text, border=False)`` for a titleless, borderless wrapper.
        padding: Extra blank cells between the panel chrome and child content.
        size: Optional layout size used when the panel is added to a layout
            without an explicit size.

    Panels are not focusable themselves. Bordered panels use the
    ``focus_border`` style when the child contains focus.
    """

    def __init__(
        self,
        child: Widget,
        title: Optional[str] = None,
        border: bool = True,
        padding: int = 0,
        size: Optional[Size] = None,
    ) -> None:
        self.child = child
        self.title = title if border else None
        self.border = border
        self.padding = max(0, padding)
        self.size = size

    def preferred_size(self, axis: str) -> int:
        border_size = 2 if self.border else 0
        return self.child.preferred_size(axis) + border_size + (self.padding * 2)

    def render(self, painter: Painter, context: RenderContext) -> None:
        inset = self.padding
        if self.border:
            border_style = "focus_border" if context.focused else "border"
            painter.box(0, 0, painter.width, painter.height, self.title, border_style)
            inset += 1
        child_width = max(0, painter.width - (inset * 2))
        child_height = max(0, painter.height - (inset * 2))
        if child_width <= 0 or child_height <= 0:
            return
        child_painter = painter.child(inset, inset, child_width, child_height)
        child_context = RenderContext(
            child_width,
            child_height,
            self.child.contains_focus(context.focused_widget),
            context.focused_widget,
        )
        self.child.render(child_painter, child_context)

    def focusable_widgets(self) -> list[Widget]:
        return self.child.focusable_widgets()

    def contains_focus(self, focused_widget: Optional[Widget]) -> bool:
        return self.child.contains_focus(focused_widget)


class Text(Widget):
    """Display one or more lines of text.

    Args:
        text: A string or zero-argument callable returning the current text.
            Callables are evaluated each render, which is useful for status
            lines derived from live application state.
        style: Symbolic style name used for every rendered line.
    """

    def __init__(
        self, text: Union[str, Callable[[], str]], style: str = "normal"
    ) -> None:
        self.text = text
        self.style = style

    def preferred_size(self, axis: str) -> int:
        lines = self._lines()
        if axis == "vertical":
            return max(1, len(lines))
        return max((display_width(line) for line in lines), default=1)

    def render(self, painter: Painter, context: RenderContext) -> None:
        del context
        for y, line in enumerate(self._lines()[: painter.height]):
            painter.write(0, y, line, self.style, width=painter.width)

    def _lines(self) -> list[str]:
        value = self.text() if callable(self.text) else self.text
        return str(value).splitlines() or [""]


@dataclass
class Property:
    """Descriptor for one :class:`PropertyGrid` row.

    Attributes:
        label: Label rendered in the left column.
        value: Attribute/key name or callable used to read from the grid source.
        align: Alignment for the value column.
        formatter: Optional function that converts the raw value to text.
        style: Style name or callable receiving the formatted/raw value and
            returning a style name.
    """

    label: str
    value: Union[str, Callable[[Any], Any]]
    align: str = "left"
    formatter: Optional[Callable[[Any], str]] = None
    style: Union[str, Callable[[Any], str]] = "normal"


class PropertyGrid(Widget):
    """Display key/value properties for one application object.

    Args:
        source: Object or dictionary read by property descriptors. It may be
            replaced by the application between renders.
        properties: Ordered list of :class:`Property` descriptors.
        label_width: Optional fixed label column width. When omitted, the
            widest label determines the width.
    """

    def __init__(
        self,
        source: Any = None,
        properties: Optional[list[Property]] = None,
        label_width: Optional[int] = None,
    ) -> None:
        self.source = source
        self.properties = properties or []
        self.label_width = label_width

    def preferred_size(self, axis: str) -> int:
        if axis == "vertical":
            return max(1, len(self.properties))
        labels = [display_width(prop.label) for prop in self.properties]
        return (max(labels) if labels else 8) + 12

    def render(self, painter: Painter, context: RenderContext) -> None:
        del context
        label_width = self.label_width
        if label_width is None:
            label_width = max(
                (display_width(prop.label) for prop in self.properties),
                default=0,
            )
        label_width = min(label_width, max(0, painter.width - 1))
        for y, prop in enumerate(self.properties[: painter.height]):
            value = self._property_value(prop)
            style = prop.style(value) if callable(prop.style) else prop.style
            painter.write(0, y, prop.label, "muted", width=label_width)
            if painter.width > label_width:
                painter.write(label_width, y, " ", width=1)
            value_width = max(0, painter.width - label_width - 1)
            painter.write(
                label_width + 1,
                y,
                value,
                style,
                width=value_width,
                align=prop.align,
            )

    def _property_value(self, prop: Property) -> str:
        raw = (
            prop.value(self.source)
            if callable(prop.value)
            else _get_value(self.source, prop.value)
        )
        if prop.formatter:
            return prop.formatter(raw)
        return str(raw)


@dataclass
class Column:
    """Presentation descriptor for a :class:`DataTable` column.

    Attributes:
        title: Header text.
        value: Attribute/key name or callable used to read a row value.
        width: :class:`Size` controlling the column width.
        align: Alignment for header and cell text.
        formatter: Optional function that converts the raw value to text.
        style: Optional callable receiving the row object and returning a style
            name for this column's cell.
    """

    title: str
    value: Union[str, Callable[[Any], Any]]
    width: Size = Size.flex(1)
    align: str = "left"
    formatter: Optional[Callable[[Any], str]] = None
    style: Optional[Callable[[Any], str]] = None

    def text_for(self, row: Any) -> str:
        """Return formatted display text for ``row``."""
        raw = self.value(row) if callable(self.value) else _get_value(row, self.value)
        if self.formatter:
            return self.formatter(raw)
        return str(raw)

    def style_for(self, row: Any) -> str:
        """Return the symbolic style name for ``row``."""
        if self.style:
            return self.style(row)
        return "normal"


class DataTable(Widget):
    """Scrollable single-selection table for application rows.

    Args:
        columns: Ordered :class:`Column` descriptors.
        rows: Mutable list of dictionaries or objects owned by the application.
        selected_index: Initial selected row index, or ``None`` for no
            selection.

    The table owns selection and scroll state. It handles ``up``, ``down``,
    ``home``, and ``end`` keys when focused.
    """

    focusable = True

    def __init__(
        self,
        columns: list[Column],
        rows: Optional[list[Any]] = None,
        selected_index: Optional[int] = 0,
    ) -> None:
        self.columns = columns
        self.rows = rows if rows is not None else []
        self.selected_index = selected_index
        self.scroll_offset = 0

    @property
    def selected_item(self) -> Any:
        """Return the selected row object, or ``None`` if nothing is selected."""
        if self.selected_index is None:
            return None
        if 0 <= self.selected_index < len(self.rows):
            return self.rows[self.selected_index]
        return None

    def preferred_size(self, axis: str) -> int:
        return 4 if axis == "vertical" else 24

    def render(self, painter: Painter, context: RenderContext) -> None:
        widths = _allocate_column_widths(painter.width, self.columns, self.rows)
        x = 0
        for column, width in zip(self.columns, widths):
            painter.write(x, 0, column.title, "title", width=width, align=column.align)
            x += width
        visible_height = max(0, painter.height - 1)
        self._clamp_selection()
        self._ensure_selection_visible(visible_height)
        for screen_y in range(visible_height):
            row_index = self.scroll_offset + screen_y
            if row_index >= len(self.rows):
                break
            row = self.rows[row_index]
            row_style = (
                "selected"
                if context.focused and row_index == self.selected_index
                else "normal"
            )
            x = 0
            for column, width in zip(self.columns, widths):
                style = row_style if row_style == "selected" else column.style_for(row)
                painter.write(
                    x,
                    screen_y + 1,
                    column.text_for(row),
                    style,
                    width=width,
                    align=column.align,
                )
                x += width

    def handle_key(self, key: str) -> bool:
        if key == "up":
            self._move_selection(-1)
            return True
        if key == "down":
            self._move_selection(1)
            return True
        if key == "home":
            self.selected_index = 0 if self.rows else None
            self.scroll_offset = 0
            return True
        if key == "end":
            self.selected_index = len(self.rows) - 1 if self.rows else None
            return True
        return False

    def _move_selection(self, delta: int) -> None:
        if not self.rows:
            self.selected_index = None
            return
        index = 0 if self.selected_index is None else self.selected_index
        self.selected_index = min(max(0, index + delta), len(self.rows) - 1)

    def _clamp_selection(self) -> None:
        if not self.rows:
            self.selected_index = None
            self.scroll_offset = 0
            return
        if self.selected_index is None:
            return
        self.selected_index = min(max(0, self.selected_index), len(self.rows) - 1)

    def _ensure_selection_visible(self, visible_height: int) -> None:
        if self.selected_index is None or visible_height <= 0:
            return
        if self.selected_index < self.scroll_offset:
            self.scroll_offset = self.selected_index
        if self.selected_index >= self.scroll_offset + visible_height:
            self.scroll_offset = self.selected_index - visible_height + 1
        max_offset = max(0, len(self.rows) - visible_height)
        self.scroll_offset = max(0, min(self.scroll_offset, max_offset))


class TreeView(Widget):
    """Scrollable tree view for arbitrary application objects.

    Args:
        roots: Top-level application objects.
        id: Optional callable returning a stable identity for a node. Stable IDs
            preserve expansion state across object refreshes.
        label: Optional callable returning display text for a node.
        children: Optional callable returning a node's child list.

    The tree owns selection, scroll offset, and expanded node IDs. It handles
    arrow-key navigation plus ``enter``/``right`` to expand and ``left`` to
    collapse.
    """

    focusable = True

    def __init__(
        self,
        roots: list[Any],
        id: Optional[Callable[[Any], Any]] = None,
        label: Optional[Callable[[Any], str]] = None,
        children: Optional[Callable[[Any], list[Any]]] = None,
    ) -> None:
        self.roots = roots
        self.id: Callable[[Any], Any] = id or builtins_id
        self.label: Callable[[Any], str] = label or str
        self.children: Callable[[Any], list[Any]] = children or _empty_children
        self.expanded_ids: set[Any] = set()
        self.selected_index = 0
        self.scroll_offset = 0

    @property
    def selected_node(self) -> Any:
        """Return the selected visible node, or ``None`` when the tree is empty."""
        visible = self._visible_nodes()
        if not visible:
            return None
        self.selected_index = min(max(0, self.selected_index), len(visible) - 1)
        return visible[self.selected_index][0]

    def preferred_size(self, axis: str) -> int:
        return 5 if axis == "vertical" else 20

    def render(self, painter: Painter, context: RenderContext) -> None:
        visible = self._visible_nodes()
        self._clamp(visible, painter.height)
        for screen_y in range(painter.height):
            index = self.scroll_offset + screen_y
            if index >= len(visible):
                break
            node, depth, is_last, ancestors_last = visible[index]
            node_id = self.id(node)
            kids = self.children(node)
            marker = " "
            if kids:
                marker = (
                    Icons.EXPANDED
                    if node_id in self.expanded_ids
                    else Icons.COLLAPSED
                )
            prefix = _tree_prefix(depth, is_last, ancestors_last)
            style = (
                "selected"
                if context.focused and index == self.selected_index
                else "normal"
            )
            painter.write(
                0,
                screen_y,
                f"{prefix}{marker} {self.label(node)}",
                style,
                width=painter.width,
            )

    def handle_key(self, key: str) -> bool:
        visible = self._visible_nodes()
        if key == "up":
            self.selected_index = max(0, self.selected_index - 1)
            return True
        if key == "down":
            self.selected_index = min(max(0, len(visible) - 1), self.selected_index + 1)
            return True
        if key in {"right", "enter"}:
            node = self.selected_node
            if node is not None and self.children(node):
                self.expanded_ids.add(self.id(node))
                return True
        if key == "left":
            node = self.selected_node
            if node is not None:
                node_id = self.id(node)
                if node_id in self.expanded_ids:
                    self.expanded_ids.remove(node_id)
                    return True
        return False

    def _visible_nodes(self) -> list[tuple[Any, int, bool, list[bool]]]:
        rows: list[tuple[Any, int, bool, list[bool]]] = []

        def visit(nodes: list[Any], depth: int, ancestors_last: list[bool]) -> None:
            for index, node in enumerate(nodes):
                is_last = index == len(nodes) - 1
                rows.append((node, depth, is_last, ancestors_last))
                node_id = self.id(node)
                if node_id in self.expanded_ids:
                    visit(self.children(node), depth + 1, [*ancestors_last, is_last])

        visit(self.roots, 0, [])
        return rows

    def _clamp(
        self,
        visible: list[tuple[Any, int, bool, list[bool]]],
        height: int,
    ) -> None:
        if not visible:
            self.selected_index = 0
            self.scroll_offset = 0
            return
        self.selected_index = min(max(0, self.selected_index), len(visible) - 1)
        if self.selected_index < self.scroll_offset:
            self.scroll_offset = self.selected_index
        if self.selected_index >= self.scroll_offset + height:
            self.scroll_offset = self.selected_index - height + 1
        max_offset = max(0, len(visible) - height)
        self.scroll_offset = max(0, min(self.scroll_offset, max_offset))


class LogView(Widget):
    """Append-only log viewer with follow-tail behavior.

    Args:
        entries: Mutable list of log entry objects owned by the application.
        text: Optional callable converting an entry to display text.
        style: Optional callable returning a symbolic style name for an entry.

    The view follows the end by default. Pressing ``up`` enters scrollback mode;
    pressing ``down`` to the bottom or ``end`` resumes following.
    """

    focusable = True

    def __init__(
        self,
        entries: Optional[list[Any]] = None,
        text: Optional[Callable[[Any], str]] = None,
        style: Optional[Callable[[Any], str]] = None,
    ) -> None:
        self.entries = entries if entries is not None else []
        self.text = text or str
        self.style = style
        self.scroll_offset = 0
        self.follow = True
        self._last_height = 0

    def preferred_size(self, axis: str) -> int:
        return 4 if axis == "vertical" else 30

    def render(self, painter: Painter, context: RenderContext) -> None:
        del context
        self._last_height = painter.height
        if self.follow:
            self.scroll_offset = max(0, len(self.entries) - painter.height)
        max_offset = max(0, len(self.entries) - painter.height)
        self.scroll_offset = max(0, min(self.scroll_offset, max_offset))
        for screen_y in range(painter.height):
            index = self.scroll_offset + screen_y
            if index >= len(self.entries):
                break
            entry = self.entries[index]
            style = self.style(entry) if self.style else "normal"
            painter.write(0, screen_y, self.text(entry), style, width=painter.width)

    def handle_key(self, key: str) -> bool:
        if key == "up":
            self.follow = False
            self.scroll_offset = max(0, self.scroll_offset - 1)
            return True
        if key == "down":
            self.scroll_offset += 1
            max_offset = max(0, len(self.entries) - max(1, self._last_height))
            if self.scroll_offset >= max_offset:
                self.scroll_offset = max_offset
                self.follow = True
            return True
        if key == "end":
            self.follow = True
            return True
        if key == "home":
            self.follow = False
            self.scroll_offset = 0
            return True
        return False


class App:
    """Application helper for manual telemetry dashboard loops.

    Args:
        refresh_hz: Target render cadence used by
            :meth:`sleep_until_next_frame`.
        styles: Optional style overrides mapping symbolic names to ANSI SGR
            fragments.
        fallback: Optional key handler called when global keys, bindings, and
            the focused widget do not consume a key.

    ``App`` owns screens, focus, key dispatch, and frame pacing. The caller owns
    the main loop and any telemetry or command servicing.
    """

    def __init__(
        self,
        refresh_hz: float = 10.0,
        styles: Optional[dict[str, str]] = None,
        fallback: Optional[Callable[[str], bool]] = None,
    ) -> None:
        self.refresh_hz = refresh_hz
        self.styles = DEFAULT_STYLES.copy()
        if styles:
            self.styles.update(styles)
        self.running = True
        self.screens: dict[str, Widget] = {}
        self.current_screen: Optional[str] = None
        self.bindings: dict[str, Callable[[], None]] = {}
        self.fallback = fallback
        self._session: Optional[TerminalSession] = None
        self._focused_widget: Optional[Widget] = None
        self._last_frame = time.monotonic()

    @contextlib.contextmanager
    def session(self) -> Iterator[TerminalSession]:
        """Enter a managed terminal session for rendering the app.

        The session switches to the alternate screen, hides the cursor, adjusts
        keyboard mode, and restores terminal state on exit.
        """
        with TerminalSession(styles=self.styles) as session:
            self._session = session
            try:
                yield session
            finally:
                self._session = None

    def add_screen(self, name: str, root: Widget) -> None:
        """Register a named screen rooted at ``root``.

        Screens are retained as widget trees, so widget state such as scroll
        offsets and selection survives switching away and back.
        """
        self.screens[name] = root
        if self.current_screen is None:
            self.current_screen = name
            self._sync_focus()

    def screen(self, name: str) -> "_ScreenBuilder":
        """Build and register a named screen with an explicit root.

        The returned context manager yields a screen builder. The builder must
        be given exactly one root widget, either through ``vbox()``, ``hbox()``,
        or ``set_root(widget)``. On successful context exit, the root is
        registered with :meth:`add_screen`.
        """
        return _ScreenBuilder(self, name)

    def show_screen(self, name: str) -> None:
        """Make an existing named screen current.

        Raises:
            KeyError: If ``name`` has not been registered with
                :meth:`add_screen`.
        """
        if name not in self.screens:
            raise KeyError(f"Unknown screen: {name}")
        self.current_screen = name
        self._sync_focus()

    def bind(self, key: str, callback: Callable[[], None]) -> None:
        """Bind a normalized key name to a zero-argument callback.

        Key names are normalized with :func:`normalize_key`, so ``"Ctrl+1"``
        and ``"ctrl+1"`` refer to the same binding.
        """
        self.bindings[normalize_key(key)] = callback

    def request_exit(self) -> None:
        """Ask the main loop to stop by setting ``running`` to ``False``."""
        self.running = False

    def poll_key(self) -> Optional[str]:
        """Return one pending normalized key name, or ``None`` if no key is ready."""
        key = _poll_key()
        if key is None:
            return None
        return normalize_key(key)

    def handle_key(self, key: str) -> bool:
        """Dispatch one key according to the framework priority order.

        Global keys are handled first, then application bindings, then the
        focused widget, then the optional fallback handler. Returns True when
        any handler consumed the key.
        """
        normalized = normalize_key(key)
        if normalized in {"q", "ctrl+c"}:
            self.request_exit()
            return True
        if normalized == "tab":
            self.focus_next()
            return True
        if normalized == "shift+tab":
            self.focus_previous()
            return True
        binding = self.bindings.get(normalized)
        if binding:
            binding()
            return True
        if self._focused_widget and self._focused_widget.handle_key(normalized):
            return True
        if self.fallback:
            return self.fallback(normalized)
        return False

    def focus_next(self) -> None:
        """Move focus to the next focusable widget on the current screen."""
        self._move_focus(1)

    def focus_previous(self) -> None:
        """Move focus to the previous focusable widget on the current screen."""
        self._move_focus(-1)

    def render(self) -> ScreenBuffer:
        """Render the current screen and flush it when inside :meth:`session`.

        Returns the :class:`ScreenBuffer` for testing or custom flushing.
        """
        root = self._current_root()
        width, height = terminal_size()
        buffer = ScreenBuffer(width, height)
        if root:
            context = RenderContext(width, height, True, self._focused_widget)
            root.render(Painter(buffer), context)
        if self._session:
            self._session.flush(buffer)
        return buffer

    def sleep_until_next_frame(self) -> None:
        """Sleep just long enough to maintain ``refresh_hz`` frame pacing."""
        if self.refresh_hz <= 0:
            return
        frame_time = 1.0 / self.refresh_hz
        now = time.monotonic()
        delay = self._last_frame + frame_time - now
        if delay > 0:
            time.sleep(delay)
        self._last_frame = time.monotonic()

    def _current_root(self) -> Optional[Widget]:
        if self.current_screen is None:
            return None
        return self.screens.get(self.current_screen)

    def _focusables(self) -> list[Widget]:
        root = self._current_root()
        if root is None:
            return []
        return root.focusable_widgets()

    def _sync_focus(self) -> None:
        focusables = self._focusables()
        if not focusables:
            self._focused_widget = None
            return
        if self._focused_widget not in focusables:
            self._focused_widget = focusables[0]

    def _move_focus(self, delta: int) -> None:
        focusables = self._focusables()
        if not focusables:
            self._focused_widget = None
            return
        if self._focused_widget not in focusables:
            self._focused_widget = focusables[0]
            return
        index = focusables.index(self._focused_widget)
        self._focused_widget = focusables[(index + delta) % len(focusables)]


class _ScreenBuilder:
    """Context manager that registers one explicitly assigned screen root."""

    def __init__(self, app: App, name: str) -> None:
        self._app = app
        self._name = name
        self._root: Optional[Widget] = None

    def __enter__(self) -> "_ScreenBuilder":
        return self

    def __exit__(
        self,
        exc_type: Optional[type[BaseException]],
        exc: Optional[BaseException],
        traceback: Optional[Any],
    ) -> None:
        del exc, traceback
        if exc_type is not None:
            return
        if self._root is None:
            raise ValueError(f"Screen {self._name!r} has no root widget")
        self._app.add_screen(self._name, self._root)

    def set_root(self, widget: Widget) -> Widget:
        """Assign and return the screen root widget.

        A screen builder accepts exactly one root. Child widgets should be
        attached explicitly through that root or another explicit parent.
        """
        if self._root is not None:
            raise ValueError(f"Screen {self._name!r} already has a root widget")
        self._root = widget
        return widget

    def vbox(self) -> VBox:
        """Create, assign, and return a :class:`VBox` root."""
        return cast(VBox, self.set_root(VBox()))

    def hbox(self) -> HBox:
        """Create, assign, and return an :class:`HBox` root."""
        return cast(HBox, self.set_root(HBox()))


def normalize_key(key: str) -> str:
    """Normalize a key name for binding and dispatch.

    Normalization lowercases the name and removes spaces. For example,
    ``"Shift + Tab"`` becomes ``"shift+tab"``.
    """
    return key.lower().replace(" ", "")


def _allocate_sizes(total: int, sizes: list[Size], preferred: list[int]) -> list[int]:
    if not sizes:
        return []
    remaining = max(0, total)
    allocated = [0 for _ in sizes]
    flex_indexes: list[int] = []
    flex_weight = 0
    for index, size in enumerate(sizes):
        if size.kind == "fixed":
            allocated[index] = min(remaining, size.value)
            remaining -= allocated[index]
        elif size.kind == "auto":
            amount = min(remaining, max(1, preferred[index]))
            allocated[index] = amount
            remaining -= amount
        else:
            flex_indexes.append(index)
            flex_weight += size.value
    for index in flex_indexes:
        if flex_weight <= 0:
            amount = 0
        else:
            amount = remaining * sizes[index].value // flex_weight
        allocated[index] = amount
    used = sum(allocated)
    cursor = 0
    while used < total and flex_indexes:
        index = flex_indexes[cursor % len(flex_indexes)]
        allocated[index] += 1
        used += 1
        cursor += 1
    return allocated


def _allocate_column_widths(
    width: int,
    columns: list[Column],
    rows: list[Any],
) -> list[int]:
    preferred: list[int] = []
    for column in columns:
        values = [display_width(column.text_for(row)) for row in rows[:50]]
        preferred.append(max([display_width(column.title), *values], default=1) + 1)
    return _allocate_sizes(width, [column.width for column in columns], preferred)


def _get_value(source: Any, name: str) -> Any:
    if source is None:
        return ""
    if isinstance(source, Mapping):
        mapping = cast(Mapping[str, Any], source)
        return mapping.get(name, "")
    return getattr(source, name, "")


def _tree_prefix(depth: int, is_last: bool, ancestors_last: list[bool]) -> str:
    parts: list[str] = []
    for ancestor_last in ancestors_last:
        parts.append("  " if ancestor_last else "│ ")
    if depth:
        parts.append("└─" if is_last else "├─")
    return "".join(parts)


def builtins_id(value: Any) -> int:
    return id(value)


def _empty_children(node: Any) -> list[Any]:
    del node
    return []


def _poll_key() -> Optional[str]:
    if os.name == "nt":
        return _poll_key_windows()
    return _poll_key_posix()


def _poll_key_posix() -> Optional[str]:
    if not sys.stdin.isatty():
        return None
    fd = sys.stdin.fileno()
    ready, _, _ = select.select([fd], [], [], 0)
    if not ready:
        return None
    char = _read_fd_char(fd)
    if char is None:
        return None
    if char == "\x03":
        return "ctrl+c"
    if char == "\t":
        return "tab"
    if char == "\x1b":
        return _read_escape_sequence(fd)
    return char


def _read_escape_sequence(fd: int) -> str:
    if not sys.stdin.isatty():
        return "escape"
    introducer = _read_stdin_char(fd, 0.1)
    if introducer is None:
        return "escape"
    if introducer == "O":
        final = _read_stdin_char(fd, 0.1)
        if final == "A":
            return "up"
        if final == "B":
            return "down"
        if final == "C":
            return "right"
        if final == "D":
            return "left"
        return "escape"
    if introducer != "[":
        if len(introducer) == 1 and introducer.isprintable():
            return f"alt+{introducer.lower()}"
        return "escape"
    sequence = _read_csi_sequence(fd)
    return _parse_csi_sequence(sequence)


def _read_stdin_char(fd: int, timeout: float) -> Optional[str]:
    ready, _, _ = select.select([fd], [], [], timeout)
    if not ready:
        return None
    return _read_fd_char(fd)


def _read_fd_char(fd: int) -> Optional[str]:
    with contextlib.suppress(BlockingIOError, OSError):
        data = os.read(fd, 1)
        if data:
            return data.decode(errors="ignore")
    return None


def _read_csi_sequence(fd: int) -> str:
    chars: list[str] = []
    while True:
        char = _read_stdin_char(fd, 0.1)
        if char is None:
            break
        chars.append(char)
        if "@" <= char <= "~":
            break
    return "".join(chars)


def _parse_csi_sequence(sequence: str) -> str:
    if sequence == "A":
        return "up"
    if sequence == "B":
        return "down"
    if sequence == "C":
        return "right"
    if sequence == "D":
        return "left"
    if sequence == "H":
        return "home"
    if sequence == "F":
        return "end"
    if sequence == "Z":
        return "shift+tab"
    if sequence.endswith("~"):
        return _parse_tilde_csi(sequence[:-1])
    if sequence.endswith(("A", "B", "C", "D", "H", "F")):
        return _parse_modified_arrow(sequence)
    return "escape"


def _parse_tilde_csi(body: str) -> str:
    if body == "1":
        return "home"
    if body == "4":
        return "end"
    return "escape"


def _parse_modified_arrow(sequence: str) -> str:
    final = sequence[-1]
    parts = sequence[:-1].split(";")
    if len(parts) < 2:
        return "escape"
    modifier = parts[-1]
    base = {
        "A": "up",
        "B": "down",
        "C": "right",
        "D": "left",
        "H": "home",
        "F": "end",
    }.get(final)
    if base is None:
        return "escape"
    if modifier == "2":
        return f"shift+{base}"
    if modifier == "3":
        return f"alt+{base}"
    return base


def _poll_key_windows() -> Optional[str]:
    with contextlib.suppress(ImportError):
        msvcrt = __import__("msvcrt")

        if not msvcrt.kbhit():
            return None
        char = cast(str, msvcrt.getwch())
        if char == "\x03":
            return "ctrl+c"
        if char == "\t":
            return "tab"
        if char in {"\x00", "\xe0"}:
            code = msvcrt.getwch()
            key = {
                "H": "up",
                "P": "down",
                "K": "left",
                "M": "right",
                "G": "home",
                "O": "end",
            }.get(code, "escape")
            return key
        return char
    return None


class TerminalSession:
    """Context manager for a direct ANSI terminal drawing session.

    Args:
        output: Text stream that receives ANSI rendering.
        input_file: Text stream used for terminal mode changes.
        styles: Optional style overrides used when flushing buffers.

    Entering the session enables Windows VT processing when needed, enters the
    alternate screen buffer, hides the cursor, and configures POSIX terminals
    for immediate key reads. Exiting restores those modes.
    """

    def __init__(
        self,
        output: TextIO = sys.stdout,
        input_file: TextIO = sys.stdin,
        styles: Optional[dict[str, str]] = None,
    ) -> None:
        self.output = output
        self.input_file = input_file
        self.styles = DEFAULT_STYLES.copy()
        if styles:
            self.styles.update(styles)
        self._fd: Optional[int] = None
        self._old_termios: Any = None
        self._windows_console_mode: Optional[tuple[Any, int]] = None

    def __enter__(self) -> "TerminalSession":
        self._windows_console_mode = _enable_windows_vt_mode()
        self._enter_terminal_mode()
        self.output.write(f"{CSI}?1049h{CSI}?25l{CSI}2J{CSI}H")
        self.output.flush()
        return self

    def __exit__(
        self,
        exc_type: object,
        exc: object,
        traceback: object,
    ) -> None:
        self.restore()

    def restore(self) -> None:
        """Restore cursor visibility, terminal modes, and the main buffer."""
        self.output.write(f"{CSI}0m{CSI}?25h{CSI}?1049l")
        self.output.flush()
        self._restore_terminal_mode()
        self._restore_windows_console_mode()

    def flush(self, buffer: ScreenBuffer) -> None:
        """Flush ``buffer`` to the session output using this session's styles."""
        self.output.write(buffer.render_ansi(self.styles))
        self.output.flush()

    def _enter_terminal_mode(self) -> None:
        if os.name != "posix" or not self.input_file.isatty():
            return
        with contextlib.suppress(ImportError, OSError):
            import termios

            self._fd = self.input_file.fileno()
            self._old_termios = termios.tcgetattr(self._fd)
            attrs = termios.tcgetattr(self._fd)
            attrs[3] = attrs[3] & ~(termios.ECHO | termios.ICANON | termios.ISIG)
            attrs[6][termios.VMIN] = 1
            attrs[6][termios.VTIME] = 0
            termios.tcsetattr(self._fd, termios.TCSADRAIN, attrs)

    def _restore_terminal_mode(self) -> None:
        if self._fd is None or self._old_termios is None:
            return
        with contextlib.suppress(ImportError, OSError):
            import termios

            termios.tcsetattr(self._fd, termios.TCSADRAIN, self._old_termios)
        self._fd = None
        self._old_termios = None

    def _restore_windows_console_mode(self) -> None:
        if self._windows_console_mode is None:
            return
        handle, mode = self._windows_console_mode
        self._windows_console_mode = None
        if os.name != "nt":
            return
        with contextlib.suppress(Exception):
            windll = getattr(ctypes, "windll", None)
            if windll is None:
                return
            windll.kernel32.SetConsoleMode(handle, mode)


def flush(
    buffer: ScreenBuffer,
    output: TextIO = sys.stdout,
    styles: Optional[dict[str, str]] = None,
) -> None:
    """Flush ``buffer`` to ``output`` without managing terminal state.

    Use :class:`TerminalSession` or :meth:`App.session` for real interactive
    dashboards. This helper is useful for tests, demos, or callers that already
    manage terminal state themselves.
    """
    output.write(buffer.render_ansi(styles))
    output.flush()


def _style_sequence(style: str, theme: dict[str, str]) -> str:
    code = theme.get(style, theme["normal"])
    if code == "0":
        return f"{CSI}0m"
    return f"{CSI}0;{code}m"


def _enable_windows_vt_mode() -> Optional[tuple[Any, int]]:
    if os.name != "nt":
        return None
    with contextlib.suppress(Exception):
        windll = getattr(ctypes, "windll", None)
        if windll is None:
            return None
        kernel32 = windll.kernel32
        handle = kernel32.GetStdHandle(-11)
        mode = ctypes.c_ulong()
        if kernel32.GetConsoleMode(handle, ctypes.byref(mode)) == 0:
            return None
        kernel32.SetConsoleMode(handle, mode.value | 0x0004)
        return handle, mode.value
    return None


__all__ = [
    "App",
    "Cell",
    "Column",
    "DEFAULT_STYLES",
    "DataTable",
    "ESC",
    "HBox",
    "Icons",
    "LogView",
    "Painter",
    "Panel",
    "Property",
    "PropertyGrid",
    "Rect",
    "RenderContext",
    "ScreenBuffer",
    "Size",
    "TerminalSession",
    "Text",
    "TreeView",
    "VBox",
    "Widget",
    "__version__",
    "align_text",
    "cell_width",
    "clip_cells",
    "CSI",
    "display_width",
    "flush",
    "iter_clusters",
    "normalize_key",
    "terminal_size",
]
