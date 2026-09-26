import { MobileUploader } from "@/components/upload/mobile-uploader";
import { apiGetOrNull } from "@/lib/api.server";
import type { StoreOut } from "@/lib/types";

export const dynamic = "force-dynamic";

interface ShelfOut {
  id: string;
  label: string;
}

export default async function UploadPage() {
  const stores = await apiGetOrNull<StoreOut[]>("/v1/stores");
  if (!stores) {
    return <p className="text-sm text-muted-foreground">Sign in to upload photos.</p>;
  }
  const withShelves = await Promise.all(
    stores.map(async (store) => ({
      id: store.id,
      name: store.name,
      shelves: (await apiGetOrNull<ShelfOut[]>(`/v1/stores/${store.id}/shelves`)) ?? [],
    })),
  );
  return <MobileUploader stores={withShelves} />;
}
