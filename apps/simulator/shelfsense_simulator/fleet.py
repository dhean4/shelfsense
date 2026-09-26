"""Deterministic telemetry generators for fridges and vehicles.

Simulated time is decoupled from wall time: ``Simulation.step()`` advances the clock by
``interval`` and asks every device for one reading. Run it at ``speed`` times real time to
demonstrate the 15-minute excursion rule in seconds, or at 1x to look like a fleet.
"""

import json
import math
import random
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

DeviceKind = Literal["fridge", "vehicle"]


@dataclass(frozen=True)
class DeviceSpec:
    """One simulated device. External ids match ``shelfsense_api.seed.device_external_ids``."""

    tenant_slug: str
    external_id: str
    kind: DeviceKind
    latitude: float
    longitude: float
    route: tuple[tuple[float, float], ...] = ()


# Mirrors the seed: a fridge per store, a van per tenant. The vans drive between stores.
DEFAULT_FLEET: tuple[DeviceSpec, ...] = (
    DeviceSpec("lagos-fresh", "fridge-ikeja-depot-shop", "fridge", 6.6018, 3.3515),
    DeviceSpec("lagos-fresh", "fridge-yaba-market-store", "fridge", 6.5158, 3.3796),
    DeviceSpec(
        "lagos-fresh",
        "van-lagos-fresh-1",
        "vehicle",
        6.6018,
        3.3515,
        route=((6.6018, 3.3515), (6.5800, 3.3600), (6.5450, 3.3700), (6.5158, 3.3796)),
    ),
    DeviceSpec("surulere-chill", "fridge-surulere-chill-store", "fridge", 6.4969, 3.3546),
    DeviceSpec(
        "surulere-chill",
        "van-surulere-chill-1",
        "vehicle",
        6.4969,
        3.3546,
        route=((6.4969, 3.3546), (6.5100, 3.3650), (6.5250, 3.3500)),
    ),
)


@dataclass(frozen=True)
class Excursion:
    """A scripted fault: the fridge drifts to ``peak_c`` from ``start`` for ``duration``."""

    device_external_id: str
    start: timedelta
    duration: timedelta
    peak_c: float = 11.0


@dataclass
class Reading:
    """One sample, ready to publish."""

    tenant_slug: str
    device_external_id: str
    recorded_at: datetime
    temperature_c: float | None = None
    latitude: float | None = None
    longitude: float | None = None
    battery_pct: float | None = None

    def topic(self, prefix: str = "shelfsense") -> str:
        """MQTT topic the API's ingester subscribes to."""
        return f"{prefix}/{self.tenant_slug}/telemetry/{self.device_external_id}"

    def payload(self) -> bytes:
        """JSON body; the topic carries the ids."""
        body: dict[str, Any] = {"recorded_at": self.recorded_at.isoformat()}
        for key in ("temperature_c", "latitude", "longitude", "battery_pct"):
            value = getattr(self, key)
            if value is not None:
                body[key] = round(value, 4)
        return json.dumps(body).encode()


@dataclass
class _FridgeState:
    temp_c: float
    battery: float


@dataclass
class _VehicleState:
    progress: float  # 0..1 along the route, bounces back and forth
    direction: int
    battery: float


@dataclass
class Simulation:
    """The whole fleet's clock and state."""

    fleet: tuple[DeviceSpec, ...] = DEFAULT_FLEET
    excursions: tuple[Excursion, ...] = ()
    interval: timedelta = timedelta(seconds=30)
    start_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    seed: int = 7
    base_temp_c: float = 4.0
    _rng: random.Random = field(init=False, repr=False)
    _elapsed: timedelta = field(init=False, default=timedelta(0))
    _fridges: dict[str, _FridgeState] = field(init=False, default_factory=dict)
    _vehicles: dict[str, _VehicleState] = field(init=False, default_factory=dict)

    def __post_init__(self) -> None:
        """Seed the RNG and initial states so a run is reproducible."""
        self._rng = random.Random(self.seed)
        for spec in self.fleet:
            if spec.kind == "fridge":
                self._fridges[spec.external_id] = _FridgeState(
                    temp_c=self.base_temp_c + self._rng.uniform(-0.5, 0.5), battery=100.0
                )
            else:
                self._vehicles[spec.external_id] = _VehicleState(
                    progress=self._rng.random(), direction=1, battery=100.0
                )

    @property
    def now(self) -> datetime:
        """Current simulated time."""
        return self.start_at + self._elapsed

    def _excursion_target(self, external_id: str) -> float | None:
        for exc in self.excursions:
            if exc.device_external_id != external_id:
                continue
            if exc.start <= self._elapsed < exc.start + exc.duration:
                return exc.peak_c
        return None

    def step(self) -> list[Reading]:
        """Advance one interval and return a reading per device."""
        self._elapsed += self.interval
        now = self.now
        readings: list[Reading] = []
        for spec in self.fleet:
            if spec.kind == "fridge":
                readings.append(self._fridge_reading(spec, now))
            else:
                readings.append(self._vehicle_reading(spec, now))
        return readings

    def _fridge_reading(self, spec: DeviceSpec, now: datetime) -> Reading:
        state = self._fridges[spec.external_id]
        target = self._excursion_target(spec.external_id)
        if target is None:
            # Mean-reverting jitter around the set point, with an occasional door opening.
            state.temp_c += 0.25 * (self.base_temp_c - state.temp_c) + self._rng.gauss(0, 0.15)
            if self._rng.random() < 0.03:
                state.temp_c += self._rng.uniform(1.0, 2.5)
        else:
            state.temp_c += 0.4 * (target - state.temp_c) + self._rng.gauss(0, 0.1)
        state.battery = max(0.0, state.battery - 0.01)
        return Reading(
            tenant_slug=spec.tenant_slug,
            device_external_id=spec.external_id,
            recorded_at=now,
            temperature_c=round(state.temp_c, 2),
            latitude=spec.latitude,
            longitude=spec.longitude,
            battery_pct=round(state.battery, 2),
        )

    def _vehicle_reading(self, spec: DeviceSpec, now: datetime) -> Reading:
        state = self._vehicles[spec.external_id]
        route = spec.route or ((spec.latitude, spec.longitude),)
        # ~40 km/h across the whole route per simulated hour, bouncing between ends.
        step = (self.interval.total_seconds() / 3600) * 0.9
        state.progress += step * state.direction
        if state.progress >= 1.0 or state.progress <= 0.0:
            state.direction *= -1
            state.progress = min(1.0, max(0.0, state.progress))
        lat, lng = _interpolate(route, state.progress)
        state.battery = max(0.0, state.battery - 0.05)
        return Reading(
            tenant_slug=spec.tenant_slug,
            device_external_id=spec.external_id,
            recorded_at=now,
            latitude=lat + self._rng.gauss(0, 0.0002),
            longitude=lng + self._rng.gauss(0, 0.0002),
            battery_pct=round(state.battery, 2),
        )


def _interpolate(route: tuple[tuple[float, float], ...], progress: float) -> tuple[float, float]:
    if len(route) == 1:
        return route[0]
    segments = len(route) - 1
    position = progress * segments
    index = min(math.floor(position), segments - 1)
    t = position - index
    (lat1, lng1), (lat2, lng2) = route[index], route[index + 1]
    return lat1 + (lat2 - lat1) * t, lng1 + (lng2 - lng1) * t
