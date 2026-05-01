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
