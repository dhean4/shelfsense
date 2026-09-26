import Link from "next/link";
import type { ReactNode } from "react";

import { DevIdentityBar } from "@/components/dev-identity-bar";
import { MainNav } from "@/components/main-nav";
import { PwaRegister } from "@/components/pwa-register";
import type { DevIdentity } from "@/lib/auth";
import { clerkEnabled } from "@/lib/auth";

async function ClerkControls() {
  if (!clerkEnabled) return null;
  const { OrganizationSwitcher, UserButton } = await import("@clerk/nextjs");
  return (
    <div className="flex items-center gap-3">
      <OrganizationSwitcher />
      <UserButton />
    </div>
  );
}

export function AppShell({
  identity,
  children,
}: {
  identity: DevIdentity | null;
  children: ReactNode;
}) {
  return (
    <div className="flex min-h-screen flex-col">
      <a
        href="#main"
        className="sr-only focus:not-sr-only focus:absolute focus:left-2 focus:top-2 focus:z-50 focus:rounded-md focus:bg-background focus:px-3 focus:py-2 focus:ring-2 focus:ring-ring"
      >
        Skip to content
      </a>
      <PwaRegister />
      <header className="border-b bg-card">
        <div className="mx-auto flex max-w-6xl flex-wrap items-center gap-x-6 gap-y-2 px-4 py-3">
          <Link href="/" className="text-lg font-semibold tracking-tight">
            ShelfSense
          </Link>
          <MainNav />
          <div className="ml-auto">
            {identity ? <DevIdentityBar identity={identity} /> : <ClerkControls />}
          </div>
        </div>
      </header>
      <main
        id="main"
        tabIndex={-1}
        className="mx-auto w-full max-w-6xl flex-1 px-4 py-6 outline-none"
      >
        {children}
      </main>
      <footer className="border-t px-4 py-3 text-center text-xs text-muted-foreground">
        ShelfSense · agentic retail &amp; cold-chain field ops
      </footer>
    </div>
  );
}
