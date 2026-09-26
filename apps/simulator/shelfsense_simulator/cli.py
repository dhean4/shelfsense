"""``shelfsense-simulator``: publish synthetic fridge/GPS telemetry over MQTT."""

import asyncio
import json
import sys
from datetime import timedelta
from urllib.parse import urlparse

import aiomqtt
import typer

from shelfsense_simulator.fleet import DEFAULT_FLEET, Excursion, Reading, Simulation

app = typer.Typer(
    name="shelfsense-simulator",
    help="Publish synthetic fridge/GPS telemetry over MQTT.",
    no_args_is_help=True,
)


@app.callback()
def main() -> None:
    """Publish synthetic fridge/GPS telemetry over MQTT."""


def build_simulation(
    *,
    tenants: list[str],
    interval_seconds: float,
    excursion_device: str | None,
    excursion_after_minutes: float,
    excursion_minutes: float,
    seed: int,
) -> Simulation:
    """Assemble a simulation from CLI options (shared with tests)."""
    fleet = tuple(d for d in DEFAULT_FLEET if not tenants or d.tenant_slug in tenants)
    if not fleet:
        raise typer.BadParameter(f"no devices for tenants {tenants}")
    excursions: tuple[Excursion, ...] = ()
    if excursion_device:
        if excursion_device not in {d.external_id for d in fleet if d.kind == "fridge"}:
            raise typer.BadParameter(f"{excursion_device!r} is not a fridge in the fleet")
        excursions = (
            Excursion(
                device_external_id=excursion_device,
                start=timedelta(minutes=excursion_after_minutes),
                duration=timedelta(minutes=excursion_minutes),
            ),
        )
    return Simulation(
        fleet=fleet, excursions=excursions, interval=timedelta(seconds=interval_seconds), seed=seed
    )


async def _publish_forever(
    sim: Simulation,
    *,
    broker_url: str,
    prefix: str,
    wall_interval: float,
    max_steps: int | None,
    dry_run: bool,
) -> int:
    parsed = urlparse(broker_url)
    host, port = parsed.hostname or "localhost", parsed.port or 1883
    steps = 0

    async def emit(client: aiomqtt.Client | None, readings: list[Reading]) -> None:
        for reading in readings:
            if client is None:
                sys.stdout.write(f"{reading.topic(prefix)} {reading.payload().decode()}\n")
            else:
                await client.publish(reading.topic(prefix), reading.payload(), qos=1)

    if dry_run:
        while max_steps is None or steps < max_steps:
            await emit(None, sim.step())
            steps += 1
            if max_steps is None:
                await asyncio.sleep(wall_interval)
        return steps

    async with aiomqtt.Client(host, port, identifier="shelfsense-simulator") as client:
        typer.echo(
            f"publishing to {host}:{port} every {wall_interval}s "
            f"(simulated {sim.interval.total_seconds():.0f}s per step)",
            err=True,
        )
        while max_steps is None or steps < max_steps:
            await emit(client, sim.step())
            steps += 1
            await asyncio.sleep(wall_interval)
    return steps


@app.command()
def run(
    broker_url: str = typer.Option("mqtt://localhost:1883", envvar="SHELFSENSE_MQTT_URL"),
    prefix: str = typer.Option("shelfsense", help="Topic prefix the API subscribes to."),
    tenant: list[str] = typer.Option([], help="Tenant slugs to simulate (default: all)."),
    interval_seconds: float = typer.Option(30.0, min=1, help="Simulated seconds per step."),
    speed: float = typer.Option(
        10.0, min=0.1, help="Simulated seconds per wall second (10 = a 15-min excursion in 90s)."
    ),
    excursion_device: str | None = typer.Option(
        "fridge-ikeja-depot-shop", help="Fridge that overheats; empty to disable."
    ),
    excursion_after_minutes: float = typer.Option(2.0, min=0),
    excursion_minutes: float = typer.Option(25.0, min=1),
    steps: int | None = typer.Option(
        None, help="Stop after this many steps (default: run forever)."
    ),
    seed: int = typer.Option(7),
    dry_run: bool = typer.Option(False, help="Print readings instead of publishing."),
) -> None:
    """Start publishing telemetry."""
    sim = build_simulation(
        tenants=tenant,
        interval_seconds=interval_seconds,
        excursion_device=excursion_device or None,
        excursion_after_minutes=excursion_after_minutes,
        excursion_minutes=excursion_minutes,
        seed=seed,
    )
    wall_interval = interval_seconds / speed
    try:
        done = asyncio.run(
            _publish_forever(
                sim,
                broker_url=broker_url,
                prefix=prefix,
                wall_interval=wall_interval,
                max_steps=steps,
                dry_run=dry_run,
            )
        )
    except aiomqtt.MqttError as exc:
        typer.echo(f"shelfsense-simulator: cannot reach broker {broker_url}: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    except KeyboardInterrupt:
        raise typer.Exit(code=0) from None
    typer.echo(f"published {done} step(s)", err=True)


@app.command()
def fleet() -> None:
    """Print the default fleet as JSON (device ids match the API seed)."""
    typer.echo(
        json.dumps(
            [
                {"tenant": d.tenant_slug, "external_id": d.external_id, "kind": d.kind}
                for d in DEFAULT_FLEET
            ],
            indent=2,
        )
    )
