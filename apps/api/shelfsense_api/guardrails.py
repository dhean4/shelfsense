"""Rules that bound what the planner may do, independent of what the model wants.

Three layers, all deterministic and testable without a model:

1. **Tool visibility by role** — the caller (or the role that triggered the run) only
   sees tools it is allowed to use. A field agent cannot dispatch a technician.
2. **Review gating** — an action goes to the human queue when the run's confidence is
   below the threshold, the estimated cost is above the limit, or the kind is one the
   triggering role may not approve on its own.
3. **Spend and step caps** — a run stops when its LLM cost or turn count exceeds the cap.
"""

from dataclasses import dataclass
from enum import StrEnum

from shelfsense_api.config import Settings


class ActionKind(StrEnum):
    """What the planner can decide to do."""

    reorder = "reorder"
    dispatch = "dispatch"
    notify = "notify"
    escalate = "escalate"


ALL_TOOLS: frozenset[str] = frozenset(
    {
        "get_inventory",
        "create_reorder",
        "dispatch_technician",
        "notify",
        "geocode",
        "submit_decisions",
    }
)

# Which tools each role may invoke, directly over HTTP/MCP or through a planner run it
# triggered. ``system`` is the worker acting on telemetry anomalies (P5).
ROLE_TOOLS: dict[str, frozenset[str]] = {
    "system": ALL_TOOLS,
    "owner": ALL_TOOLS,
    "manager": ALL_TOOLS,
    "field_agent": frozenset({"get_inventory", "create_reorder", "notify", "submit_decisions"}),
    "reviewer": frozenset({"get_inventory", "geocode", "submit_decisions"}),
}

# Actions a role may have auto-approved on its behalf. Everything else waits for review.
ROLE_AUTO_APPROVE: dict[str, frozenset[ActionKind]] = {
    "system": frozenset({ActionKind.reorder, ActionKind.notify}),
    "owner": frozenset({ActionKind.reorder, ActionKind.notify, ActionKind.dispatch}),
    "manager": frozenset({ActionKind.reorder, ActionKind.notify, ActionKind.dispatch}),
    "field_agent": frozenset({ActionKind.notify}),
    "reviewer": frozenset(),
}


def tools_for_role(role: str) -> frozenset[str]:
    """Tools ``role`` may use; unknown roles get nothing."""
    return ROLE_TOOLS.get(role, frozenset())


@dataclass(frozen=True)
class ReviewDecision:
    """Whether an action must wait for a human, and why."""

    required: bool
    reason: str | None = None


def review_decision(
    kind: ActionKind,
    estimated_cost_kobo: int,
    confidence: float | None,
    trigger_role: str,
    settings: Settings,
) -> ReviewDecision:
    """Apply the gating rules in order of severity."""
    if confidence is not None and confidence < settings.review_confidence_threshold:
        return ReviewDecision(
            True,
            f"confidence {confidence:.2f} below threshold "
            f"{settings.review_confidence_threshold:.2f}",
        )
    if estimated_cost_kobo > settings.review_cost_limit_kobo:
        return ReviewDecision(
            True,
            f"estimated cost ₦{estimated_cost_kobo / 100:,.0f} above limit "
            f"₦{settings.review_cost_limit_kobo / 100:,.0f}",
        )
    if kind == ActionKind.escalate:
        return ReviewDecision(True, "escalations always go to a human")
    if kind not in ROLE_AUTO_APPROVE.get(trigger_role, frozenset()):
        return ReviewDecision(True, f"role {trigger_role!r} cannot auto-approve {kind.value}")
    return ReviewDecision(False)


class RunAborted(RuntimeError):
    """A guardrail stopped the run. Carries the responses so the run can still be costed."""

    def __init__(self, message: str, responses: tuple[object, ...] = ()) -> None:
        """Record the message and the model responses made so far."""
        super().__init__(message)
        self.responses = responses


class CostCapExceeded(RunAborted):
    """The run's LLM spend passed ``planner_max_cost_usd``."""


class StepCapExceeded(RunAborted):
    """The run used every allowed model turn without finishing."""
