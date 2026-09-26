// P0 placeholder. The dashboard, review queue and upload PWA arrive in P4, P6 and P8.
// Until then this page only proves the web shell builds and knows where the API lives.
const apiUrl = process.env.NEXT_PUBLIC_API_URL ?? "(NEXT_PUBLIC_API_URL not set)";

export default function Home() {
  return (
    <main className="mx-auto flex min-h-screen max-w-2xl flex-col justify-center gap-4 p-8">
      <h1 className="text-3xl font-semibold tracking-tight">ShelfSense</h1>
      <p className="text-muted-foreground">
        Shelf photos and cold-chain telemetry in, reviewed decisions out.
      </p>
      <dl className="rounded-md border p-4 text-sm">
        <dt className="font-medium">API base URL</dt>
        <dd className="font-mono">{apiUrl}</dd>
      </dl>
    </main>
  );
}
