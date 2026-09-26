"use client";

import dynamic from "next/dynamic";

import type { DeviceOut, StoreOut } from "@/lib/types";

// Leaflet touches `window` at import time, so the map only loads in the browser.
const FleetMap = dynamic(() => import("./fleet-map").then((m) => m.FleetMap), {
  ssr: false,
  loading: () => <div className="h-80 w-full animate-pulse rounded-md bg-muted" aria-hidden />,
});

export function FleetMapLoader(props: { stores: StoreOut[]; devices: DeviceOut[] }) {
  return <FleetMap {...props} />;
}
