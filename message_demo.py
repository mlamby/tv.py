"""Single-message binding exploration demo for tv.py.

This demo keeps the framework API unchanged. It shows the current application
pattern for a large nested telemetry message: receive a message, derive widget
view models from it, point widgets at the new data, then render.
"""

from __future__ import annotations

import random
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

import tv

KNOTS_PER_METER_PER_SECOND = 1.9438444924406


@dataclass
class FieldNode:
    path: str
    name: str
    type_name: str
    size: int
    value: Any
    children: list["FieldNode"] = field(default_factory=list)


@dataclass
class LeafField:
    path: str
    type_name: str
    value: Any


@dataclass
class MessageState:
    message: dict[str, Any]
    received_at: float
    sequence: int = 0

    def accept(self, message: dict[str, Any]) -> None:
        self.message = message
        self.received_at = time.time()
        self.sequence += 1

    @property
    def received_time(self) -> str:
        received = datetime.fromtimestamp(self.received_at, timezone.utc).astimezone()
        return received.strftime("%H:%M:%S")

    @property
    def age_seconds(self) -> float:
        return max(0.0, time.time() - self.received_at)

    @property
    def leaf_count(self) -> int:
        return len(iter_leaf_fields(self.message))

    @property
    def branch_count(self) -> int:
        return count_nodes(build_tree_roots(self.message))


@dataclass
class MessageWidgets:
    status: tv.Text
    tree: tv.TreeView
    leaves: tv.DataTable
    details: tv.PropertyGrid


MESSAGE_ID = tv.path("header.message_id")
SOURCE = tv.path("header.source")
HEALTH = tv.path("status.overall_health", default="unknown")
SPEED_MS = tv.path("navigation.speed_ms", default=0.0, transform=float)
SPEED_KNOTS = tv.path(
    "navigation.speed_ms",
    default=0.0,
    transform=lambda value: float(value) * KNOTS_PER_METER_PER_SECOND,
)
STATE_MESSAGE_ID = tv.path("message.header.message_id")
STATE_SOURCE = tv.path("message.header.source")
STATE_HEALTH = tv.path("message.status.overall_health", default="unknown")
STATE_SPEED_MS = tv.path("message.navigation.speed_ms", default=0.0, transform=float)
STATE_SPEED_KNOTS = tv.path(
    "message.navigation.speed_ms",
    default=0.0,
    transform=lambda value: float(value) * KNOTS_PER_METER_PER_SECOND,
)


def make_message(sequence: int) -> dict[str, Any]:
    speed_ms = 9.8 + random.uniform(-0.7, 0.7)
    battery_percent = max(0.0, min(100.0, 82.0 + random.uniform(-4.0, 4.0)))
    cpu_temp_c = 51.0 + random.uniform(-6.0, 12.0)
    health = "ok"
    if battery_percent < 25.0 or cpu_temp_c > 82.0:
        health = "error"
    elif battery_percent < 45.0 or cpu_temp_c > 70.0:
        health = "warning"

    return {
        "header": {
            "message_id": f"nav-{sequence:06d}",
            "source": "bridge.telemetry",
            "schema": "vessel.navigation.v2",
        },
        "status": {
            "overall_health": health,
            "battery_percent": round(battery_percent, 1),
            "faults": [] if health == "ok" else ["thermal_margin"],
        },
        "navigation": {
            "speed_ms": round(speed_ms, 3),
            "heading_deg": round(184.0 + random.uniform(-2.0, 2.0), 2),
            "position": {
                "lat": round(-33.8568 + random.uniform(-0.0005, 0.0005), 6),
                "lon": round(151.2153 + random.uniform(-0.0005, 0.0005), 6),
                "alt_m": round(4.0 + random.uniform(-0.2, 0.2), 2),
            },
        },
        "power": {
            "battery": {
                "voltage": round(47.8 + random.uniform(-0.4, 0.4), 2),
                "current_a": round(18.2 + random.uniform(-2.0, 2.0), 2),
                "percent": round(battery_percent, 1),
            },
            "bus": {
                "voltage": 24.1,
                "current_a": round(7.3 + random.uniform(-1.0, 1.0), 2),
            },
        },
        "compute": {
            "cpu": {
                "load": round(random.uniform(0.35, 0.92), 2),
                "temp_c": round(cpu_temp_c, 1),
            },
            "memory": {"used_mb": random.randint(2100, 2800), "total_mb": 4096},
        },
        "sensors": [
            {
                "name": "imu",
                "online": True,
                "sample_hz": 200,
                "quality": {
                    "dropouts": random.randint(0, 3),
                    "jitter_ms": round(random.uniform(0.2, 1.5), 2),
                },
            },
            {
                "name": "gps",
                "online": health != "error",
                "sample_hz": 10,
                "quality": {
                    "satellites": random.randint(10, 15),
                    "hdop": round(random.uniform(0.7, 1.6), 2),
                },
            },
        ],
    }


def create_state() -> MessageState:
    state = MessageState({}, time.time())
    state.accept(make_message(1))
    return state


