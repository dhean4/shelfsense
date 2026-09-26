"use client";

import { useEffect } from "react";

/** Registers the service worker in production builds only (dev would cache stale chunks). */
export function PwaRegister() {
  useEffect(() => {
    if (process.env.NODE_ENV !== "production" || !("serviceWorker" in navigator)) return;
    navigator.serviceWorker.register("/sw.js").catch(() => {
      // Registration failing must never affect the app.
    });
  }, []);
  return null;
}
