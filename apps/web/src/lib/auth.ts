/**
 * Who the web app calls the API as.
 *
 * Two modes mirror the API (ADR-0002): Clerk when a publishable key is configured,
 * otherwise "dev identity" headers taken from cookies (with env defaults) so the whole
 * stack runs locally with no third party. The dev bar in the shell edits the cookies.
 */
import type { Role } from "./types";

/**
 * Explicit, like the API's SHELFSENSE_AUTH_MODE: "clerk" pairs with the API's "jwks",
 * "dev" (default) pairs with the API's "dev". Deriving it from the presence of a Clerk key
 * would let a key in .env silently flip the app into a mode the API is not in.
 */
export const clerkEnabled = process.env.NEXT_PUBLIC_AUTH_MODE === "clerk";

export const DEV_COOKIES = {
  tenant: "ss-dev-tenant",
  role: "ss-dev-role",
  user: "ss-dev-user",
} as const;

export interface DevIdentity {
  tenant: string;
  role: Role;
  user: string;
}

const ROLE_SET = new Set<string>(["owner", "manager", "field_agent", "reviewer"]);

export function devIdentityFrom(read: (name: string) => string | undefined): DevIdentity {
  const role = read(DEV_COOKIES.role) ?? process.env.NEXT_PUBLIC_DEV_ROLE ?? "manager";
  return {
    tenant: read(DEV_COOKIES.tenant) ?? process.env.NEXT_PUBLIC_DEV_TENANT ?? "",
    role: (ROLE_SET.has(role) ? role : "manager") as Role,
    user: read(DEV_COOKIES.user) ?? process.env.NEXT_PUBLIC_DEV_USER ?? "web-dev",
  };
}

export function devHeaders(identity: DevIdentity): Record<string, string> {
  return {
    "X-Dev-Tenant": identity.tenant,
    "X-Dev-Role": identity.role,
    "X-Dev-User": identity.user,
  };
}
