import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional
import networkx as nx

@dataclass
class DeviceState:
    id: str
    type: str
    label: str
    zone: Optional[str] = None
    criticality: float = 1.0
    status: str = "online"
    obstructed: bool = False

@dataclass
class ZoneDef:
    id: str
    label: str
    cameras: list[str]

@dataclass
class SentinelGraph:
    graph: nx.DiGraph
    devices: dict[str, DeviceState]
    zones: list[ZoneDef]
    suppressed_devices: set[str] = field(default_factory=set)
    incident: Optional[any] = None
    fix_edge_added: bool = False

def load_topology(path: str | Path) -> SentinelGraph:
    data = json.loads(Path(path).read_text())
    g = nx.DiGraph()
    devices: dict[str, DeviceState] = {}
    for d in data["devices"]:
        ds = DeviceState(
            id=d["id"],
            type=d["type"],
            label=d["label"],
            zone=d.get("zone"),
            criticality=d.get("criticality", 1.0),
        )
        devices[d["id"]] = ds
        g.add_node(d["id"], **{"type": d["type"], "label": d["label"]})
    for e in data["edges"]:
        g.add_edge(e["src"], e["dst"])
    zones = [ZoneDef(z["id"], z["label"], z["cameras"]) for z in data["zones"]]
    return SentinelGraph(graph=g, devices=devices, zones=zones)
