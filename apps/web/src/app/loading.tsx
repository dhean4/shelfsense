export default function Loading() {
  return (
    <div className="space-y-4" role="status" aria-label="Loading">
      <div className="h-7 w-56 animate-pulse rounded bg-muted" />
      <div className="h-4 w-96 max-w-full animate-pulse rounded bg-muted" />
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        {[0, 1, 2, 3].map((i) => (
          <div key={i} className="h-24 animate-pulse rounded-md border bg-muted/40" />
        ))}
      </div>
      <div className="h-64 animate-pulse rounded-md border bg-muted/40" />
      <span className="sr-only">Loading…</span>
    </div>
  );
}
