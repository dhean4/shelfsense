import type { Metadata, Viewport } from "next";
import "./globals.css";
import { Geist } from "next/font/google";
import type { ReactNode } from "react";

import { AppShell } from "@/components/app-shell";
import { currentDevIdentity } from "@/lib/api.server";
import { clerkEnabled } from "@/lib/auth";
import { cn } from "@/lib/utils";

import { Providers } from "./providers";

const geist = Geist({ subsets: ["latin"], variable: "--font-sans" });

export const metadata: Metadata = {
  title: "ShelfSense",
  description: "Shelf photos and cold-chain telemetry in, reviewed decisions out.",
  manifest: "/manifest.webmanifest",
  appleWebApp: { capable: true, title: "ShelfSense", statusBarStyle: "default" },
  icons: { apple: "/icons/apple-touch-icon.png" },
};

export const viewport: Viewport = {
  themeColor: "#1a5cab",
  width: "device-width",
  initialScale: 1,
};

async function ClerkWrapper({ children }: { children: ReactNode }) {
  if (!clerkEnabled) return <>{children}</>;
  const { ClerkProvider } = await import("@clerk/nextjs");
  return <ClerkProvider>{children}</ClerkProvider>;
}

export default async function RootLayout({ children }: Readonly<{ children: ReactNode }>) {
  const identity = clerkEnabled ? null : await currentDevIdentity();
  return (
    <html lang="en" className={cn("font-sans", geist.variable)}>
      <body className="min-h-screen bg-background text-foreground antialiased">
        <ClerkWrapper>
          <Providers identity={identity}>
            <AppShell identity={identity}>{children}</AppShell>
          </Providers>
        </ClerkWrapper>
      </body>
    </html>
  );
}
