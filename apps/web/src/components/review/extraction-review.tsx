"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardFooter, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Textarea } from "@/components/ui/textarea";
import { useApi } from "@/lib/api.client";
import type {
  DetectedItem,
  ExtractionCandidateOut,
  ExtractionReviewOut,
  GoldenCaseOut,
  PlanogramSnapshot,
  ShelfExtraction,
} from "@/lib/types";

/** Build a corrected extraction from per-slot facings the reviewer typed. */
export function buildCorrection(
  original: ShelfExtraction,
  planogram: PlanogramSnapshot,
  facings: Record<string, number>,
): ShelfExtraction {
  const byId = new Map(original.items.filter((i) => i.sku_id).map((i) => [i.sku_id as string, i]));
  const items: DetectedItem[] = [];
  for (const slot of planogram.slots) {
    const count = facings[slot.sku_id] ?? 0;
    if (count <= 0) continue;
    const seen = byId.get(slot.sku_id);
    items.push({
      sku_id: slot.sku_id,
      label: seen?.label ?? `${slot.brand} ${slot.name}`,
      facings: count,
      region: seen?.region ?? { x: 0, y: 0, w: 1, h: 1 },
      confidence: 1,
    });
  }
  // Unknown products the model saw are kept as the reviewer cannot re-identify them here.
  for (const item of original.items) if (item.sku_id === null) items.push(item);
  const total = items.reduce((sum, i) => sum + i.facings, 0) || 1;
  return {
    items,
    stock_outs: planogram.slots.filter((s) => (facings[s.sku_id] ?? 0) <= 0).map((s) => s.sku_id),
    share_of_shelf: items
      .filter((i) => i.sku_id)
      .map((i) => ({ sku_id: i.sku_id as string, percent: (100 * i.facings) / total })),
    overall_confidence: 1,
    notes: `Reviewer correction. Model notes: ${original.notes}`.slice(0, 2000),
  };
}

export function ExtractionReview({ candidate }: { candidate: ExtractionCandidateOut }) {
  const api = useApi();
  const queryClient = useQueryClient();
  const planogram = candidate.planogram as unknown as PlanogramSnapshot;
  const observed = Object.fromEntries(
    candidate.summary.slots.map((s) => [s.sku_id, s.observed_facings]),
  );
  const [facings, setFacings] = useState<Record<string, number>>(observed);
  const [note, setNote] = useState("");
  const [tags, setTags] = useState("");
  const [label, setLabel] = useState<ExtractionReviewOut | null>(null);
  const [golden, setGolden] = useState<GoldenCaseOut | null>(null);
  const [error, setError] = useState<string | null>(null);

  const changed = candidate.summary.slots.some(
    (s) => (facings[s.sku_id] ?? 0) !== s.observed_facings,
  );

  const review = useMutation({
    mutationFn: (verdict: "correct" | "corrected" | "unusable") =>
      api.post<ExtractionReviewOut>(`/v1/extractions/${candidate.extraction_id}/review`, {
        verdict,
        corrected:
          verdict === "corrected"
            ? buildCorrection(candidate.extraction, planogram, facings)
            : null,
        note: note.trim() || null,
      }),
    onSuccess: (stored) => {
      setLabel(stored);
      setError(null);
      void queryClient.invalidateQueries({ queryKey: ["review-queue"] });
    },
    onError: (err: unknown) => setError(err instanceof Error ? err.message : String(err)),
  });

  const promote = useMutation({
    mutationFn: () =>
      api.post<GoldenCaseOut>(`/v1/labels/${label?.id ?? ""}/promote`, {
        tags: tags
          .split(",")
          .map((t) => t.trim())
          .filter(Boolean),
      }),
    onSuccess: (stored) => {
      setGolden(stored);
      setError(null);
    },
    onError: (err: unknown) => setError(err instanceof Error ? err.message : String(err)),
  });

  return (
    <Card>
      <CardHeader className="flex flex-row flex-wrap items-center gap-2">
        <CardTitle className="text-base">
          {candidate.store_name} · {candidate.shelf_label}
        </CardTitle>
        <Badge variant="outline">confidence {candidate.confidence.toFixed(2)}</Badge>
        <span className="text-xs text-muted-foreground">{candidate.reason}</span>
      </CardHeader>
      <CardContent className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img
          src={candidate.download_url}
          alt={`Shelf photo of ${candidate.shelf_label}`}
          className="w-full rounded-md border object-contain"
        />
        <div className="space-y-3">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>SKU</TableHead>
                <TableHead className="w-20 text-right">Expected</TableHead>
                <TableHead className="w-20 text-right">Model</TableHead>
                <TableHead className="w-24 text-right">Actual</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {candidate.summary.slots.map((slot) => (
                <TableRow key={slot.sku_id}>
                  <TableCell className="text-sm">{slot.name}</TableCell>
                  <TableCell className="text-right">{slot.expected_facings}</TableCell>
                  <TableCell className="text-right">
                    {slot.observed_facings}
                    {slot.status === "out" ? (
                      <Badge variant="destructive" className="ml-1">
                        out
                      </Badge>
                    ) : null}
                  </TableCell>
                  <TableCell className="text-right">
                    <Input
                      type="number"
                      min={0}
                      value={facings[slot.sku_id] ?? 0}
                      onChange={(event) =>
                        setFacings({
                          ...facings,
                          [slot.sku_id]: Number.parseInt(event.target.value || "0", 10),
                        })
                      }
                      className="h-8 w-20 text-right"
                      aria-label={`Actual facings for ${slot.name}`}
                      disabled={label !== null}
                    />
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
          <p className="text-xs text-muted-foreground">
            Model notes: {candidate.extraction.notes || "—"}
          </p>
          <Textarea
            value={note}
            onChange={(event) => setNote(event.target.value)}
            placeholder="Reviewer note (optional)"
            rows={2}
            disabled={label !== null}
          />
          {error ? <p className="text-sm text-destructive">{error}</p> : null}
        </div>
      </CardContent>
      <CardFooter className="flex flex-wrap items-center gap-2">
        {label === null ? (
          <>
            <Button
              size="sm"
              onClick={() => review.mutate("corrected")}
              disabled={!changed || review.isPending}
            >
              Submit correction
            </Button>
            <Button
              size="sm"
              variant="outline"
              onClick={() => review.mutate("correct")}
              disabled={review.isPending}
            >
              Model was right
            </Button>
            <Button
              size="sm"
              variant="ghost"
              onClick={() => review.mutate("unusable")}
              disabled={review.isPending}
            >
              Photo unusable
            </Button>
          </>
        ) : golden === null ? (
          <>
            <Badge>{label.verdict}</Badge>
            {label.verdict !== "unusable" ? (
              <>
                <Input
                  value={tags}
                  onChange={(event) => setTags(event.target.value)}
                  placeholder="tags, comma separated"
                  className="h-8 w-56"
                  aria-label="Golden set tags"
                />
                <Button size="sm" onClick={() => promote.mutate()} disabled={promote.isPending}>
                  Promote to golden set
                </Button>
              </>
            ) : (
              <span className="text-sm text-muted-foreground">
                Recorded; not eligible for the golden set.
              </span>
            )}
          </>
        ) : (
          <span className="text-sm text-muted-foreground">
            Promoted as golden case {golden.id.slice(0, 8)}… with tags {golden.tags.join(", ")}.
          </span>
        )}
      </CardFooter>
    </Card>
  );
}
