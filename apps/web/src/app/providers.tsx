"use client";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useState, type ReactNode } from "react";

import { ApiProvider } from "@/lib/api.client";
import type { DevIdentity } from "@/lib/auth";

export function Providers({
  identity,
  children,
}: {
  identity: DevIdentity | null;
  children: ReactNode;
}) {
  const [client] = useState(
    () => new QueryClient({ defaultOptions: { queries: { staleTime: 5_000, retry: 1 } } }),
  );
  return (
    <QueryClientProvider client={client}>
      <ApiProvider identity={identity}>{children}</ApiProvider>
    </QueryClientProvider>
  );
}
