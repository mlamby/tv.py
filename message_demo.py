"""Multi-message binding exploration demo for tv.py.

This demo keeps the framework API unchanged. It shows the current application
pattern for nested telemetry messages: receive messages, derive widget view
models from them, point widgets at the new data, then render.
"""

from __future__ import annotations

import ctypes
import enum
import random
import time
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Generic, Optional, TypeVar

import tv

KNOTS_PER_METER_PER_SECOND = 1.9438444924406
HEALTH_OK = 0
HEALTH_WARNING = 1
HEALTH_ERROR = 2
PayloadT = TypeVar("PayloadT", bound=ctypes.Structure)


class MessageHeader(ctypes.Structure):
    _fields_ = [
        ("message_id", ctypes.c_wchar * 16),
        ("source", ctypes.c_wchar * 32),
    ]


class MessageStatus(ctypes.Structure):
    _fields_ = [
        ("overall_health", ctypes.c_uint8),
        ("fault_count", ctypes.c_uint16),
    ]


class GeoPosition(ctypes.Structure):
    _fields_ = [
        ("lat_deg", ctypes.c_double),
        ("lon_deg", ctypes.c_double),
        ("alt_m", ctypes.c_float),
    ]


class MotionSolution(ctypes.Structure):
    _fields_ = [
        ("speed_ms", ctypes.c_float),
        ("heading_deg", ctypes.c_float),
        ("position", GeoPosition),
    ]


class NavigationPayload(ctypes.Structure):
    _fields_ = [
        ("header", MessageHeader),
        ("status", MessageStatus),
        ("navigation", MotionSolution),
    ]


class BatteryState(ctypes.Structure):
    _fields_ = [
        ("voltage", ctypes.c_float),
        ("current_a", ctypes.c_float),
        ("percent", ctypes.c_float),
    ]


class PowerBus(ctypes.Structure):
    _fields_ = [
        ("voltage", ctypes.c_float),
        ("current_a", ctypes.c_float),
    ]


class PowerSystem(ctypes.Structure):
    _fields_ = [
        ("battery", BatteryState),
        ("bus", PowerBus),
    ]


class PowerPayload(ctypes.Structure):
    _fields_ = [
        ("header", MessageHeader),
        ("status", MessageStatus),
        ("power", PowerSystem),
    ]


class CpuState(ctypes.Structure):
    _fields_ = [
        ("load", ctypes.c_float),
        ("temp_c", ctypes.c_float),
    ]


class MemoryState(ctypes.Structure):
    _fields_ = [
        ("used_mb", ctypes.c_uint32),
        ("total_mb", ctypes.c_uint32),
    ]


class ComputeState(ctypes.Structure):
    _fields_ = [
        ("cpu", CpuState),
        ("memory", MemoryState),
    ]


class ComputePayload(ctypes.Structure):
    _fields_ = [
        ("header", MessageHeader),
        ("status", MessageStatus),
        ("compute", ComputeState),
    ]


class SensorQuality(ctypes.Structure):
    _fields_ = [
        ("dropouts", ctypes.c_uint16),
        ("jitter_ms", ctypes.c_float),
        ("satellites", ctypes.c_uint16),
        ("hdop", ctypes.c_float),
    ]


class SensorReading(ctypes.Structure):
    _fields_ = [
        ("name", ctypes.c_wchar * 16),
        ("online", ctypes.c_bool),
        ("sample_hz", ctypes.c_uint16),
        ("quality", SensorQuality),
    ]


SensorReadingArray = SensorReading * 2


class SensorPayload(ctypes.Structure):
    _fields_ = [
        ("header", MessageHeader),
        ("status", MessageStatus),
        ("sensors", SensorReadingArray),
    ]


def health_name(value: Any) -> str:
    if isinstance(value, enum.Enum):
        return value.name.lower()
    if isinstance(value, str):
        return value
    if value == HEALTH_OK:
        return "ok"
    if value == HEALTH_WARNING:
        return "warning"
    if value == HEALTH_ERROR:
        return "error"
    return "unknown"


