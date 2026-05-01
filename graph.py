import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional
import networkx as nx

PENALTY_OFFLINE = 40
PENALTY_OBSTRUCTED = 30
SOLO_MULTIPLIER = 2

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

def _zone_score(zone: ZoneDef, devices: dict[str, DeviceState]) -> int:
    score = 100
    solo = len(zone.cameras) == 1
    for cam_id in zone.cameras:
        dev = devices.get(cam_id)
        if dev is None: continue
        if dev.status == "offline":
            mult = SOLO_MULTIPLIER if solo else 1
            score -= PENALTY_OFFLINE * mult
        elif dev.obstructed:
            score -= PENALTY_OBSTRUCTED
    return max(0, score)

def compute_zone_scores(sg: SentinelGraph) -> dict[str, int]:
    return {z.id: _zone_score(z, sg.devices) for z in sg.zones}

def compute_building_score(zone_scores: dict[str, int], zones: list[ZoneDef],
                           devices: dict[str, DeviceState]) -> int:
    total_weight = sum(devices[z.cameras[0]].criticality if z.cameras else 1.0 for z in zones)
    weighted_sum = 0.0
    for z in zones:
        cam_criticality = devices[z.cameras[0]].criticality if z.cameras else 1.0
        weighted_sum += zone_scores[z.id] * cam_criticality
    return round(weighted_sum / total_weight)

def _status_label(score: int) -> str:
    if score >= 80: return "healthy"
    if score >= 50: return "degraded"
    return "exposed"

def compute_full_state(sg: SentinelGraph) -> dict:
    zone_scores = compute_zone_scores(sg)
    building_score = compute_building_score(zone_scores, sg.zones, sg.devices)
    zones_out = {}
    for z in sg.zones:
        zones_out[z.id] = {
            "score": zone_scores[z.id],
            "status": _status_label(zone_scores[z.id]),
            "label": z.label,
            "cameras": z.cameras,
        }
    devices_out = {}
    for dev in sg.devices.values():
        entry = {"status": dev.status, "label": dev.label, "type": dev.type}
        if dev.type == "camera": entry["obstructed"] = dev.obstructed
        devices_out[dev.id] = entry
    return {
        "building_score": building_score,
        "zones": zones_out,
        "devices": devices_out,
    }
