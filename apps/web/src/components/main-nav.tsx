"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

const NAV = [
  { href: "/", label: "Dashboard" },
  { href: "/review", label: "Review queue" },
  { href: "/runs", label: "Agent runs" },
  { href: "/photos", label: "Photos" },
  { href: "/upload", label: "Upload" },
  { href: "/telemetry", label: "Cold chain" },
  { href: "/evals", label: "Evals" },
  { href: "/costs", label: "Costs" },
] as const;

export function MainNav() {
  const pathname = usePathname();
  return (
    <nav aria-label="Main" className="flex flex-wrap gap-4 text-sm">
      {NAV.map((item) => {
        const active = item.href === "/" ? pathname === "/" : pathname.startsWith(item.href);
        return (
          <Link
            key={item.href}
            href={item.href}
            aria-current={active ? "page" : undefined}
            className={
              active
                ? "font-medium text-foreground underline underline-offset-4"
                : "text-muted-foreground hover:text-foreground"
            }
          >
            {item.label}
          </Link>
        );
      })}
    </nav>
  );
}
