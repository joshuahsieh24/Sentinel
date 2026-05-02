import json
import copy
import networkx as nx
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

PENALTY_OFFLINE = 40
PENALTY_OBSTRUCTED = 30
SOLO_MULTIPLIER = 2

@dataclass
class DeviceState:
    id: str; type: str; label: str
    zone: Optional[str] = None
    criticality: float = 1.0
    status: str = "online"
    obstructed: bool = False

@dataclass
class ZoneDef:
    id: str; label: str; cameras: list[str]

@dataclass
class RiskItem:
    device: str; label: str; score_delta: int; affected_zones: list[str]

@dataclass
class Incident:
    summary: str; recommendation: str
    fix_applied: bool = False; fix_applicable: bool = False

@dataclass
class SentinelGraph:
    graph: nx.DiGraph; devices: dict[str, DeviceState]; zones: list[ZoneDef]
    suppressed_devices: set[str] = field(default_factory=set)
    incident: Optional[Incident] = None
    fix_edge_added: bool = False

def load_topology(path: str | Path) -> SentinelGraph:
    data = json.loads(Path(path).read_text())
    g = nx.DiGraph(); devices = {}
    for d in data["devices"]:
        ds = DeviceState(id=d["id"], type=d["type"], label=d["label"], zone=d.get("zone"), criticality=d.get("criticality", 1.0))
        devices[d["id"]] = ds
        g.add_node(d["id"], **{"type": d["type"], "label": d["label"]})
    for e in data["edges"]: g.add_edge(e["src"], e["dst"])
    zones = [ZoneDef(z["id"], z["label"], z["cameras"]) for z in data["zones"]]
    return SentinelGraph(graph=g, devices=devices, zones=zones)

def _zone_score(zone: ZoneDef, devices: dict[str, DeviceState]) -> int:
    score = 100; solo = len(zone.cameras) == 1
    for cam_id in zone.cameras:
        dev = devices.get(cam_id)
        if not dev: continue
        if dev.status == "offline": score -= PENALTY_OFFLINE * (SOLO_MULTIPLIER if solo else 1)
        elif dev.obstructed: score -= PENALTY_OBSTRUCTED
    return max(0, score)

def compute_zone_scores(sg: SentinelGraph) -> dict[str, int]:
    return {z.id: _zone_score(z, sg.devices) for z in sg.zones}

def compute_building_score(zone_scores: dict[str, int], zones: list[ZoneDef], devices: dict[str, DeviceState]) -> int:
    tw = sum(devices[z.cameras[0]].criticality if z.cameras else 1.0 for z in zones)
    ws = sum(zone_scores[z.id] * (devices[z.cameras[0]].criticality if z.cameras else 1.0) for z in zones)
    return round(ws / tw)

def compute_full_state(sg: SentinelGraph) -> dict:
    zs = compute_zone_scores(sg); bs = compute_building_score(zs, sg.zones, sg.devices)
    zones_out = {z.id: {"score": zs[z.id], "status": ("healthy" if zs[z.id]>=80 else "degraded" if zs[z.id]>=50 else "exposed"), "label": z.label, "cameras": z.cameras} for z in sg.zones}
    devices_out = {d.id: {"status": d.status, "label": d.label, "type": d.type, "obstructed": getattr(d, "obstructed", False)} for d in sg.devices.values()}
    risks = compute_latent_risks(sg)
    return {"building_score": bs, "zones": zones_out, "devices": devices_out, "latent_risks": [asdict(r) for r in risks], "incident": asdict(sg.incident) if sg.incident else None}

from dataclasses import asdict

def simulate_failure(sg, device_id):
    cloned_devs = copy.deepcopy(sg.devices); cloned_g = sg.graph.copy()
    _propagate_failure(cloned_g, cloned_devs, device_id)
    b = compute_building_score(compute_zone_scores(sg), sg.zones, sg.devices)
    za = {z.id: _zone_score(z, cloned_devs) for z in sg.zones}; a = compute_building_score(za, sg.zones, cloned_devs)
    aff = [z.label for z in sg.zones if _zone_score(z, cloned_devs) < _zone_score(z, sg.devices)]
    return (a - b), aff

def _propagate_failure(g, devs, dev_id):
    if dev_id not in devs: return
    devs[dev_id].status = "offline"
    for anc in nx.ancestors(g, dev_id):
        if devs.get(anc) and devs[anc].type == "camera": devs[anc].status = "offline"

def compute_latent_risks(sg):
    res = []
    for d in sg.devices.values():
        if d.type == "camera" or d.status == "offline": continue
        delta, aff = simulate_failure(sg, d.id)
        if delta != 0: res.append(RiskItem(d.id, d.label, delta, aff))
    res.sort(key=lambda r: r.score_delta)
    return res[:3]

def apply_obstruct_cam2(sg: SentinelGraph):
    sg.devices["cam-2"].obstructed = True
    sg.incident = Incident(summary="Cam-2 obstructed — Loading Dock has no verified visual coverage.", recommendation="Inspect Loading Dock camera for physical obstruction.")

def run_rca(sg: SentinelGraph, newly_offline_cameras: list[str]) -> Optional[Incident]:
    if len(newly_offline_cameras) < 2: return None
    successor_sets = [nx.descendants(sg.graph, c) for c in newly_offline_cameras]
    common = set.intersection(*successor_sets) if successor_sets else set()
    best = next((n for n in common if not (set(sg.graph.predecessors(n)) & common)), "gateway")
    dev = sg.devices.get(best); d_type = dev.type if dev else "router"; d_lbl = dev.label if dev else best
    aff_zones = list(set(z.label for c in newly_offline_cameras for z in sg.zones if c in z.cameras))
    summary = f"{d_lbl} failure -> {len(newly_offline_cameras)} cameras offline -> {' and '.join(aff_zones)} lost verified coverage."
    return Incident(summary=summary, recommendation=f"Single point of failure: {d_lbl}. Deploy redundancy.", fix_applicable=True)
