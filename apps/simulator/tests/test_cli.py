import json
from datetime import UTC, datetime, timedelta

from typer.testing import CliRunner

from shelfsense_simulator.cli import app, build_simulation
from shelfsense_simulator.fleet import DEFAULT_FLEET, Excursion, Simulation

runner = CliRunner()


def test_no_args_prints_help() -> None:
    result = runner.invoke(app, [])
    assert "Publish synthetic" in result.output


def test_fleet_command_lists_seeded_device_ids() -> None:
    result = runner.invoke(app, ["fleet"])
    assert result.exit_code == 0
    ids = {d["external_id"] for d in json.loads(result.output)}
    assert {"fridge-ikeja-depot-shop", "van-lagos-fresh-1", "fridge-surulere-chill-store"} <= ids


def test_dry_run_prints_topics_and_payloads() -> None:
    result = runner.invoke(app, ["run", "--dry-run", "--steps", "2", "--tenant", "lagos-fresh"])
    assert result.exit_code == 0, result.output
    lines = [line for line in result.output.splitlines() if line.startswith("shelfsense/")]
    assert len(lines) == 2 * 3  # 3 devices for lagos-fresh, 2 steps
    topic, payload = lines[0].split(" ", 1)
    assert topic == "shelfsense/lagos-fresh/telemetry/fridge-ikeja-depot-shop"
    body = json.loads(payload)
    assert {"recorded_at", "temperature_c", "latitude", "longitude", "battery_pct"} <= set(body)


def test_simulation_is_deterministic() -> None:
    start = datetime(2026, 9, 26, 12, 0, tzinfo=UTC)
    a = Simulation(seed=3, interval=timedelta(seconds=10), start_at=start)
    b = Simulation(seed=3, interval=timedelta(seconds=10), start_at=start)
    for _ in range(5):
        assert [r.payload() for r in a.step()] == [r.payload() for r in b.step()]


def test_fridges_hover_near_set_point_without_an_excursion() -> None:
    sim = Simulation(fleet=tuple(d for d in DEFAULT_FLEET if d.kind == "fridge"), seed=1)
    temps = [r.temperature_c for _ in range(200) for r in sim.step()]
    assert all(t is not None and 0 < t < 9 for t in temps)
    assert sum(1 for t in temps if t is not None and t > 8) < 10  # door openings are rare


def test_scripted_excursion_exceeds_the_rule() -> None:
    fridge = "fridge-ikeja-depot-shop"
    sim = build_simulation(
        tenants=["lagos-fresh"],
        interval_seconds=30,
        excursion_device=fridge,
        excursion_after_minutes=1,
        excursion_minutes=20,
        seed=7,
    )
    above: list[tuple[timedelta, float]] = []
    for _ in range(60):  # 30 simulated minutes
        for r in sim.step():
            if (
                r.device_external_id == fridge
                and r.temperature_c is not None
                and r.temperature_c > 8
            ):
                above.append((sim.now - sim.start_at, r.temperature_c))
    assert above, "excursion never crossed 8°C"
    span = above[-1][0] - above[0][0]
    assert span >= timedelta(minutes=15)
    assert max(t for _, t in above) > 10


def test_vehicle_moves_along_its_route() -> None:
    van = next(d for d in DEFAULT_FLEET if d.external_id == "van-lagos-fresh-1")
    sim = Simulation(fleet=(van,), seed=2, interval=timedelta(minutes=5))
    points = [(r.latitude, r.longitude) for _ in range(24) for r in sim.step()]
    lats = [p[0] for p in points if p[0] is not None]
    assert max(lats) - min(lats) > 0.02  # it actually travelled between Ikeja and Yaba
    assert all(6.50 < lat < 6.61 for lat in lats)


def test_build_simulation_rejects_unknown_excursion_device() -> None:
    import pytest
    import typer

    with pytest.raises(typer.BadParameter):
        build_simulation(
            tenants=[],
            interval_seconds=30,
            excursion_device="van-lagos-fresh-1",
            excursion_after_minutes=1,
            excursion_minutes=5,
            seed=1,
        )


def test_excursion_dataclass_defaults() -> None:
    e = Excursion("fridge-x", timedelta(minutes=1), timedelta(minutes=5))
    assert e.peak_c == 11.0
