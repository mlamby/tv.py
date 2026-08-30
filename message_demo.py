"""Multi-message binding exploration demo for tv.py.

This demo keeps the framework API unchanged. It shows the current application
pattern for nested telemetry messages: receive messages, derive widget view
models from them, point widgets at the new data, then render.
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
class ReceivedMessage:
    name: str
    schema: str
    message: dict[str, Any]
    received_at: float
    interval_seconds: float
    sequence: int = 0

    def accept(
        self,
        message: dict[str, Any],
        received_at: Optional[float] = None,
    ) -> None:
        self.message = message
        self.received_at = time.time() if received_at is None else received_at
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
        return count_nodes(build_tree_roots(self.name, self.message))


@dataclass
class MessageState:
    messages: list[ReceivedMessage]

    @property
    def sequence(self) -> int:
        return sum(message.sequence for message in self.messages)

    @property
    def leaf_count(self) -> int:
        return sum(message.leaf_count for message in self.messages)

    @property
    def branch_count(self) -> int:
        return count_nodes(build_message_roots(self.messages))


@dataclass
class MessageWidgets:
    status: tv.Text
    tree: tv.TreeView
    leaves: tv.DataTable
    details: tv.PropertyGrid


def optional_float(value: Any) -> Optional[float]:
    return None if value is None else float(value)


def optional_knots(value: Any) -> Optional[float]:
    if value is None:
        return None
    return float(value) * KNOTS_PER_METER_PER_SECOND


MESSAGE_ID = tv.path("header.message_id")
HEALTH = tv.path("status.overall_health", default="unknown")
SPEED_MS = tv.path("navigation.speed_ms", default=0.0, transform=float)
SPEED_KNOTS = tv.path(
    "navigation.speed_ms",
    default=0.0,
    transform=lambda value: float(value) * KNOTS_PER_METER_PER_SECOND,
)
DETAIL_MESSAGE_ID = tv.path("message.header.message_id", default="multiple")
DETAIL_SOURCE = tv.path("message.header.source", default="multiple")
DETAIL_SCHEMA = tv.path("schema", default="multiple")
DETAIL_HEALTH = tv.path("message.status.overall_health", default="mixed")
DETAIL_SPEED_MS = tv.path(
    "message.navigation.speed_ms",
    default=None,
    transform=optional_float,
)
DETAIL_SPEED_KNOTS = tv.path(
    "message.navigation.speed_ms",
    default=None,
    transform=optional_knots,
)


def make_navigation_message(sequence: int) -> dict[str, Any]:
    speed_ms = 9.8 + random.uniform(-0.7, 0.7)
    return {
        "header": {
            "message_id": f"nav-{sequence:06d}",
            "source": "bridge.telemetry",
            "schema": "vessel.navigation.v2",
        },
        "status": {
            "overall_health": "ok",
            "tracking": "locked",
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
    }


def make_power_message(sequence: int) -> dict[str, Any]:
    battery_percent = max(0.0, min(100.0, 82.0 + random.uniform(-4.0, 4.0)))
    health = "ok"
    if battery_percent < 25.0:
        health = "error"
    elif battery_percent < 45.0:
        health = "warning"
    return {
        "header": {
            "message_id": f"pwr-{sequence:06d}",
            "source": "power.monitor",
            "schema": "vessel.power.v1",
        },
        "status": {
            "overall_health": health,
            "battery_percent": round(battery_percent, 1),
            "faults": [] if health == "ok" else ["low_battery_margin"],
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
    }


def make_compute_message(sequence: int) -> dict[str, Any]:
    cpu_temp_c = 51.0 + random.uniform(-6.0, 12.0)
    health = "ok"
    if cpu_temp_c > 82.0:
        health = "error"
    elif cpu_temp_c > 70.0:
        health = "warning"
    return {
        "header": {
            "message_id": f"cmp-{sequence:06d}",
            "source": "edge.compute",
            "schema": "vessel.compute.v1",
        },
        "status": {
            "overall_health": health,
            "faults": [] if health == "ok" else ["thermal_margin"],
        },
        "compute": {
            "cpu": {
                "load": round(random.uniform(0.35, 0.92), 2),
                "temp_c": round(cpu_temp_c, 1),
            },
            "memory": {"used_mb": random.randint(2100, 2800), "total_mb": 4096},
        },
    }


def make_sensor_message(sequence: int) -> dict[str, Any]:
    imu_dropouts = random.randint(0, 3)
    gps_online = random.random() > 0.08
    health = "ok" if gps_online and imu_dropouts < 3 else "warning"
    return {
        "header": {
            "message_id": f"sns-{sequence:06d}",
            "source": "sensor.fusion",
            "schema": "vessel.sensors.v3",
        },
        "status": {
            "overall_health": health,
            "faults": [] if health == "ok" else ["sensor_quality"],
        },
        "sensors": [
            {
                "name": "imu",
                "online": True,
                "sample_hz": 200,
                "quality": {
                    "dropouts": imu_dropouts,
                    "jitter_ms": round(random.uniform(0.2, 1.5), 2),
                },
            },
            {
                "name": "gps",
                "online": gps_online,
                "sample_hz": 10,
                "quality": {
                    "satellites": random.randint(10, 15),
                    "hdop": round(random.uniform(0.7, 1.6), 2),
                },
            },
        ],
    }


def create_state() -> MessageState:
    now = time.time()
    messages = [
        ReceivedMessage("navigation", "vessel.navigation.v2", {}, now, 0.8),
        ReceivedMessage("power", "vessel.power.v1", {}, now, 1.3),
        ReceivedMessage("compute", "vessel.compute.v1", {}, now, 1.9),
        ReceivedMessage("sensors", "vessel.sensors.v3", {}, now, 2.7),
    ]
    messages[0].accept(make_navigation_message(1), now - 0.2)
    messages[1].accept(make_power_message(1), now - 0.8)
    messages[2].accept(make_compute_message(1), now - 1.4)
    messages[3].accept(make_sensor_message(1), now - 2.0)
    return MessageState(messages)


def create_widgets(state: MessageState) -> MessageWidgets:
    status = tv.Text(lambda: status_line(state), style="normal")
    tree = tv.TreeView(
        lambda: build_message_roots(state.messages),
        id="path",
        label=lambda node: f"{node.name} ({node.type_name}, {node.size})",
        children="children",
    )
    tree.expanded_ids.update(
        {
            "$.navigation",
            "$.navigation.navigation",
            "$.power",
            "$.power.power",
            "$.compute",
            "$.compute.compute",
            "$.sensors",
            "$.sensors.sensors",
        }
    )

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
        lambda: details_source(state, tree),
        [
            tv.Property("Stream", "name"),
            tv.Property("Message", DETAIL_MESSAGE_ID),
            tv.Property("Source", DETAIL_SOURCE),
            tv.Property("Schema", DETAIL_SCHEMA),
            tv.Property("Received", "received_time"),
            tv.Property("Age", "age_seconds", align="right", formatter=format_age),
            tv.Property(
                "Health",
                DETAIL_HEALTH,
                style=health_style,
            ),
            tv.Property(
                "Speed m/s",
                DETAIL_SPEED_MS,
                align="right",
                formatter=format_speed_ms,
            ),
            tv.Property(
                "Speed knots",
                DETAIL_SPEED_KNOTS,
                align="right",
                formatter=format_knots,
            ),
            tv.Property("Sequence", "sequence", align="right"),
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
                        tv.Size.fixed(14),
                        title="Message Details",
                    )


def run_main_loop(app: tv.App, state: MessageState, widgets: MessageWidgets) -> None:
    generators = [
        make_navigation_message,
        make_power_message,
        make_compute_message,
        make_sensor_message,
    ]
    next_message_at = [
        time.monotonic() + message.interval_seconds for message in state.messages
    ]
    with app.session():
        while app.running:
            now = time.monotonic()
            for index, message in enumerate(state.messages):
                if now >= next_message_at[index]:
                    message.accept(generators[index](message.sequence + 1))
                    next_message_at[index] = now + message.interval_seconds

            key = app.poll_key()
            if key:
                app.handle_key(key)

            app.render()
            app.sleep_until_next_frame()


def leaves_for_selection(state: MessageState, tree: tv.TreeView) -> list[LeafField]:
    selected = tree.selected_node
    if selected is None:
        return [
            leaf
            for message in state.messages
            for leaf in iter_leaf_fields(message.message, f"$.{message.name}")
        ]
    return iter_leaf_fields(selected.value, selected.path)


def details_source(state: MessageState, tree: tv.TreeView) -> ReceivedMessage:
    selected = tree.selected_node
    if selected is not None:
        for message in state.messages:
            if selected.path == f"$.{message.name}" or selected.path.startswith(
                f"$.{message.name}."
            ):
                return message
    return min(state.messages, key=lambda message: message.age_seconds)


def status_line(state: MessageState) -> str:
    latest = min(state.messages, key=lambda message: message.age_seconds)
    return " | ".join(
        [
            f"Streams {len(state.messages)}",
            f"Latest {latest.name} {MESSAGE_ID(latest.message)}",
            f"Age {format_age(latest.age_seconds)}",
            f"Nav {float(SPEED_KNOTS(state.messages[0].message)):5.1f} kn",
            "Tab focus | q exits",
        ]
    )


def build_message_roots(messages: list[ReceivedMessage]) -> list[FieldNode]:
    return [
        root
        for root in (
            build_tree_node(f"$.{message.name}", message.name, message.message)
            for message in messages
        )
        if root is not None
    ]


def build_tree_roots(name: str, message: Any) -> list[FieldNode]:
    root = build_tree_node(f"$.{name}", name, message)
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
    if value is None:
        return "n/a"
    return f"{float(value):.2f}"


def format_knots(value: Any) -> str:
    if value is None:
        return "n/a"
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