def create_widgets(state: MessageState) -> MessageWidgets:
    status = tv.Text(lambda: status_line(state), style="normal")
    tree = tv.TreeView(
        lambda: build_tree_roots(state.message),
        id="path",
        label=lambda node: f"{node.name} ({node.type_name}, {node.size})",
        children="children",
    )
    tree.expanded_ids.update({"$", "$.navigation", "$.power", "$.compute", "$.sensors"})

    leaves = tv.DataTable(
        columns=[
            tv.Column("Path", "path", tv.Size.flex(3)),
            tv.Column("Type", "type_name", tv.Size.fixed(9)),
            tv.Column(
                "Value",
                "value",
                tv.Size.flex(2),
                formatter=format_value,
            ),
        ],
        rows=lambda: leaves_for_selection(state, tree),
    )

    details = tv.PropertyGrid(
        state,
        [
            tv.Property("Message", STATE_MESSAGE_ID),
            tv.Property("Source", STATE_SOURCE),
            tv.Property("Sequence", "sequence", align="right"),
            tv.Property("Received", "received_time"),
            tv.Property("Age", "age_seconds", align="right", formatter=format_age),
            tv.Property(
                "Health",
                STATE_HEALTH,
                style=health_style,
            ),
            tv.Property(
                "Speed m/s",
                STATE_SPEED_MS,
                align="right",
                formatter=format_speed_ms,
            ),
            tv.Property(
                "Speed knots",
                STATE_SPEED_KNOTS,
                align="right",
                formatter=format_knots,
            ),
            tv.Property("Branches", "branch_count", align="right"),
            tv.Property("Leaf fields", "leaf_count", align="right"),
        ],
    )
    return MessageWidgets(status, tree, leaves, details)


def create_layout(app: tv.App, widgets: MessageWidgets) -> None:
    with app.screen("message") as screen:  # noqa: SIM117
        with screen.vbox() as root:
            root.panel(widgets.status, tv.Size.fixed(1), border=False)
            with root.hbox(tv.Size.flex(1)) as body:
                body.panel(widgets.tree, tv.Size.flex(2), title="Message Fields")
                with body.vbox(tv.Size.flex(3)) as right:
                    right.panel(widgets.leaves, tv.Size.flex(1), title="Leaf Values")
                    right.panel(
                        widgets.details,
                        tv.Size.fixed(12),
                        title="Message Details",
                    )


def run_main_loop(app: tv.App, state: MessageState, widgets: MessageWidgets) -> None:
    next_message_at = time.monotonic()
    with app.session():
        while app.running:
            now = time.monotonic()
            if now >= next_message_at:
                state.accept(make_message(state.sequence + 1))
                next_message_at = now + 1.0

            key = app.poll_key()
            if key:
                app.handle_key(key)

            app.render()
            app.sleep_until_next_frame()


def leaves_for_selection(state: MessageState, tree: tv.TreeView) -> list[LeafField]:
    selected = tree.selected_node
    if selected is None:
        return iter_leaf_fields(state.message)
    return iter_leaf_fields(selected.value, selected.path)


def status_line(state: MessageState) -> str:
    return (
        f"Health {str(HEALTH(state.message)).upper()} | "
        f"Speed {float(SPEED_KNOTS(state.message)):5.1f} kn "
        f"({float(SPEED_MS(state.message)):4.1f} m/s) | "
        f"Message {MESSAGE_ID(state.message)} | "
        f"Age {format_age(state.age_seconds)} | "
        "Tab focus | q exits"
    )


def build_tree_roots(message: Any) -> list[FieldNode]:
    root = build_tree_node("$", "message", message)
    return [root] if root is not None else []


def build_tree_node(path: str, name: str, value: Any) -> Optional[FieldNode]:
    children: list[FieldNode] = []
    if isinstance(value, Mapping):
        for key, child_value in value.items():
            child_path = f"{path}.{key}" if path != "$" else f"$.{key}"
            child = build_tree_node(child_path, str(key), child_value)
            if child is not None:
                children.append(child)
        return FieldNode(path, name, "object", len(value), value, children)
    if isinstance(value, list):
        for index, child_value in enumerate(value):
            child = build_tree_node(f"{path}[{index}]", f"[{index}]", child_value)
            if child is not None:
                children.append(child)
        return FieldNode(path, name, "array", len(value), value, children)
    return None


def iter_leaf_fields(value: Any, path: str = "$") -> list[LeafField]:
    leaves: list[LeafField] = []
    if isinstance(value, Mapping):
        for key, child_value in value.items():
            child_path = f"{path}.{key}" if path != "$" else f"$.{key}"
            leaves.extend(iter_leaf_fields(child_value, child_path))
        return leaves
    if isinstance(value, list):
        for index, child_value in enumerate(value):
            leaves.extend(iter_leaf_fields(child_value, f"{path}[{index}]"))
        return leaves
    return [LeafField(path, type(value).__name__, value)]


def count_nodes(nodes: list[FieldNode]) -> int:
    return sum(1 + count_nodes(node.children) for node in nodes)


def format_value(value: Any) -> str:
    if isinstance(value, float):
        return f"{value:.3f}"
    return str(value)


def format_age(value: Any) -> str:
    return f"{float(value):.1f} s"


def format_speed_ms(value: Any) -> str:
    return f"{float(value):.2f}"


def format_knots(value: Any) -> str:
    return f"{float(value):.2f}"


def health_style(value: str) -> str:
    if value == "ok":
        return "ok"
    if value == "warning":
        return "warning"
    if value == "error":
        return "error"
    return "normal"


def main() -> None:
    state = create_state()
    widgets = create_widgets(state)
    app = tv.App(refresh_hz=10)
    create_layout(app, widgets)
    run_main_loop(app, state, widgets)


if __name__ == "__main__":
    main()
