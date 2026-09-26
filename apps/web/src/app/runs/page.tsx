import Link from "next/link";

import { Badge } from "@/components/ui/badge";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { apiGetOrNull } from "@/lib/api.server";
import { usd, type RunOut } from "@/lib/types";

export const dynamic = "force-dynamic";

const STATUS_VARIANT: Record<
  RunOut["status"],
  "default" | "secondary" | "destructive" | "outline"
> = {
  queued: "outline",
  running: "secondary",
  succeeded: "default",
  failed: "destructive",
};

export default async function RunsPage() {
  const runs = await apiGetOrNull<RunOut[]>("/v1/runs?limit=100");
  if (!runs) {
    return <p className="text-sm text-muted-foreground">Sign in to see agent runs.</p>;
  }
  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Agent runs</h1>
        <p className="text-sm text-muted-foreground">
          Every model invocation with its tokens, cost, latency and tool calls.
        </p>
      </div>
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Started</TableHead>
            <TableHead>Kind</TableHead>
            <TableHead>Status</TableHead>
            <TableHead>Model</TableHead>
            <TableHead className="text-right">Tokens</TableHead>
            <TableHead className="text-right">Cost</TableHead>
            <TableHead className="text-right">Latency</TableHead>
            <TableHead className="text-right">Tools</TableHead>
            <TableHead className="text-right">Actions</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {runs.map((run) => (
            <TableRow key={run.id}>
              <TableCell>
                <Link href={`/runs/${run.id}`} className="underline-offset-2 hover:underline">
                  {new Date(run.started_at).toLocaleString()}
                </Link>
              </TableCell>
              <TableCell>{run.kind}</TableCell>
              <TableCell>
                <Badge variant={STATUS_VARIANT[run.status]}>{run.status}</Badge>
              </TableCell>
              <TableCell className="font-mono text-xs">{run.model}</TableCell>
              <TableCell className="text-right">
                {run.input_tokens.toLocaleString()} / {run.output_tokens.toLocaleString()}
              </TableCell>
              <TableCell className="text-right">{usd(run.cost_usd)}</TableCell>
              <TableCell className="text-right">{(run.latency_ms / 1000).toFixed(1)}s</TableCell>
              <TableCell className="text-right">{run.tool_calls.length}</TableCell>
              <TableCell className="text-right">{run.actions.length}</TableCell>
            </TableRow>
          ))}
          {runs.length === 0 ? (
            <TableRow>
              <TableCell colSpan={9} className="text-center text-muted-foreground">
                No runs yet.
              </TableCell>
            </TableRow>
          ) : null}
        </TableBody>
      </Table>
    </div>
  );
}
