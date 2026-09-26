"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardFooter, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { ApiClientError, useApi } from "@/lib/api.client";
import { naira, type ActionOut } from "@/lib/types";

const KIND_LABEL: Record<ActionOut["kind"], string> = {
  reorder: "Reorder",
  dispatch: "Dispatch technician",
  notify: "Notify",
  escalate: "Escalation",
};

export function ActionCard({ action }: { action: ActionOut }) {
  const api = useApi();
  const queryClient = useQueryClient();
  const [note, setNote] = useState("");
  const [quantity, setQuantity] = useState<string>(
    action.kind === "reorder" ? String(action.payload.quantity as number) : "",
  );
  const [error, setError] = useState<string | null>(null);

  const decide = useMutation({
    mutationFn: async (verb: "approve" | "reject") => {
      const body: { note?: string; quantity?: number } = {};
      if (note.trim()) body.note = note.trim();
      if (verb === "approve" && action.kind === "reorder") {
        const q = Number.parseInt(quantity, 10);
        if (!Number.isFinite(q) || q < 1)
          throw new ApiClientError(422, "quantity must be a positive number");
        if (q !== action.payload.quantity) body.quantity = q;
      }
      return api.post<ActionOut>(`/v1/actions/${action.id}/${verb}`, body);
    },
    onSuccess: () => {
      setError(null);
      void queryClient.invalidateQueries({ queryKey: ["review-queue"] });
    },
    onError: (err: unknown) => setError(err instanceof Error ? err.message : String(err)),
  });

  const payload = action.payload;
  return (
    <Card>
      <CardHeader className="flex flex-row flex-wrap items-center gap-2">
        <Badge variant="secondary">{KIND_LABEL[action.kind]}</Badge>
        <CardTitle className="text-base">
          {action.kind === "reorder"
            ? `${String(payload.quantity)} × ${String(payload.sku_name)}`
            : action.kind === "dispatch"
              ? `${String(payload.store_name)} · ${String(payload.urgency)} urgency`
              : String(payload.shelf_label ?? "")}
        </CardTitle>
        <span className="ml-auto text-sm text-muted-foreground">
          {naira(action.estimated_cost_kobo)}
        </span>
      </CardHeader>
      <CardContent className="space-y-2 text-sm">
        <p>{action.rationale}</p>
        <p className="text-muted-foreground">
          Held because: {action.review_reason ?? "—"}
          {action.confidence != null ? ` · audit confidence ${action.confidence.toFixed(2)}` : ""}
        </p>
        <div className="grid gap-2 sm:grid-cols-[8rem_1fr]">
          {action.kind === "reorder" ? (
            <Input
              type="number"
              min={1}
              value={quantity}
              onChange={(event) => setQuantity(event.target.value)}
              aria-label="Quantity"
            />
          ) : (
            <div />
          )}
          <Textarea
            value={note}
            onChange={(event) => setNote(event.target.value)}
            placeholder="Note for the record (optional)"
            rows={2}
          />
        </div>
        {error ? <p className="text-sm text-destructive">{error}</p> : null}
      </CardContent>
      <CardFooter className="gap-2">
        <Button size="sm" onClick={() => decide.mutate("approve")} disabled={decide.isPending}>
          Approve
        </Button>
        <Button
          size="sm"
          variant="outline"
          onClick={() => decide.mutate("reject")}
          disabled={decide.isPending}
        >
          Reject
        </Button>
      </CardFooter>
    </Card>
  );
}
