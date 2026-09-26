/** Server-side API access for React Server Components and route handlers. */
import "server-only";

import { cookies } from "next/headers";

import { clerkEnabled, devHeaders, devIdentityFrom, type DevIdentity } from "./auth";

export const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export class ApiError extends Error {
  constructor(
    public readonly status: number,
    public readonly detail: string,
  ) {
    super(`API ${String(status)}: ${detail}`);
  }
}

export async function currentDevIdentity(): Promise<DevIdentity> {
  const jar = await cookies();
  return devIdentityFrom((name) => jar.get(name)?.value);
}

export async function authHeaders(): Promise<Record<string, string>> {
  if (clerkEnabled) {
    const { auth } = await import("@clerk/nextjs/server");
    const { getToken } = await auth();
    const token = await getToken();
    return token ? { Authorization: `Bearer ${token}` } : {};
  }
  return devHeaders(await currentDevIdentity());
}

export async function apiGet<T>(path: string): Promise<T> {
  const response = await fetch(`${API_URL}${path}`, {
    headers: await authHeaders(),
    cache: "no-store",
  });
  const text = await response.text();
  if (!response.ok) {
    let detail = text;
    try {
      detail = String((JSON.parse(text) as { detail?: unknown }).detail ?? text);
    } catch {
      // not JSON
    }
    throw new ApiError(response.status, detail);
  }
  return JSON.parse(text) as T;
}

/**
 * Like apiGet but returns null when the caller is not allowed (401/403) or the API cannot
 * be reached at all, so pages render a notice instead of a 500. Other API errors still throw.
 */
export async function apiGetOrNull<T>(path: string): Promise<T | null> {
  try {
    return await apiGet<T>(path);
  } catch (error) {
    if (error instanceof ApiError && (error.status === 401 || error.status === 403)) {
      return null;
    }
    if (!(error instanceof ApiError)) {
      console.error(`API unreachable at ${API_URL}: ${String(error)}`);
      return null;
    }
    throw error;
  }
}
