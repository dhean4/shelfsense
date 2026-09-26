"use client";

import { useMemo, useState } from "react";

import type { UsageBucket } from "@/lib/types";

/**
 * Daily spend, stacked by agent. Inline SVG, no chart library.
 *
 * Follows the data-viz method: two categorical series in fixed slot order (vision = slot 1
 * blue, planner = slot 2 orange; validated for CVD and contrast in both modes), <=24px
 * columns with rounded caps, a 2px surface gap between stacked segments, hairline
 * gridlines, a legend for two series, a hover tooltip, and a table view for accessibility.
 */

const SERIES = [
  { kind: "vision", label: "Vision", light: "#2a78d6", dark: "#3987e5" },
  { kind: "planner", label: "Planner", light: "#eb6834", dark: "#d95926" },
] as const;

const W = 720;
const H = 220;
const PAD = { top: 12, right: 12, bottom: 28, left: 44 };

function dayKeys(days: number): string[] {
  const out: string[] = [];
  const today = new Date();
  for (let i = days - 1; i >= 0; i--) {
    const d = new Date(today);
    d.setDate(today.getDate() - i);
    out.push(d.toISOString().slice(0, 10));
  }
  return out;
}

function niceMax(v: number): number {
  if (v <= 0) return 1;
  const p = 10 ** Math.floor(Math.log10(v));
  const m = v / p;
  const step = m <= 1 ? 1 : m <= 2 ? 2 : m <= 5 ? 5 : 10;
  return step * p;
}