@dataclass
class FieldNode:
    path: str
    name: str
    type_name: str
    size: int
    value: Any
    children: list["FieldNode"] = field(default_factory=list)


@dataclass
class ReceivedMessage(Generic[PayloadT]):
    name: str
    schema: str
    payload: PayloadT
    received_at: float
    interval_seconds: float
    sequence: int = 0

    def accept(
        self,
        payload: PayloadT,
        received_at: Optional[float] = None,
    ) -> None:
        self.payload = payload
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
        return len(tv.match_paths(self.payload))

    @property
    def branch_count(self) -> int:
        return count_nodes(build_tree_roots(self.name, self.payload))

    @property
    def health(self) -> str:
        return health_name(self.payload.status.overall_health)

    @property
    def message_id(self) -> str:
        return str(self.payload.header.message_id)

    @property
    def source(self) -> str:
        return str(self.payload.header.source)


@dataclass
class MessageCollection:
    navigation: ReceivedMessage[NavigationPayload]
    power: ReceivedMessage[PowerPayload]
    compute: ReceivedMessage[ComputePayload]
    sensors: ReceivedMessage[SensorPayload]

    def __post_init__(self) -> None:
        self._ordered: list[ReceivedMessage[Any]] = [
            self.navigation,
            self.power,
            self.compute,
            self.sensors,
        ]
        names = {message.name for message in self._ordered}
        if names != {"navigation", "power", "compute", "sensors"}:
            raise ValueError("message names must match collection field names")

    def __iter__(self) -> Iterator[ReceivedMessage[Any]]:
        return iter(self._ordered)

    def __len__(self) -> int:
        return len(self._ordered)


@dataclass
class MessageState:
    messages: MessageCollection

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
    status: tv.StatusLine
    tree: tv.TreeView
    leaves: tv.DataTable
    details: tv.PropertyGrid


MESSAGE_ID = tv.path("header.message_id")
SPEED_MS = tv.path("navigation.speed_ms", default=0.0, transform=float)
SPEED_KNOTS = tv.path(
    "navigation.speed_ms",
    default=0.0,
    transform=lambda value: float(value) * KNOTS_PER_METER_PER_SECOND,
)
DETAIL_MESSAGE_ID = tv.path("payload.header.message_id", default="multiple")
DETAIL_SOURCE = tv.path("payload.header.source", default="multiple")
DETAIL_SCHEMA = tv.path("schema", default="multiple")


def make_navigation_message(sequence: int) -> NavigationPayload:
    speed_ms = 9.8 + random.uniform(-0.7, 0.7)
    return NavigationPayload(
        MessageHeader(f"nav-{sequence:06d}", "bridge.telemetry"),
        MessageStatus(HEALTH_OK, 0),
        MotionSolution(
            round(speed_ms, 3),
            round(184.0 + random.uniform(-2.0, 2.0), 2),
            GeoPosition(
                round(-33.8568 + random.uniform(-0.0005, 0.0005), 6),
                round(151.2153 + random.uniform(-0.0005, 0.0005), 6),
                round(4.0 + random.uniform(-0.2, 0.2), 2),
            ),
        ),
    )


def make_power_message(sequence: int) -> PowerPayload:
    battery_percent = max(0.0, min(100.0, 82.0 + random.uniform(-4.0, 4.0)))
    health = HEALTH_OK
    if battery_percent < 25.0:
        health = HEALTH_ERROR
    elif battery_percent < 45.0:
        health = HEALTH_WARNING
    return PowerPayload(
        MessageHeader(f"pwr-{sequence:06d}", "power.monitor"),
        MessageStatus(health, 0 if health == HEALTH_OK else 1),
        PowerSystem(
            BatteryState(
                round(47.8 + random.uniform(-0.4, 0.4), 2),
                round(18.2 + random.uniform(-2.0, 2.0), 2),
                round(battery_percent, 1),
            ),
            PowerBus(24.1, round(7.3 + random.uniform(-1.0, 1.0), 2)),
        ),
    )


