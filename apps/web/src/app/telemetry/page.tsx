import { LiveFeed } from "@/components/telemetry/live-feed";
import { Badge } from "@/components/ui/badge";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { apiGetOrNull } from "@/lib/api.server";
import type { AnomalyOut, DeviceOut } from "@/lib/types";

export const dynamic = "force-dynamic";

export default async function TelemetryPage() {
  const [devices, anomalies] = await Promise.all([
    apiGetOrNull<DeviceOut[]>("/v1/devices"),
    apiGetOrNull<AnomalyOut[]>("/v1/anomalies?limit=20"),
  ]);
  if (!devices) {
    return <p className="text-sm text-muted-foreground">Sign in to see telemetry.</p>;
  }
  const labels = Object.fromEntries(devices.map((d) => [d.id, d.label]));
  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Cold chain</h1>
        <p className="text-sm text-muted-foreground">
          Fridges and vans report over MQTT; a fridge above 8°C for 15 minutes opens an anomaly and
          the planner decides what to do. Run <code className="font-mono">make simulate</code> and{" "}
          <code className="font-mono">make ingest</code> to see it live.
        </p>
      </div>
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Device</TableHead>
            <TableHead>Store</TableHead>
            <TableHead className="text-right">Temp</TableHead>
            <TableHead className="text-right">Battery</TableHead>
            <TableHead>Last seen</TableHead>
            <TableHead>State</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {devices.length === 0 ? (
            <TableRow>
              <TableCell colSpan={6} className="text-center text-muted-foreground">
                No devices registered. Seed the demo fleet with{" "}
                <code className="font-mono">make seed</code> or register one via the API.
              </TableCell>
            </TableRow>
          ) : null}
          {devices.map((device) => (
            <TableRow key={device.id}>
              <TableCell>
                <span className="font-medium">{device.label}</span>
                <span className="ml-2 font-mono text-xs text-muted-foreground">
                  {device.external_id}
                </span>
              </TableCell>
              <TableCell>{device.store_name ?? "—"}</TableCell>
              <TableCell className="text-right">
                {device.latest?.temperature_c != null
                  ? `${device.latest.temperature_c.toFixed(1)}°C`
                  : "—"}
              </TableCell>
              <TableCell className="text-right">
                {device.latest?.battery_pct != null
                  ? `${device.latest.battery_pct.toFixed(0)}%`
                  : "—"}
              </TableCell>
              <TableCell className="text-muted-foreground">
                {device.latest ? new Date(device.latest.recorded_at).toLocaleString() : "never"}
              </TableCell>
              <TableCell>
                {device.open_anomaly ? (
                  <Badge variant="destructive">
                    excursion since {new Date(device.open_anomaly.started_at).toLocaleTimeString()}
                  </Badge>
                ) : device.kind === "fridge" ? (
                  <Badge variant="secondary">ok</Badge>
                ) : (
                  <Badge variant="outline">van</Badge>
                )}
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
      <LiveFeed labels={labels} />
      <div>
        <h2 className="mb-2 text-lg font-medium">Recent anomalies</h2>
        {anomalies && anomalies.length > 0 ? (
          <ul className="space-y-1 text-sm">
            {anomalies.map((a) => (
              <li key={a.id} className="flex flex-wrap items-center gap-2 rounded-md border p-2">
                <Badge variant={a.status === "open" ? "destructive" : "secondary"}>
                  {a.status}
                </Badge>
                <span>{labels[a.device_id] ?? a.device_id}</span>
                <span className="text-muted-foreground">
                  {new Date(a.started_at).toLocaleString()}
                  {a.ended_at ? ` → ${new Date(a.ended_at).toLocaleTimeString()}` : ""}
                  {a.peak_temperature_c != null
                    ? ` · peak ${a.peak_temperature_c.toFixed(1)}°C`
                    : ""}
                </span>
                {a.run_id ? (
                  <a href={`/runs/${a.run_id}`} className="ml-auto text-xs underline">
                    planner run
                  </a>
                ) : null}
              </li>
            ))}
          </ul>
        ) : (
          <p className="text-sm text-muted-foreground">None yet.</p>
        )}
      </div>
    </div>
  );
}
