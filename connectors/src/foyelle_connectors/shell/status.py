"""System status: model in use, connector health, network. Local reads only."""

from __future__ import annotations

import shutil
import socket
import subprocess
from pathlib import Path
from typing import Any

from ..sdk.server import PORTS


def connector_health(
    connectors: dict[str, int], method: str, unit_template: str, *, timeout: float = 2.0
) -> list[dict[str, Any]]:
    """Health per connector.

    ``systemd`` asks ``systemctl is-active`` (the shell connector cannot open
    other connectors' ports: nftables admits only the harness uid);
    ``tcp`` tries a loopback connect (development); ``none`` skips checks.
    """
    out = []
    systemctl = shutil.which("systemctl") if method == "systemd" else None
    for name, port in connectors.items():
        state = "unknown"
        if method == "tcp":
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=timeout):
                    state = "active"
            except OSError:
                state = "unreachable"
        elif method == "systemd" and systemctl:
            unit = unit_template.format(name=name)
            try:
                proc = subprocess.run(
                    [systemctl, "is-active", "--", unit], capture_output=True, text=True, timeout=timeout, check=False
                )
                state = proc.stdout.strip() or "unknown"
            except (OSError, subprocess.TimeoutExpired):
                state = "unknown"
        out.append({"name": name, "port": port, "state": state})
    return out


def parse_connectors(spec: str | None) -> dict[str, int]:
    """``"shell,files=7301"`` -> ``{"shell": 7300, "files": 7301}``; default: all six."""
    if not spec:
        return dict(PORTS)
    result: dict[str, int] = {}
    for item in filter(None, (p.strip() for p in spec.split(","))):
        name, _, port = item.partition("=")
        result[name] = int(port) if port else PORTS.get(name, 0)
    return result


def network_status(sys_net: Path = Path("/sys/class/net"), route_file: Path = Path("/proc/net/route")) -> dict[str, Any]:
    interfaces = []
    if sys_net.is_dir():
        for iface in sorted(sys_net.iterdir()):
            if iface.name == "lo":
                continue

            def read(name: str, iface: Path = iface) -> str | None:
                try:
                    return (iface / name).read_text().strip()
                except OSError:
                    return None

            interfaces.append({"name": iface.name, "state": read("operstate") or "unknown", "carrier": read("carrier") == "1"})
    default_route: bool | None = None
    try:
        lines = route_file.read_text().splitlines()[1:]
        default_route = any(len(cols) > 2 and cols[1] == "00000000" for cols in (line.split() for line in lines))
    except OSError:
        pass
    online = default_route is True and any(i["state"] == "up" for i in interfaces)
    return {"online": online if interfaces else None, "default_route": default_route, "interfaces": interfaces}


def model_status(effective_settings: dict[str, Any]) -> dict[str, Any]:
    model = effective_settings.get("model")
    model = model if isinstance(model, dict) else {}
    keys = ("route", "main", "auxiliary", "provider", "base_url", "local_model")
    return {k: model[k] for k in keys if k in model}


def uptime_seconds(path: Path = Path("/proc/uptime")) -> float | None:
    try:
        return float(path.read_text().split()[0])
    except (OSError, ValueError, IndexError):
        return None