def make_compute_message(sequence: int) -> ComputePayload:
    cpu_temp_c = 51.0 + random.uniform(-6.0, 12.0)
    health = HEALTH_OK
    if cpu_temp_c > 82.0:
        health = HEALTH_ERROR
    elif cpu_temp_c > 70.0:
        health = HEALTH_WARNING
    return ComputePayload(
        MessageHeader(f"cmp-{sequence:06d}", "edge.compute"),
        MessageStatus(health, 0 if health == HEALTH_OK else 1),
        ComputeState(
            CpuState(round(random.uniform(0.35, 0.92), 2), round(cpu_temp_c, 1)),
            MemoryState(random.randint(2100, 2800), 4096),
        ),
    )


def make_sensor_message(sequence: int) -> SensorPayload:
    imu_dropouts = random.randint(0, 3)
    gps_online = random.random() > 0.08
    health = HEALTH_OK if gps_online and imu_dropouts < 3 else HEALTH_WARNING
    return SensorPayload(
        MessageHeader(f"sns-{sequence:06d}", "sensor.fusion"),
        MessageStatus(health, 0 if health == HEALTH_OK else 1),
        SensorReadingArray(
            SensorReading(
                "imu",
                True,
                200,
                SensorQuality(imu_dropouts, round(random.uniform(0.2, 1.5), 2), 0, 0),
            ),
            SensorReading(
                "gps",
                gps_online,
                10,
                SensorQuality(
                    0,
                    0,
                    random.randint(10, 15),
                    round(random.uniform(0.7, 1.6), 2),
                ),
            ),
        ),
    )


def create_state() -> MessageState:
    now = time.time()
    navigation = ReceivedMessage(
        "navigation",
        "vessel.navigation.v2",
        make_navigation_message(1),
        now - 0.2,
        0.8,
        1,
    )
    power = ReceivedMessage(
        "power",
        "vessel.power.v1",
        make_power_message(1),
        now - 0.8,
        1.3,
        1,
    )
    compute = ReceivedMessage(
        "compute",
        "vessel.compute.v1",
        make_compute_message(1),
        now - 1.4,
        1.9,
        1,
    )
    sensors = ReceivedMessage(
        "sensors",
        "vessel.sensors.v3",
        make_sensor_message(1),
        now - 2.0,
        2.7,
        1,
    )
    return MessageState(MessageCollection(navigation, power, compute, sensors))


