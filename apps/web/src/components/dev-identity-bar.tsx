"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { DEV_COOKIES, type DevIdentity } from "@/lib/auth";
import { ROLES } from "@/lib/types";

function setCookie(name: string, value: string) {
  document.cookie = `${name}=${encodeURIComponent(value)}; path=/; max-age=31536000; samesite=lax`;
}

/** Dev-mode only: pick the tenant and role the browser calls the API as. */
export function DevIdentityBar({ identity }: { identity: DevIdentity }) {
  const router = useRouter();
  const [tenant, setTenant] = useState(identity.tenant);
  const [role, setRole] = useState<string>(identity.role);
  const [editing, setEditing] = useState(false);

  function apply() {
    setCookie(DEV_COOKIES.tenant, tenant.trim());
    setCookie(DEV_COOKIES.role, role);
    setCookie(DEV_COOKIES.user, `dev_${role}`);
    setEditing(false);
    router.refresh();
  }

  if (!editing) {
    return (
      <button
        type="button"
        onClick={() => setEditing(true)}
        className="rounded-md border px-2 py-1 text-xs text-muted-foreground hover:text-foreground"
        title="Dev auth mode: click to change tenant or role"
      >
        dev · {identity.role} · {identity.tenant ? `${identity.tenant.slice(0, 8)}…` : "no tenant"}
      </button>
    );
  }

  return (
    <form
      className="flex flex-wrap items-center gap-2 text-xs"
      onSubmit={(event) => {
        event.preventDefault();
        apply();
      }}
    >
      <Input
        value={tenant}
        onChange={(event) => setTenant(event.target.value)}
        placeholder="tenant id (from make seed)"
        className="h-8 w-72 font-mono text-xs"
        aria-label="Tenant id"
      />
      <select
        value={role}
        onChange={(event) => setRole(event.target.value)}
        className="h-8 rounded-md border bg-background px-2"
        aria-label="Role"
      >
        {ROLES.map((r) => (
          <option key={r} value={r}>
            {r}
          </option>
        ))}
      </select>
      <Button type="submit" size="sm">
        Apply
      </Button>
      <Button type="button" size="sm" variant="ghost" onClick={() => setEditing(false)}>
        Cancel
      </Button>
    </form>
  );
}
