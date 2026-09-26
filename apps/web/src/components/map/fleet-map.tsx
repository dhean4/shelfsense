"use client";

import "leaflet/dist/leaflet.css";

import { CircleMarker, MapContainer, Popup, TileLayer } from "react-leaflet";

import type { DeviceOut, StoreOut } from "@/lib/types";

/**
 * Fridge health and van positions on an OpenStreetMap base. Circle markers avoid Leaflet's
 * image-based default icons, which break under bundlers. Status colors carry an icon and
 * label in the popup, never color alone.
 */
export function FleetMap({ stores, devices }: { stores: StoreOut[]; devices: DeviceOut[] }) {
  const byStore = new Map<string, DeviceOut[]>();
  for (const d of devices) {
    if (d.store_id) byStore.set(d.store_id, [...(byStore.get(d.store_id) ?? []), d]);
  }
  const vans = devices.filter(
    (d) => d.kind === "vehicle" && d.latest?.latitude != null && d.latest.longitude != null,
  );
  const center: [number, number] = stores.length
    ? [
        stores.reduce((a, s) => a + s.latitude, 0) / stores.length,
        stores.reduce((a, s) => a + s.longitude, 0) / stores.length,
      ]
    : [6.5244, 3.3792];

  return (
    <MapContainer
      center={center}
      zoom={12}
      scrollWheelZoom={false}
      className="h-80 w-full rounded-md"
      aria-label="Fridge health map"
    >
      <TileLayer
        attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
        url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
      />
      {stores.map((store) => {
        const fridges = (byStore.get(store.id) ?? []).filter((d) => d.kind === "fridge");
        const faulty = fridges.some((f) => f.open_anomaly);
        const silent = fridges.length > 0 && fridges.every((f) => !f.latest);
        const color = faulty ? "#d03b3b" : silent ? "#898781" : "#0ca30c";
        const label = faulty ? "⚠ excursion open" : silent ? "no telemetry yet" : "✓ fridges ok";
        return (
          <CircleMarker
            key={store.id}
            center={[store.latitude, store.longitude]}
            radius={10}
            pathOptions={{ color: "#fcfcfb", weight: 2, fillColor: color, fillOpacity: 0.95 }}
          >
            <Popup>
              <strong>{store.name}</strong>
              <br />
              {label}
              {fridges.map((f) => (
                <div key={f.id}>
                  {f.label}:{" "}
                  {f.latest?.temperature_c != null ? `${f.latest.temperature_c.toFixed(1)}°C` : "—"}
                </div>
              ))}
            </Popup>
          </CircleMarker>
        );
      })}
      {vans.map((van) => (
        <CircleMarker
          key={van.id}
          center={[van.latest?.latitude ?? 0, van.latest?.longitude ?? 0]}
          radius={6}
          pathOptions={{ color: "#fcfcfb", weight: 2, fillColor: "#2a78d6", fillOpacity: 0.95 }}
        >
          <Popup>
            <strong>{van.label}</strong>
            <br />
            last seen {van.latest ? new Date(van.latest.recorded_at).toLocaleTimeString() : "never"}
          </Popup>
        </CircleMarker>
      ))}
    </MapContainer>
  );
}