def create_widgets(state: MessageState) -> MessageWidgets:
    status = tv.StatusLine(
        [
            tv.StatusItem(
                "Streams",
                lambda: len(state.messages),
                tv.Size.fixed(10),
                align="right",
            ),
            tv.StatusItem(
                "Latest",
                lambda: min(state.messages, key=lambda message: message.age_seconds),
                tv.Size.fixed(28),
                formatter=lambda message: f"{message.name} {message.message_id}",
            ),
            tv.StatusItem(
                "Age",
                lambda: min(
                    state.messages,
                    key=lambda message: message.age_seconds,
                ).age_seconds,
                tv.Size.fixed(12),
                align="right",
                formatter=format_age,
            ),
            tv.StatusItem(
                "Nav",
                lambda: SPEED_KNOTS(state.messages.navigation.payload),
                tv.Size.fixed(12),
                align="right",
                formatter=lambda value: f"{float(value):.1f} kn",
            ),
            tv.StatusItem(
                "",
                "Tab focus | q exits",
                tv.Size.flex(1),
                style="muted",
            ),
        ],
        style="muted",
    )
    tree = tv.TreeView(
        lambda: build_message_roots(state.messages),
        id="path",
        label=lambda node: f"{node.name} ({node.type_name}, {node.size})",
        children="children",
    )
    tree.expanded_ids.update(
        {
            "navigation",
            "navigation.navigation",
            "power",
            "power.power",
            "compute",
            "compute.compute",
            "sensors",
            "sensors.sensors",
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
            tv.PropertyPattern(
                "payload.status.overall_health",
                label="leaf",
                formatter=lambda match: health_name(match.value),
                style=lambda match: health_style(match.value),
            ),
            tv.PropertyPattern(
                "payload.status.fault_count",
                label="leaf",
                align="right",
            ),
            tv.PropertyPattern(
                "payload.navigation.speed_ms",
                label="leaf",
                align="right",
                formatter=lambda match: format_speed_ms(match.value),
            ),
            tv.PropertyPattern(
                "payload.navigation.position.*_deg",
                label="leaf",
                align="right",
                formatter=lambda match: format_value(match.value),
            ),
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
    generators = {
        "navigation": make_navigation_message,
        "power": make_power_message,
        "compute": make_compute_message,
        "sensors": make_sensor_message,
    }
    next_message_at = {
        message.name: time.monotonic() + message.interval_seconds
        for message in state.messages
    }
    with app.session():
        while app.running:
            now = time.monotonic()
            for message in state.messages:
                if now >= next_message_at[message.name]:
                    message.accept(generators[message.name](message.sequence + 1))
                    next_message_at[message.name] = now + message.interval_seconds

            key = app.poll_key()
            if key:
                app.handle_key(key)

            app.render()
            app.sleep_until_next_frame()


def leaves_for_selection(state: MessageState, tree: tv.TreeView) -> list[tv.PathMatch]:
    selected = tree.selected_node
    if selected is None:
        return [
            match
            for message in state.messages
            for match in tv.match_paths(message.payload, prefix=message.name)
        ]
    return tv.match_paths(selected.value, prefix=selected.path)


def details_source(state: MessageState, tree: tv.TreeView) -> ReceivedMessage[Any]:
    selected = tree.selected_node
    if selected is not None:
        for message in state.messages:
            if selected.path == message.name or selected.path.startswith(
                f"{message.name}."
            ):
                return message
    return min(state.messages, key=lambda message: message.age_seconds)


def build_message_roots(messages: Iterable[ReceivedMessage[Any]]) -> list[FieldNode]:
    return [
        root
        for root in (
            build_tree_node(message.name, message.name, message.payload)
            for message in messages
        )
        if root is not None
    ]


def build_tree_roots(name: str, message: Any) -> list[FieldNode]:
    root = build_tree_node(name, name, message)
    return [root] if root is not None else []


def build_tree_node(path: str, name: str, value: Any) -> Optional[FieldNode]:
    child_matches = tv.iter_path_children(value, prefix=path)
    if not child_matches:
        return None
    children = [
        child
        for match in child_matches
        for child in [build_tree_node(match.path, match.name, match.value)]
        if child is not None
    ]
    return FieldNode(
        path,
        name,
        container_type_name(value),
        len(child_matches),
        value,
        children,
    )


def container_type_name(value: Any) -> str:
    if isinstance(value, ctypes.Structure):
        return value.__class__.__name__
    if isinstance(value, (ctypes.Array, list, tuple)):
        return "array"
    if isinstance(value, Mapping):
        return "object"
    return type(value).__name__


def count_nodes(nodes: list[FieldNode]) -> int:
    return sum(1 + count_nodes(node.children) for node in nodes)


def format_value(value: Any) -> str:
    if isinstance(value, enum.Enum):
        return value.name
    if isinstance(value, float):
        return f"{value:.3f}"
    return str(value)


def format_age(value: Any) -> str:
    return f"{float(value):.1f} s"


def format_speed_ms(value: Any) -> str:
    if value is None:
        return "n/a"
    return f"{float(value):.2f}"


def health_style(value: Any) -> str:
    value = health_name(value)
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
