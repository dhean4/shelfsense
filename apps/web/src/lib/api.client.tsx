"use client";

/** Browser-side API access: mutations and refetches from client components. */
import { useAuth } from "@clerk/nextjs";
import { createContext, useCallback, useContext, useMemo, type ReactNode } from "react";

import { clerkEnabled, devHeaders, type DevIdentity } from "./auth";

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

type HeaderGetter = () => Promise<Record<string, string>>;

interface ApiContextValue {
  identity: DevIdentity | null;
  getHeaders: HeaderGetter;
}

const ApiContext = createContext<ApiContextValue | null>(null);

function ContextBridge({
  identity,
  getHeaders,
  children,
}: {
  identity: DevIdentity | null;
  getHeaders: HeaderGetter;
  children: ReactNode;
}) {
  const value = useMemo(() => ({ identity, getHeaders }), [identity, getHeaders]);
  return <ApiContext.Provider value={value}>{children}</ApiContext.Provider>;
}

/** Mounted only when Clerk is configured: the session token becomes the bearer header. */
function ClerkBridge({ children }: { children: ReactNode }) {
  const { getToken } = useAuth();
  const getHeaders = useCallback(async (): Promise<Record<string, string>> => {
    const token = await getToken();
    return token ? { Authorization: `Bearer ${token}` } : {};
  }, [getToken]);
  return (
    <ContextBridge identity={null} getHeaders={getHeaders}>
      {children}
    </ContextBridge>
  );
}

function DevBridge({ identity, children }: { identity: DevIdentity | null; children: ReactNode }) {
  const getHeaders = useCallback(
    () => Promise.resolve(identity ? devHeaders(identity) : {}),
    [identity],
  );
  return (
    <ContextBridge identity={identity} getHeaders={getHeaders}>
      {children}
    </ContextBridge>
  );
}

export function ApiProvider({
  identity,
  children,
}: {
  identity: DevIdentity | null;
  children: ReactNode;
}) {
  return clerkEnabled ? (
    <ClerkBridge>{children}</ClerkBridge>
  ) : (
    <DevBridge identity={identity}>{children}</DevBridge>
  );
}

export class ApiClientError extends Error {
  constructor(
    public readonly status: number,
    public readonly detail: string,
  ) {
    super(detail);
  }
}

function detailOf(text: string): string {
  try {
    return String((JSON.parse(text) as { detail?: unknown }).detail ?? text);
  } catch {
    return text;
  }
}

export function useApi() {
  const ctx = useContext(ApiContext);
  if (!ctx) throw new Error("useApi must be used inside <ApiProvider>");
  const { getHeaders, identity } = ctx;

  const request = useCallback(
    async <T,>(path: string, init: RequestInit = {}): Promise<T> => {
      const isForm = init.body instanceof FormData;
      const headers: Record<string, string> = {
        ...(await getHeaders()),
        ...(init.body && !isForm ? { "content-type": "application/json" } : {}),
      };
      const response = await fetch(`${API_URL}${path}`, { ...init, headers });
      const text = await response.text();
      if (!response.ok) throw new ApiClientError(response.status, detailOf(text));
      return (text ? JSON.parse(text) : null) as T;
    },
    [getHeaders],
  );

  return useMemo(
    () => ({
      identity,
      get: <T,>(path: string) => request<T>(path),
      post: <T,>(path: string, body?: unknown) =>
        request<T>(path, {
          method: "POST",
          body: body === undefined ? undefined : JSON.stringify(body),
        }),
      upload: <T,>(path: string, form: FormData) =>
        request<T>(path, { method: "POST", body: form }),
    }),
    [identity, request],
  );
}
