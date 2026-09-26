"use client";

import { useMutation, useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import { useApi } from "@/lib/api.client";
import type { PhotoOut } from "@/lib/types";

interface StoreWithShelves {
  id: string;
  name: string;
  shelves: { id: string; label: string }[];
}

const REMEMBER = "ss-upload-last";

/**
 * Field-agent flow, one thumb: pick store and shelf (remembered), open the camera, confirm,
 * upload, watch the audit land. Everything is a large touch target; state is announced.
 */
export function MobileUploader({ stores }: { stores: StoreWithShelves[] }) {
  const api = useApi();
  const [storeId, setStoreId] = useState(stores[0]?.id ?? "");
  const [shelfId, setShelfId] = useState(stores[0]?.shelves[0]?.id ?? "");
  const [file, setFile] = useState<File | null>(null);
  const [preview, setPreview] = useState<string | null>(null);
  const [photoId, setPhotoId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    try {
      const raw = localStorage.getItem(REMEMBER);
      if (raw) {
        const saved = JSON.parse(raw) as { storeId: string; shelfId: string };
        if (stores.some((s) => s.id === saved.storeId)) {
          setStoreId(saved.storeId);
          setShelfId(saved.shelfId);
        }
      }
    } catch {
      // storage unavailable
    }
  }, [stores]);

  useEffect(() => {
    if (!file) {
      setPreview(null);
      return;
    }
    const url = URL.createObjectURL(file);
    setPreview(url);
    return () => URL.revokeObjectURL(url);
  }, [file]);

  const store = stores.find((s) => s.id === storeId);
  const shelves = store?.shelves ?? [];

  const upload = useMutation({
    mutationFn: () => {
      if (!file) throw new Error("take a photo first");
      const form = new FormData();
      form.append("file", file);
      return api.upload<PhotoOut>(`/v1/shelves/${shelfId}/photos`, form);
    },
    onSuccess: (photo) => {
      setPhotoId(photo.id);
      setError(null);
      try {
        localStorage.setItem(REMEMBER, JSON.stringify({ storeId, shelfId }));
      } catch {
        // storage unavailable
      }
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

  function reset() {
    setFile(null);
    setPhotoId(null);
    setError(null);
  }

  return (
    <div className="mx-auto max-w-md space-y-4">
      <h1 className="text-xl font-semibold">Shelf photo</h1>
      <div className="grid gap-3">
        <label className="grid gap-1 text-sm">
          <span className="font-medium">Store</span>
          <select
            value={storeId}
            onChange={(e) => {
              setStoreId(e.target.value);
              const first = stores.find((s) => s.id === e.target.value)?.shelves[0]?.id ?? "";
              setShelfId(first);
            }}
            className="h-12 rounded-md border bg-background px-3 text-base"
          >
            {stores.map((s) => (
              <option key={s.id} value={s.id}>
                {s.name}
              </option>
            ))}
          </select>
        </label>
        <label className="grid gap-1 text-sm">
          <span className="font-medium">Shelf</span>
          <select
            value={shelfId}
            onChange={(e) => setShelfId(e.target.value)}
            className="h-12 rounded-md border bg-background px-3 text-base"
          >
            {shelves.map((s) => (
              <option key={s.id} value={s.id}>
                {s.label}
              </option>
            ))}
          </select>
        </label>
      </div>

      {photoId === null ? (
        <>
          <label className="block">
            <span className="sr-only">Take a photo of the shelf</span>
            <input
              type="file"
              accept="image/*"
              capture="environment"
              onChange={(e) => setFile(e.target.files?.[0] ?? null)}
              className="sr-only"
              id="shelf-photo"
            />
            <span
              role="presentation"
              className="flex h-14 w-full cursor-pointer items-center justify-center rounded-md border-2 border-dashed text-base"
              onClick={() => document.getElementById("shelf-photo")?.click()}
            >
              {file ? "Retake photo" : "📷 Take photo"}
            </span>
          </label>
          {preview ? (
            // eslint-disable-next-line @next/next/no-img-element
            <img
              src={preview}
              alt="Preview of the shelf photo you took"
              className="w-full rounded-md border"
            />
          ) : null}
          <Button
            className="h-14 w-full text-base"
            onClick={() => upload.mutate()}
            disabled={!file || !shelfId || upload.isPending}
          >
            {upload.isPending ? "Uploading…" : "Upload"}
          </Button>
          {error ? (
            <p role="alert" className="text-sm text-destructive">
              {error}
            </p>
          ) : null}
        </>
      ) : (
        <div className="space-y-3" aria-live="polite">
          <p className="text-base">
            {photo?.status === "done"
              ? "✓ Audit complete."
              : photo?.status === "failed"
                ? `✗ Failed: ${photo.error ?? "unknown error"}`
                : "Uploaded. Auditing the shelf…"}
          </p>
          {photo?.extraction ? (
            <ul className="rounded-md border p-3 text-sm">
              <li>
                {photo.extraction.summary.stock_out_count} stock-out(s) of{" "}
                {photo.extraction.summary.slots.length} slots
              </li>
              <li>compliance {(photo.extraction.summary.compliance_rate * 100).toFixed(0)}%</li>
              <li>confidence {photo.extraction.overall_confidence.toFixed(2)}</li>
              {photo.extraction.summary.slots
                .filter((s) => s.status !== "ok")
                .map((s) => (
                  <li key={s.sku_id} className="text-muted-foreground">
                    {s.status === "out" ? "out" : "low"}: {s.name}
                  </li>
                ))}
            </ul>
          ) : null}
          <Button className="h-14 w-full text-base" variant="outline" onClick={reset}>
            Take another
          </Button>
        </div>
      )}
    </div>
  );
}
