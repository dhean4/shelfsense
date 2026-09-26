"""Compare two reports: the delta table for PR comments and the regression gate."""

from dataclasses import dataclass
from typing import Any

from shelfsense_evals.scoring import LOWER_IS_BETTER, PLANNER_METRICS, VISION_METRICS

MARKER = "<!-- shelfsense-eval-report -->"


@dataclass(frozen=True)
class Delta:
    """One metric's movement."""

    agent: str
    metric: str
    baseline: float | None
    current: float | None

    @property
    def change(self) -> float | None:
        """Current minus baseline."""
        if self.baseline is None or self.current is None:
            return None
        return self.current - self.baseline

    @property
    def regressed(self) -> bool:
        """Did it get worse?"""
        change = self.change
        if change is None:
            return False
        return change > 0 if self.metric in LOWER_IS_BETTER else change < 0


def deltas(baseline: dict[str, Any], current: dict[str, Any]) -> list[Delta]:
    """Every metric of both agents."""
    out: list[Delta] = []
    for agent, metrics in (("vision", VISION_METRICS), ("planner", PLANNER_METRICS)):
        base = baseline.get("summary", {}).get(agent, {})
        cur = current.get("summary", {}).get(agent, {})
        for metric in (*metrics, "total_cost_usd", "failures"):
            b = base.get(metric)
            c = cur.get(metric)
            out.append(
                Delta(
                    agent, metric, None if b is None else float(b), None if c is None else float(c)
                )
            )
    return out


def _fmt(metric: str, value: float | None) -> str:
    if value is None:
        return "—"
    if metric in ("cost_usd", "total_cost_usd"):
        return f"${value:.4f}"
    if metric == "latency_ms":
        return f"{value / 1000:.1f}s"
    if metric in ("failures", "cases"):
        return f"{value:.0f}"
    return f"{value:.3f}"


def markdown(baseline: dict[str, Any], current: dict[str, Any], *, tolerance: float) -> str:
    """A PR comment body with one table per agent."""
    lines = [
        MARKER,
        "## Eval report",
        "",
        f"Dataset `{current.get('dataset', '?')}` · provider `{current.get('provider', '?')}` · "
        f"vision `{current.get('vision_model', '?')}` · "
        f"planner `{current.get('planner_model', '?')}` · "
        f"commit `{current.get('git_sha') or '?'}` vs baseline `{baseline.get('git_sha') or '?'}`",
        "",
    ]
    for agent in ("vision", "planner"):
        rows = [d for d in deltas(baseline, current) if d.agent == agent]
        cases = current.get("summary", {}).get(agent, {}).get("cases", 0)
        lines += [
            f"### {agent} ({cases:.0f} cases)",
            "",
            "| metric | baseline | current | Δ | |",
            "|---|---:|---:|---:|:--|",
        ]
        for d in rows:
            change = d.change
            arrow = ""
            if change is not None and abs(change) > 1e-9:
                worse = d.regressed and abs(change) > tolerance * (
                    abs(d.baseline) if d.baseline else 1
                )
                arrow = "🔻 regression" if worse else ("▲" if change > 0 else "▼")
            lines.append(
                f"| {d.metric} | {_fmt(d.metric, d.baseline)} | {_fmt(d.metric, d.current)} | "
                f"{'—' if change is None else f'{change:+.4f}'} | {arrow} |"
            )
        lines.append("")
    lines.append(
        "_Lower is better for facings_mae, hallucination_rate, unneeded_action_rate, "
        "cost and latency._"
    )
    return "\n".join(lines)


def regressions(
    baseline: dict[str, Any], current: dict[str, Any], *, tolerance: float, gated: frozenset[str]
) -> list[Delta]:
    """Deltas that fail the gate: gated metrics that moved the wrong way beyond tolerance."""
    failed: list[Delta] = []
    for d in deltas(baseline, current):
        if d.metric not in gated or not d.regressed:
            continue
        change = d.change or 0.0
        allowance = tolerance * (abs(d.baseline) if d.baseline else 1.0)
        if abs(change) > allowance:
            failed.append(d)
    return failed


GATED_METRICS = frozenset(
    {
        "sku_recall",
        "stock_out_f1",
        "hallucination_rate",
        "decision_accuracy",
        "unneeded_action_rate",
    }
)
