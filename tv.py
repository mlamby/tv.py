"""Small single-file terminal dashboard framework.

Copyright (c) 2026 Michael Lamb
SPDX-License-Identifier: MIT

This module is intentionally kept as the copyable framework file.
"""

from __future__ import annotations

import contextlib
import ctypes
import fnmatch
import importlib
import importlib.util
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

Accessor = Union[str, Callable[[Any], Any]]
Style = Union[str, Callable[[Any], str]]
TextStyle = Union[str, Callable[[], str]]
PropertySpec = Union["Property", "PropertyPattern"]
PropertyPatternFormatter = Callable[["PathMatch"], str]
PropertyPatternStyle = Union[str, Callable[["PathMatch"], str]]


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

EMOJI_SUPPORT_ERROR = (
    "Emoji support requires optional dependencies: pip install regex wcwidth"
)

_emoji_support_enabled = False
_emoji_findall: Optional[Callable[[str, str], list[str]]] = None
_emoji_wcswidth: Optional[Callable[[str], int]] = None


def enable_emoji_support(enabled: bool = True) -> None:
    """Enable or disable optional emoji-aware text measurement.

    Emoji support uses optional runtime dependencies for full grapheme cluster
    splitting and terminal cell width measurement. Default behavior remains
    dependency-free until this function is called with ``enabled=True``.
    """
    global _emoji_findall, _emoji_support_enabled, _emoji_wcswidth
    if not enabled:
        _emoji_support_enabled = False
        return
    try:
        regex_module = importlib.import_module("regex")
        wcwidth_module = importlib.import_module("wcwidth")
    except ImportError as exc:
        raise RuntimeError(_emoji_support_error_message()) from exc
    _emoji_findall = cast(Callable[[str, str], list[str]], regex_module.findall)
    _emoji_wcswidth = cast(Callable[[str], int], wcwidth_module.wcswidth)
    _emoji_support_enabled = True


