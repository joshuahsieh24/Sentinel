"""
Sentinel dependency graph, scoring, predictive risk, and RCA engines.
All state lives here. main.py holds locks and broadcasts; this module is pure logic.
"""
import copy
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
    type: str  # camera | switch | uplink | router
    label: str
    zone: Optional[str] = None
    criticality: float = 1.0
    status: str = "online"   # online | offline
    obstructed: bool = False


@dataclass
class ZoneDef:
    id: str
    label: str
    cameras: list[str]


@dataclass
class RiskItem:
    device: str
    label: str
    score_delta: int
    affected_zones: list[str]


@dataclass
class Incident:
    summary: str
    recommendation: str
    fix_applied: bool = False
    fix_applicable: bool = False  # True only when a topology graph fix exists


@dataclass
class SentinelGraph:
    graph: nx.DiGraph
    devices: dict[str, DeviceState]
    zones: list[ZoneDef]
    suppressed_devices: set[str] = field(default_factory=set)
    incident: Optional[Incident] = None
    # The "apply fix" adds a cam-2 → switch-b edge; track so reset can remove it
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


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

def _zone_score(zone: ZoneDef, devices: dict[str, DeviceState]) -> int:
    score = 100
    solo = len(zone.cameras) == 1
    for cam_id in zone.cameras:
        dev = devices.get(cam_id)
        if dev is None:
            continue
        if dev.status == "offline":
            # Solo camera offline doubles the penalty — zone has no fallback
            mult = SOLO_MULTIPLIER if solo else 1
            score -= PENALTY_OFFLINE * mult
        elif dev.obstructed:
            # Obstruction: camera is online but blind. No solo multiplier —
            # the camera is still present, coverage is degraded not lost.
            score -= PENALTY_OBSTRUCTED
    return max(0, score)


def compute_zone_scores(sg: SentinelGraph) -> dict[str, int]:
    return {z.id: _zone_score(z, sg.devices) for z in sg.zones}


def compute_building_score(zone_scores: dict[str, int], zones: list[ZoneDef],
                           devices: dict[str, DeviceState]) -> int:
    total_weight = sum(
        devices[z.cameras[0]].criticality if z.cameras else 1.0
        for z in zones
    )
    weighted_sum = 0.0
    for z in zones:
        cam_criticality = devices[z.cameras[0]].criticality if z.cameras else 1.0
        weighted_sum += zone_scores[z.id] * cam_criticality
    return round(weighted_sum / total_weight)


def _status_label(score: int) -> str:
    if score >= 80:
        return "healthy"
    if score >= 50:
        return "degraded"
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
        if dev.type == "camera":
            entry["obstructed"] = dev.obstructed
        devices_out[dev.id] = entry

    risks = compute_latent_risks(sg)
    risks_out = [
        {"device": r.device, "label": r.label,
         "score_delta": r.score_delta, "affected_zones": r.affected_zones}
        for r in risks
    ]

    incident_out = None
    if sg.incident:
        incident_out = {
            "summary": sg.incident.summary,
            "recommendation": sg.incident.recommendation,
            "fix_applied": sg.incident.fix_applied,
        }

    fix_available = (
        sg.incident is not None
        and not sg.incident.fix_applied
        and sg.incident.fix_applicable
    )

    return {
        "building_score": building_score,
        "zones": zones_out,
        "devices": devices_out,
        "incident": incident_out,
        "latent_risks": risks_out,
        "fix_available": fix_available,
    }


# ---------------------------------------------------------------------------
# Predictive risk — simulate each online non-camera device failing
# ---------------------------------------------------------------------------

def simulate_failure(sg: SentinelGraph, device_id: str) -> tuple[int, list[str]]:
    """Clone state, mark device_id offline, propagate, return (score_delta, affected_zones)."""
    cloned_devices = copy.deepcopy(sg.devices)
    cloned_graph = sg.graph.copy()

    _propagate_failure(cloned_graph, cloned_devices, device_id)

    before = compute_building_score(
        compute_zone_scores(sg),
        sg.zones,
        sg.devices,
    )
    zone_scores_after = {
        z.id: _zone_score(z, cloned_devices) for z in sg.zones
    }
    after = compute_building_score(zone_scores_after, sg.zones, cloned_devices)

    affected = [
        z.label for z in sg.zones
        if _zone_score(z, cloned_devices) < _zone_score(z, sg.devices)
    ]
    return (after - before), affected


def _propagate_failure(graph: nx.DiGraph, devices: dict[str, DeviceState],
                       device_id: str):
    """Mark device_id offline, then mark all cameras that depend on it offline.

    Edges run cam → switch → uplink → gateway (network path direction).
    Cameras that depend on device_id are its ANCESTORS in that graph.
    """
    if device_id not in devices:
        return
    devices[device_id].status = "offline"
    for ancestor in nx.ancestors(graph, device_id):
        if devices.get(ancestor) and devices[ancestor].type == "camera":
            devices[ancestor].status = "offline"


def compute_latent_risks(sg: SentinelGraph) -> list[RiskItem]:
    results: list[RiskItem] = []
    for dev in sg.devices.values():
        if dev.type == "camera" or dev.status == "offline":
            continue
        delta, affected = simulate_failure(sg, dev.id)
        if delta != 0:
            results.append(RiskItem(
                device=dev.id,
                label=dev.label,
                score_delta=delta,
                affected_zones=affected,
            ))
    results.sort(key=lambda r: r.score_delta)  # most negative first
    return results[:3]


