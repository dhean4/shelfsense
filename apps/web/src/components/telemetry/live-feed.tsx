"use client";

import { useEffect, useRef, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { useApi } from "@/lib/api.client";

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

interface FeedEvent {
  type: "reading" | "anomaly_opened" | "anomaly_resolved";
  device_id: string;
  device_external_id: string;
  recorded_at?: string;
  temperature_c?: number | null;
  latitude?: number | null;
  longitude?: number | null;
  peak_temperature_c?: number | null;
  started_at?: string;
  ended_at?: string;
}

/** Consumes the SSE stream with fetch (EventSource cannot send auth headers). */
export function LiveFeed({ labels }: { labels: Record<string, string> }) {
  const api = useApi();
  const [events, setEvents] = useState<FeedEvent[]>([]);
  const [state, setState] = useState<"connecting" | "live" | "closed">("connecting");
  const identity = api.identity;
  const abort = useRef<AbortController | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    abort.current = controller;
    let cancelled = false;

    async function connect() {
      const headers: Record<string, string> = identity
        ? {
            "X-Dev-Tenant": identity.tenant,
            "X-Dev-Role": identity.role,
            "X-Dev-User": identity.user,
          }
        : {};
      try {
        const response = await fetch(`${API_URL}/v1/telemetry/stream`, {
          headers,
          signal: controller.signal,
        });
        if (!response.ok || !response.body) {
          setState("closed");
          return;
        }
        setState("live");
        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let buffer = "";
        while (!cancelled) {
          const { value, done } = await reader.read();
          if (done) break;
          buffer += decoder.decode(value, { stream: true });
          let index: number;
          while ((index = buffer.indexOf("\n\n")) !== -1) {
            const frame = buffer.slice(0, index);
            buffer = buffer.slice(index + 2);
            const dataLine = frame.split("\n").find((line) => line.startsWith("data: "));
            if (!dataLine) continue;
            try {
              const event = JSON.parse(dataLine.slice(6)) as FeedEvent;
              if (event.type) setEvents((prev) => [event, ...prev].slice(0, 40));
            } catch {
              // ignore malformed frames
            }
          }
        }
      } catch {
        // aborted or network error
      } finally {
        if (!cancelled) setState("closed");
      }
    }

    void connect();
    return () => {
      cancelled = true;
      controller.abort();
    };
  }, [identity]);

  return (
    <Card>
      <CardHeader className="flex flex-row items-center gap-2">
        <CardTitle className="text-base">Live feed</CardTitle>
        <Badge variant={state === "live" ? "default" : "outline"}>{state}</Badge>
      </CardHeader>
      <CardContent>
        {events.length === 0 ? (
          <p className="text-sm text-muted-foreground">Waiting for readings…</p>
        ) : (
          <ul className="max-h-72 space-y-1 overflow-y-auto font-mono text-xs">
            {events.map((event, i) => (
              <li key={`${event.device_id}-${String(i)}`} className="flex gap-3">
                <span className="text-muted-foreground">
                  {new Date(
                    event.recorded_at ?? event.started_at ?? event.ended_at ?? Date.now(),
                  ).toLocaleTimeString()}
                </span>
                <span className="w-56 truncate">
                  {labels[event.device_id] ?? event.device_external_id}
                </span>
                {event.type === "reading" ? (
                  <span>
                    {event.temperature_c != null ? `${event.temperature_c.toFixed(1)}°C` : ""}
                    {event.latitude != null && event.longitude != null
                      ? ` @ ${event.latitude.toFixed(4)}, ${event.longitude.toFixed(4)}`
                      : ""}
                  </span>
                ) : (
                  <span className={event.type === "anomaly_opened" ? "text-destructive" : ""}>
                    {event.type.replace("_", " ")}
                    {event.peak_temperature_c != null
                      ? ` (peak ${event.peak_temperature_c.toFixed(1)}°C)`
                      : ""}
                  </span>
                )}
              </li>
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}