def _emoji_support_error_message() -> str:
    if importlib.util.find_spec("pip") is None:
        return (
            f"{EMOJI_SUPPORT_ERROR}. Current interpreter: {sys.executable}. "
            "Install the packages into this interpreter, or recreate the virtual "
            "environment so its python and pip commands match."
        )
    return (
        f"{EMOJI_SUPPORT_ERROR}. Install them for this interpreter with: "
        f"{sys.executable} -m pip install regex wcwidth"
    )


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
    if _emoji_support_enabled and _emoji_wcswidth is not None:
        return max(0, _emoji_wcswidth(text))
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
    if _emoji_support_enabled and _emoji_findall is not None:
        yield from _emoji_findall(r"\X", text)
        return
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
            self._clear_wide_overlaps(row, x_start, x_end - x_start, style)
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
            self._clear_wide_overlaps(y, col, width, style)
            self._cells[y][col] = Cell(cluster, style)
            for offset in range(1, width):
                self._cells[y][col + offset] = Cell("", style)
            col += width

    def _clear_wide_overlaps(
        self,
        y: int,
        x: int,
        width: int,
        style: str = "normal",
    ) -> None:
        """Clear stale wide-glyph halves touched by a pending draw.

        Double-width glyphs occupy a leading cell plus an empty continuation
        cell. Before overwriting any part of that pair, both cells must become
        ordinary spaces so later drawing cannot leave a dangling half behind.
        """
        if width <= 0:
            return

        def clear_cluster(left: int) -> None:
            if not 0 <= left < self.width:
                return
            width = max(1, cell_width(self._cells[y][left].char))
            for offset in range(width):
                if 0 <= left + offset < self.width:
                    self._cells[y][left + offset] = Cell(" ", style)

        def leading_cell(col: int) -> int:
            while col > 0 and self._cells[y][col].char == "":
                col -= 1
            return col

        x_start = max(0, x)
        x_end = min(self.width, x + width)
        if x_start >= x_end:
            return
        if x_start > 0 and self._cells[y][x_start].char == "":
            clear_cluster(leading_cell(x_start))
        for col in range(x_start, x_end):
            cell = self._cells[y][col]
            if cell.char == "":
                clear_cluster(leading_cell(col))
            elif cell_width(cell.char) > 1:
                clear_cluster(col)

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
        x_start = max(0, x)
        y_start = max(0, y)
        x_end = min(self.width, x + width)
        y_end = min(self.height, y + height)
        child_x = self.x + x_start
        child_y = self.y + y_start
        child_width = max(0, x_end - x_start)
        child_height = max(0, y_end - y_start)
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
        style: Symbolic style name or zero-argument callable returning the
            current style for every rendered line.
    """

    def __init__(
        self, text: Union[str, Callable[[], Any]], style: TextStyle = "normal"
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
        style = self._style()
        for y, line in enumerate(self._lines()[: painter.height]):
            painter.write(0, y, line, style, width=painter.width)

    def _lines(self) -> list[str]:
        value = self.text() if callable(self.text) else self.text
        return str(value).splitlines() or [""]

    def _style(self) -> str:
        return self.style() if callable(self.style) else self.style


class PathAccessor:
    """Callable accessor for nested mapping/object/list values.

    Args:
        expression: Dotted field path with optional list indexes, such as
            ``"status.overall_health"`` or ``"sensors[0].quality.hdop"``.
        default: Value returned when the path cannot be resolved.
        transform: Optional callable applied to the resolved value.

    Path segments resolve mappings by key first, then object attributes. Index
    segments require a sequence-like value that supports integer indexing.
    """

    def __init__(
        self,
        expression: str,
        default: Any = "",
        transform: Optional[Callable[[Any], Any]] = None,
    ) -> None:
        self.expression = expression
        self.default = default
        self.transform = transform
        self._parts = _parse_path_expression(expression)

    def __call__(self, source: Any) -> Any:
        value = self._resolve(source)
        if self.transform:
            return self.transform(value)
        return value

    def _resolve(self, source: Any) -> Any:
        value = source
        for kind, part in self._parts:
            if value is None:
                return self.default
            if kind == "field":
                value = _get_value(value, cast(str, part), _MISSING)
                if value is _MISSING:
                    return self.default
                continue
            try:
                value = value[cast(int, part)]
            except (IndexError, KeyError, TypeError):
                return self.default
        return value


def path(
    expression: str,
    default: Any = "",
    transform: Optional[Callable[[Any], Any]] = None,
) -> PathAccessor:
    """Return a callable accessor for a nested field path.

    The returned object can be used anywhere a widget accepts an accessor, for
    example in :class:`Column`, :class:`Property`, or application-provided
    status text callbacks.
    """
    return PathAccessor(expression, default, transform)


@dataclass
class Property:
    """Descriptor for one :class:`PropertyGrid` row.

    Attributes:
        label: Label rendered in the left column.
        value: Attribute/key name or callable used to read from the grid source.
        align: Alignment for the value column.
        formatter: Optional function that converts the raw value to text.
        style: Style name or callable receiving the raw value and returning a
            style name.
    """

    label: str
    value: Accessor
    align: str = "left"
    formatter: Optional[Callable[[Any], str]] = None
    style: Style = "normal"


@dataclass
class PropertyPattern:
    """Descriptor for generated :class:`PropertyGrid` rows.

    Attributes:
        pattern: Dotted path pattern. Field segments support ``*``, ``?``,
            bracket character classes, and recursive ``**`` path segments.
            Sequence indexes support ``[*]`` wildcard expansion.
        label: Label mode for generated rows: ``"relative"``, ``"full"``, or
            ``"leaf"``.
        align: Alignment for generated value cells.
        formatter: Optional function receiving a :class:`PathMatch` and
            returning display text.
        style: Style name or callable receiving each match and returning a
            style name.
        sort: When true, generated rows are sorted by resolved path for stable
            rendering.
    """

    pattern: str
    label: str = "relative"
    align: str = "left"
    formatter: Optional[PropertyPatternFormatter] = None
    style: PropertyPatternStyle = "normal"
    sort: bool = True


@dataclass
class _PropertyRow:
    label: str
    raw: Any
    align: str
    formatter: Optional[Callable[[], str]]
    style: Union[str, Callable[[], str]]


def _property_formatter_callback(
    formatter: Callable[[Any], str],
    raw: Any,
) -> Callable[[], str]:
    def callback() -> str:
        return formatter(raw)

    return callback


def _property_style_callback(
    style: Callable[[Any], str],
    raw: Any,
) -> Callable[[], str]:
    def callback() -> str:
        return style(raw)

    return callback


def _pattern_formatter_callback(
    formatter: PropertyPatternFormatter,
    match: "PathMatch",
) -> Callable[[], str]:
    def callback() -> str:
        return formatter(match)

    return callback


def _pattern_style_callback(
    style: Callable[["PathMatch"], str],
    match: "PathMatch",
) -> Callable[[], str]:
    def callback() -> str:
        return style(match)

    return callback


class PropertyGrid(Widget):
    """Display key/value properties for one application object.

    Args:
        source: Object or dictionary read by property descriptors, or a
            zero-argument callable that returns one. It may be replaced by the
            application between renders.
        properties: Ordered list of :class:`Property` and
            :class:`PropertyPattern` descriptors.
        label_width: Optional fixed label column width. When omitted, the
            widest label determines the width.
    """

    def __init__(
        self,
        source: Any = None,
        properties: Optional[list[PropertySpec]] = None,
        label_width: Optional[int] = None,
    ) -> None:
        self.source = source
        self.properties = properties or []
        self.label_width = label_width

    def preferred_size(self, axis: str) -> int:
        rows = self._property_rows(self._source())
        if axis == "vertical":
            return max(1, len(rows))
        labels = [display_width(row.label) for row in rows]
        return (max(labels) if labels else 8) + 12

    def render(self, painter: Painter, context: RenderContext) -> None:
        del context
        source = self._source()
        rows = self._property_rows(source)
        label_width = self.label_width
        if label_width is None:
            label_width = max(
                (display_width(row.label) for row in rows),
                default=0,
            )
        label_width = min(label_width, max(0, painter.width - 1))
        for y, row in enumerate(rows[: painter.height]):
            value = row.formatter() if row.formatter else str(row.raw)
            style = row.style() if callable(row.style) else row.style
            painter.write(0, y, row.label, "muted", width=label_width)
            if painter.width > label_width:
                painter.write(label_width, y, " ", width=1)
            value_width = max(0, painter.width - label_width - 1)
            painter.write(
                label_width + 1,
                y,
                value,
                style,
                width=value_width,
                align=row.align,
            )

    def _source(self) -> Any:
        return self.source() if callable(self.source) else self.source

    def _property_rows(self, source: Any) -> list[_PropertyRow]:
        rows: list[_PropertyRow] = []
        for prop in self.properties:
            if isinstance(prop, PropertyPattern):
                rows.extend(_expand_property_pattern(source, prop))
                continue
            raw = _resolve_accessor(source, prop.value)
            formatter: Optional[Callable[[], str]] = None
            if prop.formatter:
                formatter = _property_formatter_callback(prop.formatter, raw)
            style: Union[str, Callable[[], str]]
            if callable(prop.style):
                style = _property_style_callback(prop.style, raw)
            else:
                style = prop.style
            rows.append(_PropertyRow(prop.label, raw, prop.align, formatter, style))
        return rows


@dataclass
class Column:
    """Presentation descriptor for a :class:`DataTable` column.

    Attributes:
        title: Header text.
        value: Attribute/key name or callable used to read a row value.
        width: :class:`Size` controlling the column width.
        align: Alignment for header and cell text.
        formatter: Optional function that converts the raw value to text.
        style: Style name or callable receiving the row object and returning a
            style name for this column's cell.
    """

    title: str
    value: Accessor
    width: Size = Size.flex(1)
    align: str = "left"
    formatter: Optional[Callable[[Any], str]] = None
    style: Style = "normal"

    def text_for(self, row: Any) -> str:
        """Return formatted display text for ``row``."""
        raw = _resolve_accessor(row, self.value)
        if self.formatter:
            return self.formatter(raw)
        return str(raw)

    def style_for(self, row: Any) -> str:
        """Return the symbolic style name for ``row``."""
        return _resolve_style(self.style, row)


class DataTable(Widget):
    """Scrollable single-selection table for application rows.

    Args:
        columns: Ordered :class:`Column` descriptors.
        rows: Mutable list of dictionaries/objects owned by the application, or
            a zero-argument callable returning the current list.
        selected_index: Initial selected row index, or ``None`` for no
            selection.

    The table owns selection and scroll state. It handles ``up``, ``down``,
    ``home``, and ``end`` keys when focused.
    """

    focusable = True

    def __init__(
        self,
        columns: list[Column],
        rows: Optional[Union[list[Any], Callable[[], list[Any]]]] = None,
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
        rows = self._rows()
        if 0 <= self.selected_index < len(rows):
            return rows[self.selected_index]
        return None

    def preferred_size(self, axis: str) -> int:
        return 4 if axis == "vertical" else 24

    def render(self, painter: Painter, context: RenderContext) -> None:
        rows = self._rows()
        widths = _allocate_column_widths(painter.width, self.columns, rows)
        x = 0
        for column, width in zip(self.columns, widths):
            painter.write(x, 0, column.title, "title", width=width, align=column.align)
            x += width
        visible_height = max(0, painter.height - 1)
        self._clamp_selection(rows)
        self._ensure_selection_visible(visible_height, rows)
        for screen_y in range(visible_height):
            row_index = self.scroll_offset + screen_y
            if row_index >= len(rows):
                break
            row = rows[row_index]
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
        rows = self._rows()
        if key == "up":
            self._move_selection(-1, rows)
            return True
        if key == "down":
            self._move_selection(1, rows)
            return True
        if key == "home":
            self.selected_index = 0 if rows else None
            self.scroll_offset = 0
            return True
        if key == "end":
            self.selected_index = len(rows) - 1 if rows else None
            return True
        return False

    def _rows(self) -> list[Any]:
        return self.rows() if callable(self.rows) else self.rows

    def _move_selection(self, delta: int, rows: list[Any]) -> None:
        if not rows:
            self.selected_index = None
            return
        index = 0 if self.selected_index is None else self.selected_index
        self.selected_index = _clamp_index(index + delta, len(rows))

    def _clamp_selection(self, rows: list[Any]) -> None:
        if not rows:
            self.selected_index = None
            self.scroll_offset = 0
            return
        if self.selected_index is None:
            return
        self.selected_index = _clamp_index(self.selected_index, len(rows))

    def _ensure_selection_visible(self, visible_height: int, rows: list[Any]) -> None:
        if self.selected_index is None or visible_height <= 0:
            return
        self.scroll_offset = _scroll_offset_for_index(
            self.selected_index,
            self.scroll_offset,
            visible_height,
            len(rows),
        )


class TreeView(Widget):
    """Scrollable tree view for arbitrary application objects.

    Args:
        roots: Optional top-level application objects, or a zero-argument
            callable returning the current top-level objects.
        id: Optional attribute/key name or callable returning a stable identity
            for a node. Stable IDs preserve expansion state across object
            refreshes.
        label: Optional attribute/key name or callable returning display text
            for a node.
        children: Optional attribute/key name or callable returning a node's
            child list.
        style: Style name or callable receiving a node and returning a style
            name for unselected rows.

    The tree owns selection, scroll offset, and expanded node IDs. It handles
    arrow-key navigation plus ``enter``/``right`` to expand and ``left`` to
    collapse.
    """

    focusable = True

    def __init__(
        self,
        roots: Optional[Union[list[Any], Callable[[], list[Any]]]] = None,
        id: Optional[Accessor] = None,
        label: Optional[Accessor] = None,
        children: Optional[Accessor] = None,
        style: Style = "normal",
    ) -> None:
        self.roots = roots if roots is not None else []
        self.id: Accessor = id or builtins_id
        self.label: Accessor = label or str
        self.children: Accessor = children or _empty_children
        self.style = style
        self.expanded_ids: set[Any] = set()
        self.selected_index = 0
        self.scroll_offset = 0

    @property
    def selected_node(self) -> Any:
        """Return the selected visible node, or ``None`` when the tree is empty."""
        visible = self._visible_nodes()
        if not visible:
            return None
        self.selected_index = _clamp_index(self.selected_index, len(visible))
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
            node_id = self._id_for(node)
            kids = self._children_for(node)
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
                else self._style_for(node)
            )
            painter.write(
                0,
                screen_y,
                f"{prefix}{marker} {self._label_for(node)}",
                style,
                width=painter.width,
            )

    def handle_key(self, key: str) -> bool:
        visible = self._visible_nodes()
        if key == "up":
            self.selected_index = _clamp_index(self.selected_index - 1, len(visible))
            return True
        if key == "down":
            self.selected_index = _clamp_index(self.selected_index + 1, len(visible))
            return True
        if key in {"right", "enter"}:
            node = self.selected_node
            if node is not None and self._children_for(node):
                self.expanded_ids.add(self._id_for(node))
                return True
        if key == "left":
            node = self.selected_node
            if node is not None:
                node_id = self._id_for(node)
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
                node_id = self._id_for(node)
                if node_id in self.expanded_ids:
                    visit(
                        self._children_for(node),
                        depth + 1,
                        [*ancestors_last, is_last],
                    )

        visit(self._roots(), 0, [])
        return rows

    def _roots(self) -> list[Any]:
        return self.roots() if callable(self.roots) else self.roots

    def _id_for(self, node: Any) -> Any:
        return _resolve_accessor(node, self.id)

    def _label_for(self, node: Any) -> str:
        return str(_resolve_accessor(node, self.label))

    def _children_for(self, node: Any) -> list[Any]:
        children = _resolve_accessor(node, self.children)
        if not children:
            return []
        return cast(list[Any], children)

    def _style_for(self, node: Any) -> str:
        return _resolve_style(self.style, node)

    def _clamp(
        self,
        visible: list[tuple[Any, int, bool, list[bool]]],
        height: int,
    ) -> None:
        if not visible:
            self.selected_index = 0
            self.scroll_offset = 0
            return
        self.selected_index = _clamp_index(self.selected_index, len(visible))
        self.scroll_offset = _scroll_offset_for_index(
            self.selected_index,
            self.scroll_offset,
            height,
            len(visible),
        )


class LogView(Widget):
    """Append-only log viewer with follow-tail behavior.

    Args:
        entries: Mutable list of log entry objects owned by the application.
        text: Optional attribute/key name or callable converting an entry to
            display text.
        style: Style name or callable returning a symbolic style name for an
            entry.

    The view follows the end by default. Pressing ``up`` enters scrollback mode;
    pressing ``down`` to the bottom or ``end`` resumes following.
    """

    focusable = True

    def __init__(
        self,
        entries: Optional[list[Any]] = None,
        text: Optional[Accessor] = None,
        style: Style = "normal",
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
            style = _resolve_style(self.style, entry)
            text = str(_resolve_accessor(entry, self.text))
            painter.write(0, screen_y, text, style, width=painter.width)

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


def _clamp_index(index: int, count: int) -> int:
    if count <= 0:
        return 0
    return min(max(0, index), count - 1)


def _scroll_offset_for_index(
    index: int,
    scroll_offset: int,
    visible_height: int,
    count: int,
) -> int:
    if visible_height <= 0:
        return scroll_offset
    if index < scroll_offset:
        scroll_offset = index
    if index >= scroll_offset + visible_height:
        scroll_offset = index - visible_height + 1
    max_offset = max(0, count - visible_height)
    return max(0, min(scroll_offset, max_offset))


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


_MISSING = object()


@dataclass(frozen=True)
class _PatternIndex:
    value: Optional[int] = None
    wildcard: bool = False


@dataclass(frozen=True)
class _PatternSegment:
    field: Optional[str]
    indexes: tuple[_PatternIndex, ...] = ()
    recursive: bool = False


@dataclass(frozen=True)
class PathMatch:
    """One value matched by :func:`match_paths`.

    Attributes:
        path: Resolved dotted path without a root marker.
        value: Matched value.
        name: Final field name or index label.
        type_name: Python type name for ``value``.
    """

    path: str
    value: Any
    name: str
    type_name: str


def iter_path_children(source: Any, *, prefix: str = "") -> list[PathMatch]:
    """Return immediate children of ``source`` using path traversal semantics."""

    return [
        PathMatch(
            _prefix_path(prefix, name),
            value,
            name,
            type(value).__name__,
        )
        for name, value in _iter_child_values(source)
    ]


def _expand_property_pattern(source: Any, prop: PropertyPattern) -> list[_PropertyRow]:
    matches = match_paths(
        source,
        prop.pattern,
        leaves_only=False,
        sort=prop.sort,
    )
    prefix = _literal_prefix(_parse_path_pattern(prop.pattern))
    rows: list[_PropertyRow] = []
    for match in matches:
        formatter: Optional[Callable[[], str]] = None
        if prop.formatter:
            formatter = _pattern_formatter_callback(prop.formatter, match)
        style: Union[str, Callable[[], str]]
        if callable(prop.style):
            style = _pattern_style_callback(prop.style, match)
        else:
            style = prop.style
        rows.append(
            _PropertyRow(
                _property_pattern_label(match, prop.label, prefix),
                match.value,
                prop.align,
                formatter,
                style,
            )
        )
    return rows


def _property_pattern_label(match: PathMatch, mode: str, prefix: str) -> str:
    if mode == "full":
        return match.path
    if mode == "leaf":
        return match.name
    if prefix == "":
        return match.path
    if match.path == prefix:
        return match.name
    if match.path.startswith(prefix + "."):
        return match.path[len(prefix) + 1 :]
    if match.path.startswith(prefix + "["):
        return match.path[len(prefix) :]
    return match.path


def match_paths(
    source: Any,
    pattern: str = "**",
    *,
    leaves_only: bool = True,
    sort: bool = True,
    prefix: str = "",
) -> list[PathMatch]:
    """Return values from ``source`` whose paths match ``pattern``.

    ``pattern`` uses the same dotted path syntax as :class:`PropertyPattern`.
    By default only terminal values are returned, which is useful for building
    leaf-field tables from nested telemetry objects. ``prefix`` mounts returned
    paths below a display path without changing the matched value.
    """

    raw_matches = _match_path_pattern(source, pattern)
    matches = [
        PathMatch(
            _prefix_path(prefix, raw.path),
            raw.value,
            _path_leaf(raw.path),
            type(raw.value).__name__,
        )
        for raw in raw_matches
        if not leaves_only or _is_match_leaf(raw.value)
    ]
    if sort:
        matches.sort(key=lambda match: match.path)
    return matches


@dataclass(frozen=True)
class _RawPathMatch:
    path: str
    value: Any


def _path_leaf(path_value: str) -> str:
    if path_value.endswith("]"):
        bracket = path_value.rfind("[")
        if bracket != -1:
            return path_value[bracket:]
    dot = path_value.rfind(".")
    if dot != -1:
        return path_value[dot + 1 :]
    return path_value


def _match_path_pattern(source: Any, pattern: str) -> list[_RawPathMatch]:
    segments = _parse_path_pattern(pattern)
    matches: list[_RawPathMatch] = []
    seen: set[tuple[int, int]] = set()
    _match_pattern_segments(
        source,
        segments,
        0,
        "",
        matches,
        seen,
    )
    return matches


def _match_pattern_segments(
    value: Any,
    segments: list[_PatternSegment],
    index: int,
    path_value: str,
    matches: list[_RawPathMatch],
    seen: set[tuple[int, int]],
) -> None:
    if index == len(segments):
        matches.append(_RawPathMatch(path_value, value))
        return
    segment = segments[index]
    if segment.recursive:
        _match_pattern_segments(
            value,
            segments,
            index + 1,
            path_value,
            matches,
            seen,
        )
        identity = (id(value), index)
        if identity in seen:
            return
        seen.add(identity)
        for child in iter_path_children(value, prefix=path_value):
            _match_pattern_segments(
                child.value,
                segments,
                index,
                child.path,
                matches,
                seen,
            )
        return
    for next_path, next_value in _match_pattern_segment(value, path_value, segment):
        _match_pattern_segments(
            next_value,
            segments,
            index + 1,
            next_path,
            matches,
            seen,
        )


def _match_pattern_segment(
    value: Any,
    path_value: str,
    segment: _PatternSegment,
) -> list[tuple[str, Any]]:
    candidates: list[tuple[str, Any]]
    if segment.field is None:
        candidates = [(path_value, value)]
    elif _is_leaf_value(value):
        return []
    elif _has_glob(segment.field):
        candidates = [
            (child.path, child.value)
            for child in iter_path_children(value, prefix=path_value)
            if not child.name.startswith("[")
            and fnmatch.fnmatchcase(child.name, segment.field)
        ]
    else:
        child = _get_value(value, segment.field, _MISSING)
        if child is _MISSING:
            return []
        candidates = [(_join_path(path_value, segment.field), child)]
    for pattern_index in segment.indexes:
        next_candidates: list[tuple[str, Any]] = []
        for candidate_path, candidate_value in candidates:
            if pattern_index.wildcard:
                for item_index, item_value in _iter_index_values(candidate_value):
                    next_candidates.append(
                        (f"{candidate_path}[{item_index}]", item_value)
                    )
                continue
            try:
                item_value = candidate_value[cast(int, pattern_index.value)]
            except (IndexError, KeyError, TypeError):
                continue
            next_candidates.append(
                (f"{candidate_path}[{pattern_index.value}]", item_value)
            )
        candidates = next_candidates
    return candidates


def _parse_path_pattern(pattern: str) -> list[_PatternSegment]:
    if pattern == "":
        return []
    segments: list[_PatternSegment] = []
    for raw_segment in pattern.split("."):
        if raw_segment == "":
            raise ValueError(f"Invalid path pattern: {pattern!r}")
        if raw_segment == "**":
            segments.append(_PatternSegment(None, recursive=True))
            continue
        field, indexes = _parse_pattern_segment(raw_segment, pattern)
        segments.append(_PatternSegment(field, tuple(indexes)))
    return segments


def _parse_pattern_segment(
    segment: str,
    pattern: str,
) -> tuple[Optional[str], list[_PatternIndex]]:
    indexes: list[_PatternIndex] = []
    bracket = segment.find("[")
    if bracket == -1:
        return segment, indexes
    field = segment[:bracket] or None
    cursor = bracket
    while cursor < len(segment):
        if segment[cursor] != "[":
            raise ValueError(f"Invalid path pattern: {pattern!r}")
        end = segment.find("]", cursor + 1)
        if end == -1:
            raise ValueError(f"Invalid path pattern: {pattern!r}")
        raw_index = segment[cursor + 1 : end]
        if raw_index == "*":
            indexes.append(_PatternIndex(wildcard=True))
        elif raw_index.isdigit():
            indexes.append(_PatternIndex(int(raw_index)))
        else:
            return segment, indexes
        cursor = end + 1
        if cursor < len(segment) and segment[cursor] != "[":
            return segment, []
    return field, indexes


def _literal_prefix(segments: list[_PatternSegment]) -> str:
    prefix = ""
    for segment in segments:
        if segment.recursive:
            break
        if segment.field is not None and _has_glob(segment.field):
            break
        if segment.field is not None:
            prefix = _join_path(prefix, segment.field)
        for index in segment.indexes:
            if index.wildcard:
                return prefix
            prefix = f"{prefix}[{index.value}]"
    return prefix


def _has_glob(value: str) -> bool:
    return any(char in value for char in "*?[")


def _iter_child_values(source: Any) -> Iterator[tuple[str, Any]]:
    yield from _iter_named_values(source)
    yield from (
        (f"[{index}]", value) for index, value in _iter_index_values(source)
    )


def _iter_named_values(source: Any) -> Iterator[tuple[str, Any]]:
    if _is_leaf_value(source):
        return
    if isinstance(source, Mapping):
        for key, value in source.items():
            if isinstance(key, str):
                yield key, value
        return
    for name in dir(source):
        if name.startswith("_"):
            continue
        try:
            value = getattr(source, name)
        except Exception:
            continue
        if callable(value):
            continue
        yield name, value


def _iter_index_values(source: Any) -> Iterator[tuple[int, Any]]:
    if _is_leaf_value(source) or isinstance(source, Mapping):
        return
    try:
        length = len(source)
    except TypeError:
        return
    for index in range(length):
        try:
            yield index, source[index]
        except (IndexError, KeyError, TypeError):
            continue


def _join_path(prefix: str, part: str) -> str:
    if part.startswith("["):
        return f"{prefix}{part}"
    if prefix:
        return f"{prefix}.{part}"
    return part


def _prefix_path(prefix: str, path_value: str) -> str:
    if path_value == "":
        return prefix
    if path_value.startswith("["):
        return f"{prefix}{path_value}"
    if prefix:
        return f"{prefix}.{path_value}"
    return path_value


def _is_leaf_value(source: Any) -> bool:
    return source is None or isinstance(
        source,
        (str, bytes, bytearray, bool, int, float, complex),
    )


def _is_match_leaf(source: Any) -> bool:
    if _is_leaf_value(source):
        return True
    try:
        next(_iter_child_values(source))
    except StopIteration:
        return True
    return False


def _parse_path_expression(expression: str) -> list[tuple[str, Union[str, int]]]:
    if expression == "":
        return []
    parts: list[tuple[str, Union[str, int]]] = []
    for segment in expression.split("."):
        if segment == "":
            raise ValueError(f"Invalid path expression: {expression!r}")
        cursor = 0
        bracket = segment.find("[")
        if bracket == -1:
            parts.append(("field", segment))
            continue
        if bracket > 0:
            parts.append(("field", segment[:bracket]))
            cursor = bracket
        while cursor < len(segment):
            if segment[cursor] != "[":
                raise ValueError(f"Invalid path expression: {expression!r}")
            end = segment.find("]", cursor + 1)
            if end == -1:
                raise ValueError(f"Invalid path expression: {expression!r}")
            raw_index = segment[cursor + 1 : end]
            if not raw_index.isdigit():
                raise ValueError(f"Invalid path expression: {expression!r}")
            parts.append(("index", int(raw_index)))
            cursor = end + 1
    return parts


def _get_value(source: Any, name: str, default: Any = "") -> Any:
    if source is None:
        return default
    if isinstance(source, Mapping):
        mapping = cast(Mapping[str, Any], source)
        return mapping.get(name, default)
    return getattr(source, name, default)


def _resolve_accessor(source: Any, accessor: Accessor, default: Any = "") -> Any:
    if callable(accessor):
        return accessor(source)
    return _get_value(source, accessor, default)


def _resolve_style(style: Style, source: Any) -> str:
    if callable(style):
        return style(source)
    return style


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
    key = _poll_console_input_key_windows()
    if key is not None:
        return key
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


def _poll_console_input_key_windows() -> Optional[str]:
    if os.name != "nt":
        return None
    with contextlib.suppress(Exception):
        windll = getattr(ctypes, "windll", None)
        if windll is None:
            return None
        kernel32 = windll.kernel32
        handle = kernel32.GetStdHandle(-10)
        pending = ctypes.c_ulong()
        if kernel32.GetNumberOfConsoleInputEvents(handle, ctypes.byref(pending)) == 0:
            return None
        while pending.value:
            record = _WindowsInputRecord()
            read = ctypes.c_ulong()
            if (
                kernel32.ReadConsoleInputW(
                    handle,
                    ctypes.byref(record),
                    1,
                    ctypes.byref(read),
                )
                == 0
            ):
                return None
            if read.value == 0:
                return None
            pending.value -= 1
            if record.EventType != 0x0001:
                continue
            event = record.Event.KeyEvent
            if not event.bKeyDown:
                continue
            key = _windows_key_name(
                event.uChar.UnicodeChar,
                event.wVirtualKeyCode,
                event.dwControlKeyState,
            )
            if key is not None:
                return key
    return None


def _windows_key_name(
    char: str,
    virtual_key: int,
    control_state: int,
) -> Optional[str]:
    left_alt_pressed = 0x0002
    right_alt_pressed = 0x0001
    shift_pressed = 0x0010
    virtual_key_tab = 0x09

    alt = bool(control_state & (left_alt_pressed | right_alt_pressed))
    shift = bool(control_state & shift_pressed)
    named = {
        0x26: "up",
        0x28: "down",
        0x25: "left",
        0x27: "right",
        0x24: "home",
        0x23: "end",
        0x21: "pageup",
        0x22: "pagedown",
        0x2E: "delete",
        0x08: "backspace",
        0x0D: "enter",
        0x1B: "escape",
    }.get(virtual_key)
    if virtual_key == virtual_key_tab:
        named = "shift+tab" if shift else "tab"
    if named is not None:
        return f"alt+{named}" if alt else named
    if char == "\x03":
        return "ctrl+c"
    if char and char.isprintable():
        key = char.lower()
        return f"alt+{key}" if alt else key
    return None


class _WindowsCharUnion(ctypes.Union):
    _fields_ = [
        ("UnicodeChar", ctypes.c_wchar),
        ("AsciiChar", ctypes.c_char),
    ]


class _WindowsKeyEventRecord(ctypes.Structure):
    _fields_ = [
        ("bKeyDown", ctypes.c_int),
        ("wRepeatCount", ctypes.c_ushort),
        ("wVirtualKeyCode", ctypes.c_ushort),
        ("wVirtualScanCode", ctypes.c_ushort),
        ("uChar", _WindowsCharUnion),
        ("dwControlKeyState", ctypes.c_ulong),
    ]


class _WindowsEventUnion(ctypes.Union):
    _fields_ = [
        ("KeyEvent", _WindowsKeyEventRecord),
    ]


class _WindowsInputRecord(ctypes.Structure):
    _fields_ = [
        ("EventType", ctypes.c_ushort),
        ("Event", _WindowsEventUnion),
    ]


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
    "PathMatch",
    "Property",
    "PropertyPattern",
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
    "enable_emoji_support",
    "flush",
    "iter_path_children",
    "iter_clusters",
    "match_paths",
    "normalize_key",
    "path",
    "terminal_size",
]
