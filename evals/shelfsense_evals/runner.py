"""Run every case through the agents and score it. Providers come from the API's LLM layer."""

import asyncio
import json
import subprocess
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from shelfsense_api.agents.planner import PlannerFailed, PlannerInput, run_planner
from shelfsense_api.agents.tools import ToolContext
from shelfsense_api.agents.vision import VisionFailed, run_vision
from shelfsense_api.config import Settings
from shelfsense_api.guardrails import RunAborted
from shelfsense_api.llm import LLMProvider, LLMResponse
from shelfsense_api.llm.provider import LLMError
from shelfsense_evals.dataset import PlannerCase, VisionCase, load_dataset
from shelfsense_evals.fake_tools import CaseToolExecutor
from shelfsense_evals.scoring import (
    PLANNER_METRICS,
    VISION_METRICS,
    PlannerScore,
    VisionScore,
    aggregate,
    score_planner,
    score_vision,
)


@dataclass
class RunReport:
    """Everything a run produced, serialisable to JSON."""

    dataset: str
    provider: str
    vision_model: str
    planner_model: str
    started_at: str
    git_sha: str | None
    vision: list[dict[str, Any]] = field(default_factory=list)
    planner: list[dict[str, Any]] = field(default_factory=list)
    duration_s: float = 0.0

    @property
    def summary(self) -> dict[str, dict[str, float]]:
        """Aggregates per agent."""
        return {
            "vision": aggregate(self.vision, VISION_METRICS),
            "planner": aggregate(self.planner, PLANNER_METRICS),
        }

    def as_dict(self) -> dict[str, Any]:
        """JSON document."""
        return {
            "dataset": self.dataset,
            "provider": self.provider,
            "vision_model": self.vision_model,
            "planner_model": self.planner_model,
            "started_at": self.started_at,
            "git_sha": self.git_sha,
            "duration_s": round(self.duration_s, 1),
            "summary": self.summary,
            "vision": self.vision,
            "planner": self.planner,
        }


def _git_sha() -> str | None:
    try:
        return subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, check=True
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None


async def run_vision_case(
    provider: LLMProvider, settings: Settings, case: VisionCase, dataset_dir: Path
) -> VisionScore:
    """One photo."""
    image = (dataset_dir / case.image).read_bytes()
    ctx = case.planogram.context()
    responses: list[LLMResponse] = []
    try:
        result = await run_vision(provider, settings, image, ctx, trace_tag=f"eval:{case.id}")
    except VisionFailed as exc:
        return score_vision(case.id, None, case.truth, list(exc.responses), error=str(exc))
    except LLMError as exc:
        return score_vision(case.id, None, case.truth, responses, error=str(exc))
    return score_vision(case.id, result.extraction, case.truth, list(result.responses))


async def run_planner_case(
    provider: LLMProvider, settings: Settings, case: PlannerCase
) -> PlannerScore:
    """One decision case, with tools served from the case itself."""
    store_id = uuid.uuid5(uuid.NAMESPACE_URL, f"eval:store:{case.store_name}")
    executor = CaseToolExecutor(case, store_id)
    ctx = ToolContext(
        session=None,  # type: ignore[arg-type]  # the executor never touches it
        settings=settings,
        tenant_id=UUID(int=0),
        role=case.trigger_role,
        run_id=uuid.uuid5(uuid.NAMESPACE_URL, f"eval:run:{case.id}"),
        confidence=case.overall_confidence,
    )
    inp = PlannerInput(
        store_id=store_id,
        store_name=case.store_name,
        trigger_role=case.trigger_role,
        shelf_label=case.shelf_label,
        extraction_summary=case.summary,
        overall_confidence=case.overall_confidence,
        notes=case.notes,
        telemetry=case.telemetry,
    )
    try:
        result = await run_planner(provider, settings, ctx, inp, executor=executor, gate=False)
    except (RunAborted, PlannerFailed) as exc:
        responses = list(getattr(exc, "responses", ()))
        return score_planner(case.id, None, case.expected, responses, error=str(exc))
    except LLMError as exc:
        return score_planner(case.id, None, case.expected, [], error=str(exc))
    executor.observed.escalated = result.decisions.escalate
    return score_planner(case.id, executor.observed, case.expected, list(result.responses))


async def run_dataset(
    provider: LLMProvider,
    settings: Settings,
    dataset_path: Path,
    *,
    limit: int | None = None,
    tags: set[str] | None = None,
    kinds: set[str] | None = None,
    concurrency: int = 4,
    on_case: Any = None,
) -> RunReport:
    """Run (a filtered subset of) the dataset with bounded concurrency."""
    cases = load_dataset(dataset_path)
    if kinds:
        cases = [c for c in cases if c.kind in kinds]
    if tags:
        cases = [c for c in cases if tags & set(c.tags)]
    if limit is not None:
        cases = cases[:limit]
    report = RunReport(
        dataset=str(dataset_path),
        provider=provider.name,
        vision_model=settings.vision_model,
        planner_model=settings.planner_model,
        started_at=datetime.now(UTC).isoformat(timespec="seconds"),
        git_sha=_git_sha(),
    )
    semaphore = asyncio.Semaphore(concurrency)
    started = time.perf_counter()

    async def one(case: VisionCase | PlannerCase) -> None:
        async with semaphore:
            if isinstance(case, VisionCase):
                score: VisionScore | PlannerScore = await run_vision_case(
                    provider, settings, case, dataset_path.parent
                )
                report.vision.append(score.as_dict())
            else:
                score = await run_planner_case(provider, settings, case)
                report.planner.append(score.as_dict())
            if on_case is not None:
                on_case(case, score)

    await asyncio.gather(*(one(c) for c in cases))
    report.vision.sort(key=lambda s: str(s["case_id"]))
    report.planner.sort(key=lambda s: str(s["case_id"]))
    report.duration_s = time.perf_counter() - started
    return report


def write_report(report: RunReport, path: Path) -> None:
    """Persist as JSON."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report.as_dict(), indent=2, sort_keys=True) + "\n")


def load_report(path: Path) -> dict[str, Any]:
    """Read a report JSON."""
    data: dict[str, Any] = json.loads(path.read_text())
    return data


__all__ = ["AsyncSession", "RunReport", "load_report", "run_dataset", "write_report"]
