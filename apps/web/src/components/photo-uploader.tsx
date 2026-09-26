"use client";

import { useMutation, useQuery } from "@tanstack/react-query";
import { useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { useApi } from "@/lib/api.client";
import type { PhotoOut } from "@/lib/types";

export function PhotoUploader({ shelves }: { shelves: { id: string; label: string }[] }) {
  const api = useApi();
  const [shelfId, setShelfId] = useState(shelves[0]?.id ?? "");
  const [file, setFile] = useState<File | null>(null);
  const [photoId, setPhotoId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const upload = useMutation({
    mutationFn: () => {
      if (!file) throw new Error("choose a photo first");
      const form = new FormData();
      form.append("file", file);
      return api.upload<PhotoOut>(`/v1/shelves/${shelfId}/photos`, form);
    },
    onSuccess: (photo) => {
      setPhotoId(photo.id);
      setError(null);
    },
    onError: (err: unknown) => setError(err instanceof Error ? err.message : String(err)),
  });

  const status = useQuery({
    queryKey: ["photo", photoId],
    queryFn: () => api.get<PhotoOut>(`/v1/photos/${photoId ?? ""}`),
    enabled: photoId !== null,
    refetchInterval: (query) => {
      const s = query.state.data?.status;
      return s === "done" || s === "failed" ? false : 2_000;
    },
  });

  const photo = status.data;
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Upload</CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        <div className="flex flex-wrap items-center gap-2">
          <select
            value={shelfId}
            onChange={(event) => setShelfId(event.target.value)}
            className="h-9 rounded-md border bg-background px-2 text-sm"
            aria-label="Shelf"
          >
            {shelves.map((shelf) => (
              <option key={shelf.id} value={shelf.id}>
                {shelf.label}
              </option>
            ))}
          </select>
          <input
            type="file"
            accept="image/jpeg,image/png,image/webp"
            onChange={(event) => setFile(event.target.files?.[0] ?? null)}
            className="text-sm"
            aria-label="Photo"
          />
          <Button
            size="sm"
            onClick={() => upload.mutate()}
            disabled={!file || !shelfId || upload.isPending}
          >
            Upload
          </Button>
        </div>
        {error ? <p className="text-sm text-destructive">{error}</p> : null}
        {photo ? (
          <div className="space-y-2 text-sm">
            <div className="flex items-center gap-2">
              <Badge
                variant={
                  photo.status === "failed"
                    ? "destructive"
                    : photo.status === "done"
                      ? "default"
                      : "secondary"
                }
              >
                {photo.status}
              </Badge>
              <span className="text-muted-foreground">
                {photo.width}×{photo.height} · {(photo.size_bytes / 1024).toFixed(0)} KB
              </span>
            </div>
            {photo.error ? <p className="text-destructive">{photo.error}</p> : null}
            {photo.extraction ? (
              <p>
                {photo.extraction.summary.stock_out_count} stock-out(s) of{" "}
                {photo.extraction.summary.slots.length} slots · compliance{" "}
                {(photo.extraction.summary.compliance_rate * 100).toFixed(0)}% · confidence{" "}
                {photo.extraction.overall_confidence.toFixed(2)} · {photo.extraction.model} · $
                {(photo.extraction.cost_usd ?? 0).toFixed(4)}
              </p>
            ) : photo.status !== "failed" ? (
              <p className="text-muted-foreground">
                Waiting for the worker (run `make worker` or `make process-jobs`)…
              </p>
            ) : null}
          </div>
        ) : null}
      </CardContent>
    </Card>
  );
}
