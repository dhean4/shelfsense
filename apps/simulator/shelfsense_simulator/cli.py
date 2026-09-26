"""Command-line entry point for the telemetry simulator."""

import typer

app = typer.Typer(
    name="shelfsense-simulator",
    help="Publish synthetic fridge/GPS telemetry over MQTT.",
    no_args_is_help=True,
)

NOT_IMPLEMENTED_EXIT_CODE = 2


@app.callback()
def main() -> None:
    """Publish synthetic fridge/GPS telemetry over MQTT.

    Present so Typer keeps ``run`` as a subcommand; a lone command would otherwise become
    the root and ``no_args_is_help`` would stop applying.
    """


@app.command()
def run(
    broker_url: str = typer.Option("mqtt://localhost:1883", envvar="SHELFSENSE_MQTT_URL"),
    interval_seconds: float = typer.Option(5.0, min=0.1),
) -> None:
    """Start publishing telemetry. Arrives in P5; today this refuses loudly."""
    # TODO(P5): implement with paho-mqtt: one fridge + one vehicle per seeded store,
    # temperature drift with injected excursions (> 8°C for 15 min) so the anomaly rule
    # has something to catch, GPS along a fixed Lagos route.
    typer.echo(
        f"shelfsense-simulator: publisher arrives in P5 "
        f"(would connect to {broker_url} every {interval_seconds}s).",
        err=True,
    )
    raise typer.Exit(code=NOT_IMPLEMENTED_EXIT_CODE)