export function CostChart({ buckets, days }: { buckets: UsageBucket[]; days: number }) {
  const [hover, setHover] = useState<string | null>(null);
  const [showTable, setShowTable] = useState(false);

  const keys = useMemo(() => dayKeys(days), [days]);
  const byDay = useMemo(() => {
    const map = new Map<string, Record<string, number>>();
    for (const k of keys) map.set(k, { vision: 0, planner: 0 });
    for (const b of buckets) {
      const row = map.get(b.key);
      if (row) row[b.kind] = (row[b.kind] ?? 0) + b.cost_usd;
    }
    return map;
  }, [buckets, keys]);

  const totals = keys.map((k) => {
    const row = byDay.get(k) ?? {};
    return Object.values(row).reduce((a, b) => a + b, 0);
  });
  const max = niceMax(Math.max(...totals, 0.01));
  const plotW = W - PAD.left - PAD.right;
  const plotH = H - PAD.top - PAD.bottom;
  const band = plotW / keys.length;
  const barW = Math.min(24, band * 0.6);
  const y = (v: number) => PAD.top + plotH - (v / max) * plotH;
  const ticks = [0, max / 2, max];
  const labelEvery = days > 14 ? Math.ceil(days / 7) : 1;

  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-center gap-4 text-xs text-muted-foreground">
        {SERIES.map((s) => (
          <span key={s.kind} className="inline-flex items-center gap-1.5">
            <span
              aria-hidden
              className="inline-block h-2.5 w-2.5 rounded-sm"
              style={{ background: `light-dark(${s.light}, ${s.dark})` }}
            />
            {s.label}
          </span>
        ))}
        <button
          type="button"
          onClick={() => setShowTable((v) => !v)}
          className="ml-auto underline-offset-2 hover:underline"
        >
          {showTable ? "Show chart" : "Show table"}
        </button>
      </div>
      {showTable ? (
        <table className="w-full text-sm">
          <thead>
            <tr className="text-left text-xs text-muted-foreground">
              <th className="py-1">Day</th>
              {SERIES.map((s) => (
                <th key={s.kind} className="py-1 text-right">
                  {s.label}
                </th>
              ))}
              <th className="py-1 text-right">Total</th>
            </tr>
          </thead>
          <tbody>
            {keys.map((k, i) => (
              <tr key={k} className="border-t">
                <td className="py-1 tabular-nums">{k}</td>
                {SERIES.map((s) => (
                  <td key={s.kind} className="py-1 text-right tabular-nums">
                    ${(byDay.get(k)?.[s.kind] ?? 0).toFixed(3)}
                  </td>
                ))}
                <td className="py-1 text-right tabular-nums">${(totals[i] ?? 0).toFixed(3)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : (
        <div className="relative">
          <svg
            viewBox={`0 0 ${String(W)} ${String(H)}`}
            className="h-auto w-full"
            role="img"
            aria-label="Daily spend by agent"
          >
            {ticks.map((t) => (
              <g key={t}>
                <line
                  x1={PAD.left}
                  x2={W - PAD.right}
                  y1={y(t)}
                  y2={y(t)}
                  stroke="currentColor"
                  strokeOpacity={0.12}
                  strokeWidth={1}
                />
                <text
                  x={PAD.left - 6}
                  y={y(t) + 3}
                  textAnchor="end"
                  fontSize={10}
                  fill="currentColor"
                  fillOpacity={0.6}
                  style={{ fontVariantNumeric: "tabular-nums" }}
                >
                  ${t.toFixed(2)}
                </text>
              </g>
            ))}
            {keys.map((k, i) => {
              const row = byDay.get(k) ?? {};
              const x = PAD.left + band * i + (band - barW) / 2;
              let cursor = y(0);
              const segments = SERIES.map((s) => {
                const v = row[s.kind] ?? 0;
                const h = (v / max) * plotH;
                const top = cursor - h;
                cursor = top;
                return { ...s, v, h, top };
              });
              const isHover = hover === k;
              return (
                <g key={k} onMouseEnter={() => setHover(k)} onMouseLeave={() => setHover(null)}>
                  {/* hit target wider than the mark */}
                  <rect
                    x={PAD.left + band * i}
                    y={PAD.top}
                    width={band}
                    height={plotH}
                    fill="transparent"
                  />
                  {segments.map((s, j) => {
                    if (s.h <= 0) return null;
                    const gap = j > 0 ? 2 : 0; // surface gap between stacked segments
                    const isTop = segments.slice(j + 1).every((n) => n.h <= 0);
                    const r = isTop ? 4 : 0;
                    const hh = Math.max(0, s.h - gap);
                    const yy = s.top;
                    // rounded cap only at the data end (top), square at the baseline
                    const path = `M${String(x)},${String(yy + hh)} V${String(yy + r)} Q${String(x)},${String(yy)} ${String(x + r)},${String(yy)} H${String(x + barW - r)} Q${String(x + barW)},${String(yy)} ${String(x + barW)},${String(yy + r)} V${String(yy + hh)} Z`;
                    return (
                      <path
                        key={s.kind}
                        d={path}
                        style={{ fill: `light-dark(${s.light}, ${s.dark})` }}
                        opacity={hover && !isHover ? 0.55 : 1}
                      />
                    );
                  })}
                  {i % labelEvery === 0 ? (
                    <text
                      x={x + barW / 2}
                      y={H - 10}
                      textAnchor="middle"
                      fontSize={10}
                      fill="currentColor"
                      fillOpacity={0.6}
                    >
                      {k.slice(5)}
                    </text>
                  ) : null}
                </g>
              );
            })}
            <line
              x1={PAD.left}
              x2={W - PAD.right}
              y1={y(0)}
              y2={y(0)}
              stroke="currentColor"
              strokeOpacity={0.25}
              strokeWidth={1}
            />
          </svg>
          {hover ? (
            <div className="pointer-events-none absolute right-2 top-2 rounded-md border bg-popover px-2 py-1 text-xs shadow-sm">
              <div className="font-medium">{hover}</div>
              {SERIES.map((s) => (
                <div key={s.kind} className="flex justify-between gap-4 tabular-nums">
                  <span>{s.label}</span>
                  <span>${(byDay.get(hover)?.[s.kind] ?? 0).toFixed(3)}</span>
                </div>
              ))}
            </div>
          ) : null}
        </div>
      )}
    </div>
  );
}
