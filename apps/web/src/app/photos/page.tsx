import { PhotoUploader } from "@/components/photo-uploader";
import { apiGetOrNull } from "@/lib/api.server";
import type { StoreOut } from "@/lib/types";

export const dynamic = "force-dynamic";

interface ShelfOut {
  id: string;
  store_id: string;
  label: string;
  position: number;
}

export default async function PhotosPage() {
  const stores = await apiGetOrNull<StoreOut[]>("/v1/stores");
  if (!stores) {
    return <p className="text-sm text-muted-foreground">Sign in to upload photos.</p>;
  }
  const shelves = await Promise.all(
    stores.map(async (store) => ({
      store,
      shelves: (await apiGetOrNull<ShelfOut[]>(`/v1/stores/${store.id}/shelves`)) ?? [],
    })),
  );
  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Shelf photos</h1>
        <p className="text-sm text-muted-foreground">
          Pick a shelf and upload a photo. The vision agent audits it and the planner decides what
          to do; watch the result on the runs page. (The mobile PWA page arrives in P8.)
        </p>
      </div>
      <PhotoUploader
        shelves={shelves.flatMap(({ store, shelves: list }) =>
          list.map((shelf) => ({ id: shelf.id, label: `${store.name} · ${shelf.label}` })),
        )}
      />
    </div>
  );
}