# ---------------------------------------------------------------------------
# RCA engine
# ---------------------------------------------------------------------------

_RCA_SUMMARY = {
    "switch": "{label} failure → {n} cameras offline → {zones} lost verified coverage.",
    "uplink": "Uplink failure → {n} cameras offline → {zones} lost verified coverage.",
    "router": "Gateway failure — all cameras affected — full building coverage lost.",
}

_RCA_RECOMMENDATION = {
    "switch": (
        "Single point of failure: {label} serves {n} critical cameras. "
        "Deploy redundant uplink for {cam} via the alternate switch."
    ),
    "uplink": (
        "Core network device failure. "
        "Recommend hot-standby uplink and out-of-band management path."
    ),
    "router": (
        "Core network device failure. "
        "Recommend hot-standby gateway and out-of-band management path."
    ),
}


def run_rca(sg: SentinelGraph, newly_offline_cameras: list[str]) -> Optional[Incident]:
    if len(newly_offline_cameras) < 2:
        return None

    # Edges: cam → switch → uplink → gateway.
    # Common path nodes = intersection of all successors from each offline camera.
    # LCA = the node in that common set with no predecessors also in the common set
    # (i.e. the node closest to the cameras).
    successor_sets = []
    for cam_id in newly_offline_cameras:
        succ = nx.descendants(sg.graph, cam_id)
        successor_sets.append(succ)

    common = set.intersection(*successor_sets) if successor_sets else set()

    best_ancestor = None
    for node in common:
        preds_in_common = set(sg.graph.predecessors(node)) & common
        if not preds_in_common:
            best_ancestor = node
            break

    if best_ancestor is None:
        best_ancestor = "gateway"

    dev = sg.devices.get(best_ancestor)
    dev_type = dev.type if dev else "router"
    dev_label = dev.label if dev else best_ancestor

    affected_zone_labels = []
    for cam_id in newly_offline_cameras:
        cam_dev = sg.devices.get(cam_id)
        if cam_dev and cam_dev.zone:
            zone = next((z for z in sg.zones if z.id == cam_dev.zone), None)
            if zone and zone.label not in affected_zone_labels:
                affected_zone_labels.append(zone.label)

    zones_str = " and ".join(affected_zone_labels)
    n = len(newly_offline_cameras)

    # Pick first offline camera for recommendation text
    first_cam_label = sg.devices[newly_offline_cameras[0]].label if newly_offline_cameras else "camera"

    summary = _RCA_SUMMARY.get(dev_type, _RCA_SUMMARY["router"]).format(
        label=dev_label, n=n, zones=zones_str
    )
    recommendation = _RCA_RECOMMENDATION.get(dev_type, _RCA_RECOMMENDATION["router"]).format(
        label=dev_label, n=n, cam=first_cam_label
    )

    return Incident(summary=summary, recommendation=recommendation, fix_applicable=True)


# ---------------------------------------------------------------------------
# State mutations
# ---------------------------------------------------------------------------

def apply_obstruct_cam2(sg: SentinelGraph):
    sg.devices["cam-2"].obstructed = True
    sg.suppressed_devices.discard("cam-2")  # cam-2 still on network when obstructed
    zone_scores = compute_zone_scores(sg)
    # Single camera obstructed — generate incident
    sg.incident = Incident(
        summary=(
            "Cam-2 obstructed — Loading Dock has no verified visual coverage. "
            "Cam-2 is online but blind."
        ),
        recommendation=(
            "Inspect Loading Dock camera for physical obstruction. "
            "Dispatch security personnel to verify zone coverage."
        ),
    )


def apply_fail_switch_a(sg: SentinelGraph):
    """
    Phase 1 of switch-a failure: immediately suppress cam-1 and cam-2 events.
    Phase 2 (called 1.5s later by main.py): apply_fail_switch_a_commit().
    """
    sg.suppressed_devices.add("cam-1")
    sg.suppressed_devices.add("cam-2")
    sg.suppressed_devices.add("switch-a")


def apply_fail_switch_a_commit(sg: SentinelGraph):
    """Mutate graph state 1.5s after event suppression begins."""
    sg.devices["switch-a"].status = "offline"
    sg.devices["cam-1"].status = "offline"
    sg.devices["cam-2"].status = "offline"
    incident = run_rca(sg, ["cam-1", "cam-2"])
    sg.incident = incident


def apply_fix(sg: SentinelGraph):
    """
    Simulate adding a redundant Switch-B → Cam-2 path.
    Cam-2 comes back online via Switch-B; Switch-A still offline.
    # Simulated graph mutation. Production would reconfigure actual network paths.
    """
    if not sg.incident or sg.incident.fix_applied:
        return False
    # Add redundant edge: cam-2 → switch-b
    sg.graph.add_edge("cam-2", "switch-b")
    sg.fix_edge_added = True
    sg.devices["cam-2"].status = "online"
    sg.suppressed_devices.discard("cam-2")
    sg.incident.fix_applied = True
    sg.incident.recommendation = (
        "Mitigation applied. Loading Dock now has a redundant network path via Switch-B."
    )
    return True


def reset(sg: SentinelGraph):
    for dev in sg.devices.values():
        dev.status = "online"
        dev.obstructed = False
    sg.suppressed_devices.clear()
    sg.incident = None
    if sg.fix_edge_added:
        if sg.graph.has_edge("cam-2", "switch-b"):
            sg.graph.remove_edge("cam-2", "switch-b")
        sg.fix_edge_added = False
