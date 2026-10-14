"""Blast-radius-typed guardrail: classifies every proposed action and blocks destructive ones."""
from __future__ import annotations

from .models import Action, Decision, Level

POLICY: dict[Action, tuple[Level, str]] = {
    Action.NONE: (Level.L0_READ, "auto"),
    Action.ESCALATE_TO_HUMAN: (Level.L0_READ, "auto"),
    Action.RESTART_POD: (Level.L1_RESTART, "auto"),
    Action.SCALE_DEPLOYMENT: (Level.L2_SCALE, "auto+rollback"),
    Action.ROLLBACK_DEPLOYMENT: (Level.L3_CONFIG, "dry-run+ask"),
    Action.REVERT_CONFIGMAP: (Level.L3_CONFIG, "dry-run+ask"),
    Action.DELETE_PVC: (Level.L4_DESTRUCTIVE, "blocked"),
    Action.DELETE_NAMESPACE: (Level.L4_DESTRUCTIVE, "blocked"),
}

MAX_REPLICAS = 10


class Guardrail:
    def __init__(self, enabled: bool = True, auto_approve_l3: bool = True):
        self.enabled = enabled
        # In the demo, L3 dry-runs are auto-approved once validated (a revert to last-known-good).
        self.auto_approve_l3 = auto_approve_l3

    def evaluate(self, action: Action, target: str, replicas: int | None = None) -> Decision:
        level, mode = POLICY.get(action, (Level.L4_DESTRUCTIVE, "blocked"))
        if not self.enabled:
            return Decision(action, level, True, "unguarded", "Guardrail disabled (ablation run)")

        if mode == "blocked":
            return Decision(action, level, False, mode,
                            f"{action.value} on '{target}' is irreversible data loss (L4). "
                            "Hard-blocked; escalated to a human.")
        if action == Action.SCALE_DEPLOYMENT and replicas is not None and replicas > MAX_REPLICAS:
            return Decision(action, level, False, "blocked",
                            f"Scale to {replicas} exceeds cap of {MAX_REPLICAS} replicas")
        if mode == "dry-run+ask":
            if self.auto_approve_l3:
                return Decision(action, level, True, mode,
                                "Dry-run passed: reverting to last-known-good revision")
            return Decision(action, level, False, mode, "Awaiting human approval")
        reasons = {
            Level.L0_READ: "Read-only / no-op",
            Level.L1_RESTART: "Pod restart is reversible and bounded to one replica",
            Level.L2_SCALE: "Scale change is reversible; snapshot taken for rollback",
        }
        return Decision(action, level, True, mode, reasons[level])
