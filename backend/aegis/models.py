"""Shared contracts between the four workstreams (see plan: 'The three interfaces')."""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any


class Action(str, Enum):
    """M3 -> M1 contract: repair actions are a fixed enum, never free-form shell."""
    NONE = "none"
    RESTART_POD = "restart_pod"
    SCALE_DEPLOYMENT = "scale_deployment"
    REVERT_CONFIGMAP = "revert_configmap"
    ROLLBACK_DEPLOYMENT = "rollback_deployment"
    ESCALATE_TO_HUMAN = "escalate_to_human"
    # Destructive actions an LLM may *propose*; the guardrail exists to stop them.
    DELETE_PVC = "delete_pvc"
    DELETE_NAMESPACE = "delete_namespace"


class Level(str, Enum):
    L0_READ = "L0"
    L1_RESTART = "L1"
    L2_SCALE = "L2"
    L3_CONFIG = "L3"
    L4_DESTRUCTIVE = "L4"


FAULTS: dict[int, dict[str, str]] = {
    1: {"name": "Config error", "service": "cart", "expect": "auto"},
    2: {"name": "Memory leak", "service": "recommendation", "expect": "auto"},
    3: {"name": "Traffic spike", "service": "frontend", "expect": "auto"},
    4: {"name": "Dependency failure", "service": "redis", "expect": "auto"},
    5: {"name": "Disk full", "service": "redis", "expect": "blocked"},
    6: {"name": "Network latency", "service": "checkout", "expect": "diagnose"},
}


@dataclass
class IncidentContext:
    """M2 -> M3 contract."""
    timestamp: float
    affected_services: list[str]
    metrics_window: dict[str, list[float]]
    recent_logs: list[str]
    recent_events: list[str]
    predicted_load: float
    fault_id: int | None = None  # ground-truth label from fault marker (eval only)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Diagnosis:
    root_cause: str
    explanation: str
    proposed_action: Action
    target: str
    model: str = "rule-based"


@dataclass
class Decision:
    action: Action
    level: Level
    allowed: bool
    mode: str  # auto | auto+rollback | dry-run+ask | blocked
    reason: str


@dataclass
class Incident:
    id: int
    started: float
    service: str
    fault_id: int | None
    status: str = "detected"  # detected | diagnosing | repairing | resolved | blocked | escalated
    signal: str = ""
    diagnosis: Diagnosis | None = None
    decision: Decision | None = None
    resolved_at: float | None = None
    timeline: list[dict[str, Any]] = field(default_factory=list)

    @property
    def mttr(self) -> float | None:
        return None if self.resolved_at is None else round(self.resolved_at - self.started, 1)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["mttr"] = self.mttr
        return d
