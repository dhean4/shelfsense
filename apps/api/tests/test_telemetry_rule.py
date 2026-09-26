from datetime import UTC, datetime, timedelta

from shelfsense_api.mqtt_ingest import broker_from_url, parse_payload, parse_topic
from shelfsense_api.telemetry import Sample, excursion_start

T0 = datetime(2026, 9, 26, 10, 0, tzinfo=UTC)
LIMIT = 8.0
DURATION = timedelta(minutes=15)


def _series(temps: list[float], step_minutes: int = 5) -> list[Sample]:
    return [Sample(T0 + timedelta(minutes=i * step_minutes), t) for i, t in enumerate(temps)]


def test_no_samples_or_cool_latest_is_no_excursion() -> None:
    assert excursion_start([], LIMIT, DURATION) is None
    assert excursion_start(_series([9, 9, 9, 7.5]), LIMIT, DURATION) is None


def test_short_spike_is_not_an_excursion() -> None:
    # Above the limit for 10 minutes only.
    assert excursion_start(_series([4, 4, 9, 9.5, 10]), LIMIT, DURATION) is None


def test_sustained_excursion_reports_its_start() -> None:
    samples = _series([4, 4.5, 9, 9.5, 10, 10.5])  # above from index 2 (10:10) to 10:25
    assert excursion_start(samples, LIMIT, DURATION) == T0 + timedelta(minutes=10)


def test_excursion_restarts_after_a_cool_reading() -> None:
    samples = _series([9, 9, 9, 9, 7, 9, 9])  # cool at 10:20; trailing run is only 10 min
    assert excursion_start(samples, LIMIT, DURATION) is None


def test_boundary_at_exactly_the_duration_counts() -> None:
    samples = _series([9, 9, 9, 9])  # 10:00 → 10:15
    assert excursion_start(samples, LIMIT, DURATION) == T0


def test_parse_topic() -> None:
    parts = parse_topic("shelfsense/lagos-fresh/telemetry/fridge-1", "shelfsense")
    assert parts is not None and parts.tenant_slug == "lagos-fresh"
    assert parts.device_external_id == "fridge-1"
    assert parse_topic("other/lagos-fresh/telemetry/fridge-1", "shelfsense") is None
    assert parse_topic("shelfsense/lagos-fresh/status/fridge-1", "shelfsense") is None
    assert parse_topic("shelfsense//telemetry/fridge-1", "shelfsense") is None


def test_parse_payload_takes_device_from_topic_and_validates() -> None:
    reading = parse_payload(
        "fridge-1",
        b'{"recorded_at": "2026-09-26T10:00:00Z", "temperature_c": 4.2, '
        b'"device_external_id": "spoofed"}',
    )
    assert reading is not None
    assert reading.device_external_id == "fridge-1"
    assert reading.temperature_c == 4.2
    assert parse_payload("fridge-1", b"not json") is None
    assert parse_payload("fridge-1", b'{"temperature_c": 4}') is None  # no recorded_at
    assert (
        parse_payload("fridge-1", b'{"recorded_at": "2026-09-26T10:00:00Z", "temperature_c": 500}')
        is None
    )


def test_naive_timestamps_are_treated_as_utc() -> None:
    reading = parse_payload("f", b'{"recorded_at": "2026-09-26T10:00:00"}')
    assert reading is not None and reading.recorded_at.tzinfo is not None


def test_broker_from_url() -> None:
    assert broker_from_url("mqtt://broker.local:1884") == ("broker.local", 1884)
    assert broker_from_url("mqtt://localhost") == ("localhost", 1883)
