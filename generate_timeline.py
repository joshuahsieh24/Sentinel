"""
One-shot script to generate assets/network_timeline.json.
Run once: python generate_timeline.py
Commit the output. Do not run at runtime.

Models 120 seconds of network traffic mirroring what scapy would emit
parsing baseline.pcap from Cisco Packet Tracer Simulation Mode.
"""
import json
import random
from pathlib import Path

random.seed(42)

DURATION_MS = 120_000
DEVICES = ["cam-1", "cam-2", "cam-3", "switch-a", "switch-b", "gateway"]
CAMERAS = ["cam-1", "cam-2", "cam-3"]
GATEWAY = "gateway"
BROADCAST = "broadcast"

events: list[dict] = []


def add(ts: int, type_: str, src: str, dst: str, summary: str):
    events.append({
        "ts_offset_ms": ts,
        "type": type_,
        "src": src,
        "dst": dst,
        "summary": summary,
    })


# ARP — every device every 3–5s
for device in DEVICES:
    t = random.randint(0, 2000)
    while t < DURATION_MS:
        add(t, "ARP", device, BROADCAST, f"who-has 10.0.0.1 tell {device}")
        t += random.randint(3000, 5000)

# ICMP — each camera every 1–2s
for cam in CAMERAS:
    t = random.randint(0, 500)
    seq = 1
    while t < DURATION_MS:
        add(t, "ICMP", cam, GATEWAY, f"echo request seq={seq}")
        t += random.randint(1000, 2000)
        seq += 1

# RTP heartbeats — each camera every 100ms
for cam in CAMERAS:
    t = 0
    seq = 1
    while t < DURATION_MS:
        add(t, "RTP", cam, GATEWAY, f"stream heartbeat seq={seq}")
        t += 100
        seq += 1

# DHCP renewals — each device every 30s
for device in DEVICES:
    t = random.randint(0, 5000)
    while t < DURATION_MS:
        add(t, "DHCP", device, BROADCAST, f"request lease renewal {device}")
        t += 30_000

events.sort(key=lambda e: e["ts_offset_ms"])

out = Path(__file__).parent / "assets" / "network_timeline.json"
out.write_text(json.dumps(events, indent=2))
print(f"Generated {len(events)} events → {out}")
