"""``shelfsense-api``: serve, migrate, seed, export-openapi."""

import asyncio
import json
from pathlib import Path

import typer
from alembic import command
from alembic.config import Config

from shelfsense_api.config import get_settings

app = typer.Typer(name="shelfsense-api", help="ShelfSense API operations.", no_args_is_help=True)

API_ROOT = Path(__file__).resolve().parent.parent
ALEMBIC_INI = API_ROOT / "alembic.ini"
OPENAPI_JSON = API_ROOT / "openapi.json"


def alembic_config(database_url: str | None = None) -> Config:
    """Alembic config pointing at this package's migrations, optionally with a URL override.

    ``migrations/env.py`` reads ``config.attributes["url"]`` before falling back to settings.
    """
    config = Config(str(ALEMBIC_INI))
    if database_url is not None:
        config.attributes["url"] = database_url
    return config


@app.command()
def serve(
    host: str = typer.Option("127.0.0.1"),
    port: int = typer.Option(8000),
    reload: bool = typer.Option(True, help="Reload on file changes (development)."),
) -> None:
    """Run the API with uvicorn."""
    import uvicorn

    uvicorn.run("shelfsense_api.main:app", host=host, port=port, reload=reload)


@app.command()
def migrate(
    revision: str = typer.Argument("head"),
    database_url: str | None = typer.Option(
        None, help="Override SHELFSENSE_MIGRATION_DATABASE_URL for this run."
    ),
) -> None:
    """Apply migrations up to REVISION (default: head)."""
    command.upgrade(alembic_config(database_url), revision)


@app.command()
def seed(
    database_url: str | None = typer.Option(
        None, help="Override SHELFSENSE_MIGRATION_DATABASE_URL for this run."
    ),
) -> None:
    """Load the deterministic demo dataset (idempotent)."""
    from shelfsense_api.seed import seed_database

    dsn = database_url or get_settings().migration_database_url
    summary = asyncio.run(seed_database(dsn))
    for line in summary:
        typer.echo(line)


@app.command()
def worker(
    consumer: str = typer.Option("worker-1", help="Consumer name in the group."),
    metrics_port: int | None = typer.Option(None, help="Serve Prometheus metrics on this port."),
) -> None:
    """Run the background job worker until SIGINT/SIGTERM."""
    import signal

    from shelfsense_api.jobs import get_queue
    from shelfsense_api.observability import configure_tracing, flush, start_metrics_server

    settings = get_settings()
    configure_tracing(settings)
    start_metrics_server(metrics_port or settings.metrics_port)

    async def _main() -> None:
        queue = get_queue(settings)
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, stop.set)
        typer.echo(f"worker {consumer} consuming {queue._stream}")
        try:
            await queue.run(consumer, stop=stop)
        finally:
            await queue.close()
            flush()

    asyncio.run(_main())


@app.command()
def ingest(
    metrics_port: int | None = typer.Option(None, help="Serve Prometheus metrics on this port."),
) -> None:
    """Subscribe to MQTT telemetry and ingest it until SIGINT/SIGTERM."""
    import logging
    import signal

    from shelfsense_api.mqtt_ingest import run_ingest
    from shelfsense_api.observability import configure_tracing, flush, start_metrics_server

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    settings = get_settings()
    configure_tracing(settings)
    start_metrics_server(metrics_port or settings.metrics_port)

    async def _main() -> None:
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, stop.set)
        try:
            await run_ingest(settings, stop=stop)
        finally:
            flush()

    asyncio.run(_main())


@app.command("process-jobs")
def process_jobs(limit: int = typer.Option(10, min=1)) -> None:
    """Handle up to LIMIT queued jobs inline, then exit (useful without a worker)."""
    from shelfsense_api.jobs import get_queue
    from shelfsense_api.observability import configure_tracing, flush

    settings = get_settings()
    configure_tracing(settings)

    async def _main() -> int:
        queue = get_queue(settings)
        handled = 0
        try:
            while handled < limit and await queue.process_one():
                handled += 1
        finally:
            await queue.close()
            flush()
        return handled

    typer.echo(f"processed {asyncio.run(_main())} job(s)")


@app.command("synth-photos")
def synth_photos(
    output: Path = typer.Option(
        API_ROOT / "tests" / "fixtures" / "photos", help="Directory for PNG + truth JSON."
    ),
) -> None:
    """Render the synthetic shelf photos with ground truth."""
    from shelfsense_api.synthetic import write_all

    for path in write_all(output):
        typer.echo(f"wrote {path}")


@app.command("record-fixtures")
def record_fixtures(
    photos: Path = typer.Option(API_ROOT / "tests" / "fixtures" / "photos"),
    fixtures: Path = typer.Option(API_ROOT / "tests" / "fixtures" / "llm"),
) -> None:
    """Call the real model on every synthetic photo and save the responses as fixtures.

    Needs ANTHROPIC_API_KEY and spends money. Re-run after changing the vision prompt,
    schema or model; the unit tests replay these files and fail on a miss.
    """
    from shelfsense_api.agents.vision import run_vision
    from shelfsense_api.llm.anthropic_provider import AnthropicProvider
    from shelfsense_api.llm.replay import RecordingProvider
    from shelfsense_api.synthetic import SCENARIOS, planogram_context

    settings = get_settings()
    provider = RecordingProvider(AnthropicProvider(), fixtures)

    async def _main() -> None:
        for scenario in SCENARIOS:
            image = (photos / f"{scenario.name}.png").read_bytes()
            ctx = planogram_context(scenario.tenant_slug, scenario.store_name, scenario.shelf_label)
            result = await run_vision(provider, settings, image, ctx, trace_tag=scenario.name)
            s = result.summary
            typer.echo(
                f"{scenario.name}: {result.attempts} call(s), "
                f"stock-outs {s.stock_out_count}/{len(s.slots)}, "
                f"confidence {result.extraction.overall_confidence:.2f}, "
                f"tokens {sum(r.usage.input_tokens for r in result.responses)}"
                f"/{sum(r.usage.output_tokens for r in result.responses)}"
            )

    asyncio.run(_main())


@app.command()
def demo(
    photos: Path = typer.Option(API_ROOT / "tests" / "fixtures" / "photos"),
    telemetry_minutes: int = typer.Option(
        60, min=0, help="Simulated minutes of telemetry to load."
    ),
) -> None:
    """Load demo activity for the seeded tenants: photos queued for audit and an hour of telemetry.

    Idempotent enough for a demo: photos are re-uploaded (new rows) each run, telemetry is
    appended. Run `make worker` (or `process-jobs`) afterwards to see the agents work.
    """
    from shelfsense_api.demo import load_demo

    summary = asyncio.run(load_demo(get_settings(), photos, telemetry_minutes))
    for line in summary:
        typer.echo(line)


@app.command("export-openapi")
def export_openapi(
    output: Path = typer.Option(OPENAPI_JSON, help="Where to write the generated document."),
) -> None:
    """Write the implementation's OpenAPI document (the source for generated TS types)."""
    from shelfsense_api.main import create_app

    document = create_app().openapi()
    output.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n")
    typer.echo(f"wrote {output} ({len(document['paths'])} paths)")
