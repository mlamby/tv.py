"""Example application for the telemetry dashboard framework."""

from __future__ import annotations

import random
import time
from dataclasses import dataclass, field
from typing import Any

import tv


@dataclass
class Device:
    name: str
    path: str
    status: str
    rate: float
    latency_ms: float
    children: list[Device] = field(default_factory=lambda: [])


@dataclass
class DashboardModel:
    devices: list[Device]
    roots: list[Device]
    logs: list[str]


@dataclass
class DashboardWidgets:
    table: tv.DataTable
    tree: tv.TreeView
    details: tv.PropertyGrid
    log_view: tv.LogView
    banner: tv.Text


def create_data_model() -> DashboardModel:
    devices = [
        Device("api-1", "/svc/api-1", "ok", 420.0, 18.0),
        Device("api-2", "/svc/api-2", "warning", 370.0, 41.0),
        Device("worker-1", "/svc/worker-1", "ok", 255.0, 24.0),
        Device("db-primary", "/data/db-primary", "ok", 92.0, 11.0),
    ]
    roots = [
        Device("services", "/svc", "ok", 0.0, 0.0, devices[:3]),
        Device("storage", "/data", "ok", 0.0, 0.0, devices[3:]),
    ]
    return DashboardModel(devices, roots, ["dashboard started"])


def create_widgets(model: DashboardModel) -> DashboardWidgets:
    table = tv.DataTable(
        columns=[
            tv.Column("Name", "name", tv.Size.auto()),
            tv.Column("Status", "status", tv.Size.fixed(10), style=status_style),
            tv.Column(
                "Rate/s",
                lambda row: row.rate,
                tv.Size.flex(1),
                "right",
                format_float,
            ),
            tv.Column(
                "Latency",
                lambda row: row.latency_ms,
                tv.Size.fixed(10),
                "right",
                format_ms,
            ),
        ],
        rows=model.devices,
    )
    tree = tv.TreeView(
        model.roots,
        id=lambda node: node.path,
        label=lambda node: f"{status_icon(node.status)} {node.name}",
        children=lambda node: node.children,
    )
    tree.expanded_ids.add("/svc")
    details = tv.PropertyGrid(
        properties=[
            tv.Property("Name", "name"),
            tv.Property("Path", "path", style="muted"),
            tv.Property(
                "Status",
                "status",
                style=lambda value: status_style_name(str(value)),
            ),
            tv.Property("Rate/s", "rate", formatter=format_float),
            tv.Property("Latency", "latency_ms", formatter=format_ms),
        ]
    )
    log_view = tv.LogView(
        model.logs,
        style=lambda line: "warning" if "warning" in line else "normal",
    )
    banner = tv.Text(
        lambda: "Tab focus | Alt-1 overview | Alt-2 health | q exits",
        "muted",
    )
    return DashboardWidgets(table, tree, details, log_view, banner)


def create_layout(app: tv.App, widgets: DashboardWidgets) -> None:
    with app.screen("overview") as screen:  # noqa: SIM117
        with screen.vbox() as overview:
            overview.panel(widgets.banner, tv.Size.fixed(3), title="Status")
            with overview.hbox(tv.Size.flex(1)) as middle:
                middle.panel(widgets.table, tv.Size.flex(2), title="Devices")
                middle.panel(widgets.details, tv.Size.flex(1), title="Details")
            overview.panel(widgets.log_view, tv.Size.fixed(8), title="Log")

    with app.screen("health") as screen:  # noqa: SIM117
        with screen.vbox() as health:  # noqa: SIM117
            health.panel(widgets.banner, tv.Size.fixed(1), border=False)
            with health.hbox() as middle:
                middle.panel(widgets.tree, tv.Size.flex(1), title="Topology")
                middle.panel(widgets.table, tv.Size.flex(2), title="Device Health")

    app.bind("alt+1", lambda: app.show_screen("overview"))
    app.bind("alt+2", lambda: app.show_screen("health"))


def run_main_loop(app: tv.App, model: DashboardModel, widgets: DashboardWidgets) -> None:
    last_log = time.monotonic()
    with app.session():
        while app.running:
            update_devices(model.devices)
            widgets.details.source = widgets.table.selected_item
            if time.monotonic() - last_log > 1.5:
                selected = widgets.table.selected_item or model.devices[0]
                timestamp = time.strftime("%H:%M:%S")
                model.logs.append(f"{timestamp} {selected.name} {selected.status}")
                last_log = time.monotonic()

            key = app.poll_key()
            if key:
                app.handle_key(key)

            app.render()
            app.sleep_until_next_frame()


def main() -> None:
    model = create_data_model()
    widgets = create_widgets(model)
    app = tv.App(refresh_hz=10)
    create_layout(app, widgets)
    run_main_loop(app, model, widgets)


def update_devices(devices: list[Device]) -> None:
    for device in devices:
        device.rate = max(0.0, device.rate + random.uniform(-8.0, 8.0))
        device.latency_ms = max(1.0, device.latency_ms + random.uniform(-2.0, 2.0))
        if device.latency_ms > 70:
            device.status = "error"
        elif device.latency_ms > 45:
            device.status = "warning"
        else:
            device.status = "ok"


def format_float(value: Any) -> str:
    return f"{float(value):.1f}"


def format_ms(value: Any) -> str:
    return f"{float(value):.0f} ms"


def status_style(row: object) -> str:
    return status_style_name(getattr(row, "status", ""))


def status_style_name(status: str) -> str:
    if status == "ok":
        return "ok"
    if status == "warning":
        return "warning"
    if status == "error":
        return "error"
    return "normal"


def status_icon(status: str) -> str:
    if status == "ok":
        return tv.Icons.OK
    if status == "warning":
        return tv.Icons.WARNING
    if status == "error":
        return tv.Icons.ERROR
    return " "


if __name__ == "__main__":
    main()
