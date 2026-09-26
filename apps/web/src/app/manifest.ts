import type { MetadataRoute } from "next";

export default function manifest(): MetadataRoute.Manifest {
  return {
    name: "ShelfSense",
    short_name: "ShelfSense",
    description: "Shelf photos and cold-chain telemetry in, reviewed decisions out.",
    start_url: "/upload",
    display: "standalone",
    background_color: "#fcfcfb",
    theme_color: "#1a5cab",
    icons: [
      { src: "/icons/icon-192.png", sizes: "192x192", type: "image/png" },
      { src: "/icons/icon-512.png", sizes: "512x512", type: "image/png" },
      {
        src: "/icons/icon-maskable-512.png",
        sizes: "512x512",
        type: "image/png",
        purpose: "maskable",
      },
    ],
  };
}
