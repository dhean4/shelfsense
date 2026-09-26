"""``shelfsense-evals``: generate, run, compare, export-golden."""

import asyncio
import json
from pathlib import Path
from typing import Literal

import typer
from rich.console import Console
from rich.table import Table

from shelfsense_api.config import Settings
from shelfsense_api.llm.provider import get_provider
from shelfsense_evals.compare import GATED_METRICS, markdown, regressions
from shelfsense_evals.dataset import load_dataset
from shelfsense_evals.runner import load_report, run_dataset, write_report

app = typer.Typer(name="shelfsense-evals", help="ShelfSense agent evals.", no_args_is_help=True)
console = Console(stderr=True)

EVALS_ROOT = Path(__file__).resolve().parent.parent
DATASET = EVALS_ROOT / "data" / "golden.jsonl"
FIXTURES = EVALS_ROOT / "fixtures" / "llm"
BASELINE = EVALS_ROOT / "baseline.json"
RESULTS = EVALS_ROOT / "results" / "latest.json"


@app.command()
def generate(
    dataset: Path = typer.Option(DATASET),
    vision: int = typer.Option(60, min=0),
    planner: int = typer.Option(40, min=0),
    seed: int = typer.Option(2026),
) -> None:
    """Render the synthetic seed dataset (photos + JSONL)."""
    from shelfsense_evals.generate import generate as _generate

    count = _generate(dataset, vision=vision, planner=planner, seed=seed)
    typer.echo(f"wrote {count} cases to {dataset}")


@app.command()
def validate(dataset: Path = typer.Option(DATASET)) -> None:
    """Check every line parses and every photo exists."""
    cases = load_dataset(dataset)
    missing = [
        c.id for c in cases if c.kind == "vision" and not (dataset.parent / c.image).exists()
    ]
    if missing:
        typer.echo(f"missing photos for {missing}", err=True)
        raise typer.Exit(code=1)
    kinds = {k: sum(1 for c in cases if c.kind == k) for k in ("vision", "planner")}
    typer.echo(f"{len(cases)} cases valid: {kinds}")


@app.command()
def run(
    dataset: Path = typer.Option(DATASET),
    provider: Literal["anthropic", "replay", "fake"] = typer.Option("replay"),
    record: bool = typer.Option(
        False,
        help=(
            "Save responses as fixtures. With provider=replay only fixture misses hit the "
            "API (incremental); with provider=anthropic every case is re-recorded."
        ),
    ),
    fixtures: Path = typer.Option(FIXTURES),
    out: Path = typer.Option(RESULTS),
    limit: int | None = typer.Option(None),
    tag: list[str] = typer.Option([], help="Only cases with any of these tags."),
    kind: list[str] = typer.Option([], help="vision, planner, or both (default)."),
    concurrency: int = typer.Option(4, min=1, max=16),
) -> None:
    """Run the dataset and write a report. ``replay`` needs recorded fixtures and costs nothing."""
    settings = Settings(llm_provider=provider, llm_record=record, llm_fixtures_dir=str(fixtures))
    llm = get_provider(settings)

    def progress(case: object, score: object) -> None:
        error = getattr(score, "error", None)
        mark = "[red]x[/red]" if error else "[green]ok[/green]"
        console.print(f"{mark} {getattr(case, 'id', '?')}" + (f" — {error}" if error else ""))

    report = asyncio.run(
        run_dataset(
            llm,
            settings,
            dataset,
            limit=limit,
            tags=set(tag) or None,
            kinds=set(kind) or None,
            concurrency=concurrency,
            on_case=progress,
        )
    )
    write_report(report, out)
    _print_summary(report.as_dict())
    typer.echo(f"wrote {out}")


def _print_summary(report: dict[str, object]) -> None:
    summary = report["summary"]
    assert isinstance(summary, dict)
    for agent, metrics in summary.items():
        if not metrics:
            continue
        table = Table(
            title=f"{agent} ({metrics['cases']:.0f} cases, {metrics['failures']:.0f} failed)"
        )
        table.add_column("metric")
        table.add_column("value", justify="right")
        for key, value in metrics.items():
            if key in ("cases", "failures"):
                continue
            table.add_row(key, f"{value:.4f}" if key != "latency_ms" else f"{value / 1000:.1f}s")
        console.print(table)


@app.command()
def compare(
    current: Path = typer.Argument(RESULTS),
    baseline: Path = typer.Option(BASELINE),
    tolerance: float = typer.Option(0.02, help="Relative drop allowed on gated metrics."),
    out_markdown: Path | None = typer.Option(None, help="Write the PR comment body here."),
    fail_on_regression: bool = typer.Option(True),
) -> None:
    """Delta table against the baseline; exit 1 on a gated regression."""
    base = load_report(baseline)
    cur = load_report(current)
    body = markdown(base, cur, tolerance=tolerance)
    if out_markdown is not None:
        out_markdown.write_text(body + "\n")
    typer.echo(body)
    failed = regressions(base, cur, tolerance=tolerance, gated=GATED_METRICS)
    if failed:
        for d in failed:
            typer.echo(f"REGRESSION {d.agent}.{d.metric}: {d.baseline} -> {d.current}", err=True)
        if fail_on_regression:
            raise typer.Exit(code=1)


@app.command("promote-baseline")
def promote_baseline(
    current: Path = typer.Argument(RESULTS), baseline: Path = typer.Option(BASELINE)
) -> None:
    """Make the latest report the baseline future PRs compare against."""
    baseline.write_text(current.read_text())
    typer.echo(f"{baseline} <- {current}")


@app.command("export-golden")
def export_golden(
    out: Path = typer.Option(EVALS_ROOT / "data" / "review" / "golden.jsonl"),
    tenant_id: str = typer.Option(..., help="Tenant whose golden cases to export."),
) -> None:
    """Pull promoted golden cases (and their photos) from the database into a dataset file."""
    from shelfsense_evals.export import export_from_database

    count = asyncio.run(export_from_database(out, tenant_id))
    typer.echo(f"exported {count} cases to {out}")


@app.command()
def show(report: Path = typer.Argument(RESULTS)) -> None:
    """Print a report's summary."""
    _print_summary(load_report(report))


if __name__ == "__main__":  # pragma: no cover
    app()

__all__ = ["app", "json"]
