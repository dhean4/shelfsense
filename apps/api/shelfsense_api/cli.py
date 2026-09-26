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


@app.command("export-openapi")
def export_openapi(
    output: Path = typer.Option(OPENAPI_JSON, help="Where to write the generated document."),
) -> None:
    """Write the implementation's OpenAPI document (the source for generated TS types)."""
    from shelfsense_api.main import create_app

    document = create_app().openapi()
    output.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n")
    typer.echo(f"wrote {output} ({len(document['paths'])} paths)")
