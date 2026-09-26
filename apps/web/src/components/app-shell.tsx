import Link from "next/link";
import type { ReactNode } from "react";

import { DevIdentityBar } from "@/components/dev-identity-bar";
import type { DevIdentity } from "@/lib/auth";
import { clerkEnabled } from "@/lib/auth";

const NAV = [
  { href: "/", label: "Dashboard" },
  { href: "/review", label: "Review queue" },
  { href: "/runs", label: "Agent runs" },
  { href: "/photos", label: "Photos" },
  { href: "/telemetry", label: "Cold chain" },
] as const;

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
      <header className="border-b bg-card">
        <div className="mx-auto flex max-w-6xl flex-wrap items-center gap-x-6 gap-y-2 px-4 py-3">
          <Link href="/" className="text-lg font-semibold tracking-tight">
            ShelfSense
          </Link>
          <nav className="flex flex-wrap gap-4 text-sm text-muted-foreground">
            {NAV.map((item) => (
              <Link key={item.href} href={item.href} className="hover:text-foreground">
                {item.label}
              </Link>
            ))}
          </nav>
          <div className="ml-auto">
            {identity ? <DevIdentityBar identity={identity} /> : <ClerkControls />}
          </div>
        </div>
      </header>
      <main className="mx-auto w-full max-w-6xl flex-1 px-4 py-6">{children}</main>
      <footer className="border-t px-4 py-3 text-center text-xs text-muted-foreground">
        ShelfSense · agentic retail &amp; cold-chain field ops
      </footer>
    </div>
  );
}
