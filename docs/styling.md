# Styling

`tv.py` keeps styling deliberately small. Widgets write semantic style names
such as `"warning"` or `"border"` into the screen buffer. When the buffer is
rendered, those names are translated into ANSI SGR numbers.

The default mapping lives in `DEFAULT_STYLES`:

```python
DEFAULT_STYLES = {
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
```

## Using Styles

Most widget APIs take semantic style names:

```python
Text("waiting for telemetry", style="muted")

Column(
    "Status",
    "status",
    style=lambda row: "error" if row.status == "down" else "ok",
)
```

Prefer adding or reusing semantic names over scattering raw color choices
through app code. That keeps dashboard logic readable and makes themes easy to
change.

## What The Numbers Mean

The values in the style dictionary are ANSI SGR fragments. SGR stands for
Select Graphic Rendition, the part of ANSI terminal control used for colors and
text attributes.

Common attributes:

| Code | Meaning |
| ---: | --- |
| `0` | Reset all styling |
| `1` | Bold or bright |
| `2` | Dim |
| `4` | Underline |
| `7` | Inverse video |

Foreground colors:

| Code | Color |
| ---: | --- |
| `30` | Black |
| `31` | Red |
| `32` | Green |
| `33` | Yellow |
| `34` | Blue |
| `35` | Magenta |
| `36` | Cyan |
| `37` | White |
| `90`-`97` | Bright foreground colors |

Background colors:

| Code | Color |
| ---: | --- |
| `40`-`47` | Background versions of `30`-`37` |
| `100`-`107` | Bright background colors |

Combine codes with semicolons:

```python
"1;36"   # bold cyan
"1;31"   # bold red
"30;47"  # black text on a white background
"7"      # inverse video
```

## Overriding Styles

Applications can pass a style map when rendering a buffer to ANSI. The supplied
values override `DEFAULT_STYLES`:

```python
ansi = buffer.render_ansi(
    {
        "border": "90",
        "focus_border": "1;97",
        "warning": "1;33",
        "error": "1;31",
    }
)
```

If a cell uses a style name that is missing from the map, `tv.py` falls back to
`"normal"`.

Even when `"normal"` maps to a concrete color such as `"37"`, rendered output
ends with a true ANSI reset (`0`) so terminal state is restored for callers that
use `render_ansi()` directly.

## Built-In Semantic Styles

- `normal`: regular dashboard text.
- `muted`: less prominent supporting text.
- `title`: panel titles and table headers.
- `border`: panel borders.
- `focus_border`: panel border when its child has focus.
- `selected`: selected table rows.
- `ok`: healthy or successful status.
- `warning`: degraded status.
- `error`: failed or dangerous status.
