"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { useApi } from "@/lib/api.client";
import { devHeaders } from "@/lib/auth";
import { consumeSse } from "@/lib/sse";

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export interface RunEvent {
  type: "run_started" | "model_turn" | "tool_result" | "vision_attempt" | "run_finished";
  run_id: string;
  kind: string;
  step?: number;
  text?: string;
  tool_calls?: string[];
  stop_reason?: string;
  latency_ms?: number;
  name?: string;
  ok?: boolean;
  error?: string | null;
  duration_ms?: number;
  attempt?: number;
  stock_outs?: number;
  confidence?: number;
  status?: string;
  cost_usd?: number | null;
}

/**
 * Live timeline for one run: the model's reasoning per turn, tool results and the finish,
 * streamed from the worker over SSE. Reasoning arrives per turn (the model calls are not
 * token-streamed); when the run finishes the server-rendered page is refreshed.
 */
export function RunLive({ runId, finished }: { runId: string; finished: boolean }) {
  const api = useApi();
  const router = useRouter();
  const [events, setEvents] = useState<RunEvent[]>([]);
  const [state, setState] = useState<"connecting" | "live" | "closed">(
    finished ? "closed" : "connecting",
  );

  useEffect(() => {
    if (finished) return;
    const controller = new AbortController();
    const headers = api.identity ? devHeaders(api.identity) : {};
    void consumeSse(
      `${API_URL}/v1/runs/stream`,
      headers,
      (_event, data) => {
        const e = data as RunEvent;
        if (e.run_id !== runId) return;
        setEvents((prev) => [...prev, e]);
        if (e.type === "run_finished") {
          controller.abort();
          setState("closed");
          router.refresh();
        }
      },
      controller.signal,
      () => setState("live"),
    ).catch(() => setState("closed"));
    return () => controller.abort();
  }, [api.identity, finished, router, runId]);

  if (finished && events.length === 0) return null;

  return (
    <Card>
      <CardHeader className="flex flex-row items-center gap-2">
        <CardTitle className="text-base">Live progress</CardTitle>
        <Badge variant={state === "live" ? "default" : "outline"}>{state}</Badge>
      </CardHeader>
      <CardContent>
        <ol className="space-y-2 text-sm" aria-live="polite" aria-relevant="additions">
          {events.length === 0 ? (
            <li className="text-muted-foreground">Waiting for the worker to pick this run up…</li>
          ) : null}
          {events.map((e, i) => (
            <li key={`${e.type}-${String(i)}`} className="flex gap-3">
              <span className="w-24 shrink-0 font-mono text-xs text-muted-foreground">
                {e.type.replace("_", " ")}
              </span>
              <span>{describe(e)}</span>
            </li>
          ))}
        </ol>
      </CardContent>
    </Card>
  );
}

export function describe(e: RunEvent): string {
  switch (e.type) {
    case "run_started":
      return `${e.kind} run started`;
    case "model_turn": {
      const calls = e.tool_calls?.length ? ` → calls ${e.tool_calls.join(", ")}` : "";
      const text = e.text
        ? `"${e.text.slice(0, 240)}${e.text.length > 240 ? "…" : ""}"`
        : "(no text)";
      return `step ${String(e.step ?? "?")}: ${text}${calls}`;
    }
    case "tool_result":
      return `${e.name ?? "tool"} ${e.ok ? "ok" : `failed: ${e.error ?? ""}`} (${String(e.duration_ms ?? 0)} ms)`;
    case "vision_attempt":
      return e.ok
        ? `attempt ${String(e.attempt ?? 1)} valid: ${String(e.stock_outs ?? 0)} stock-out(s), confidence ${(e.confidence ?? 0).toFixed(2)}`
        : `attempt ${String(e.attempt ?? 1)} rejected: ${e.error ?? ""}`;
    case "run_finished":
      return `${e.status ?? "?"}${e.cost_usd != null ? ` · $${e.cost_usd.toFixed(4)}` : ""}${e.error ? ` · ${e.error}` : ""}`;
    default:
      return "";
  }
}
