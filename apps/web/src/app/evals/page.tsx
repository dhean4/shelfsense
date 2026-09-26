import latest from "@/data/eval-latest.json";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";

interface Report {
  dataset: string;
  provider: string;
  vision_model: string;
  planner_model: string;
  started_at: string;
  git_sha: string | null;
  duration_s: number;
  summary: Record<string, Record<string, number>>;
  vision: Record<string, number | string | null>[];
  planner: Record<string, number | string | null>[];
}

const report = latest as Report;

const LOWER_IS_BETTER = new Set([
  "facings_mae",
  "hallucination_rate",
  "unneeded_action_rate",
  "cost_usd",
  "latency_ms",
  "total_cost_usd",
  "failures",
]);

function fmt(metric: string, value: number): string {
  if (metric.includes("cost")) return `$${value.toFixed(4)}`;
  if (metric === "latency_ms") return `${(value / 1000).toFixed(1)}s`;
  if (metric === "cases" || metric === "failures") return value.toFixed(0);
  return value.toFixed(3);
}

function Scoreboard({ agent, metrics }: { agent: string; metrics: Record<string, number> }) {
  const rows = Object.entries(metrics).filter(([k]) => k !== "cases" && k !== "failures");
  return (
    <Card>
      <CardHeader className="flex flex-row items-center gap-2">
        <CardTitle className="text-base capitalize">{agent}</CardTitle>
        <Badge variant="outline">{metrics.cases?.toFixed(0) ?? "0"} cases</Badge>
        {metrics.failures ? (
          <Badge variant="destructive">{metrics.failures.toFixed(0)} failed</Badge>
        ) : null}
      </CardHeader>
      <CardContent>
        <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
          {rows.map(([metric, value]) => (
            <div key={metric} className="rounded-md border p-3">
              <div className="text-xs text-muted-foreground">
                {metric.replace(/_/g, " ")}
                {LOWER_IS_BETTER.has(metric) ? " ↓" : ""}
              </div>
              <div className="text-xl font-semibold">{fmt(metric, value)}</div>
            </div>
          ))}
        </div>
      </CardContent>
    </Card>
  );
}

export default function EvalsPage() {
  const worstVision = [...report.vision]
    .filter((r) => typeof r.exact_match === "number")
    .sort((a, b) => Number(a.sku_recall) - Number(b.sku_recall))
    .slice(0, 8);
  const worstPlanner = [...report.planner]
    .sort((a, b) => Number(a.decision_accuracy) - Number(b.decision_accuracy))
    .slice(0, 8);
  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Eval scoreboard</h1>
        <p className="text-sm text-muted-foreground">
          Latest run of <code className="font-mono">evals/data/golden.jsonl</code> · provider{" "}
          {report.provider} · vision {report.vision_model} · planner {report.planner_model} ·{" "}
          {new Date(report.started_at).toLocaleString()} · commit {report.git_sha ?? "?"} ·{" "}
          {report.duration_s.toFixed(0)}s. Refresh with <code className="font-mono">make eval</code>
          .
        </p>
      </div>
      {Object.entries(report.summary).map(([agent, metrics]) =>
        Object.keys(metrics).length ? (
          <Scoreboard key={agent} agent={agent} metrics={metrics} />
        ) : null,
      )}
      <div className="grid gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Hardest vision cases</CardTitle>
          </CardHeader>
          <CardContent>
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>case</TableHead>
                  <TableHead className="text-right">recall</TableHead>
                  <TableHead className="text-right">stock-out F1</TableHead>
                  <TableHead className="text-right">halluc.</TableHead>
                  <TableHead className="text-right">cost</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {worstVision.map((r) => (
                  <TableRow key={String(r.case_id)}>
                    <TableCell className="font-mono text-xs">{String(r.case_id)}</TableCell>
                    <TableCell className="text-right">{Number(r.sku_recall).toFixed(2)}</TableCell>
                    <TableCell className="text-right">
                      {Number(r.stock_out_f1).toFixed(2)}
                    </TableCell>
                    <TableCell className="text-right">
                      {Number(r.hallucination_rate).toFixed(2)}
                    </TableCell>
                    <TableCell className="text-right">${Number(r.cost_usd).toFixed(3)}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </CardContent>
        </Card>
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Hardest planner cases</CardTitle>
          </CardHeader>
          <CardContent>
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>case</TableHead>
                  <TableHead className="text-right">exact</TableHead>
                  <TableHead className="text-right">reorder F1</TableHead>
                  <TableHead className="text-right">unneeded</TableHead>
                  <TableHead className="text-right">steps</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {worstPlanner.map((r) => (
                  <TableRow key={String(r.case_id)}>
                    <TableCell className="font-mono text-xs">{String(r.case_id)}</TableCell>
                    <TableCell className="text-right">
                      {Number(r.decision_accuracy).toFixed(0)}
                    </TableCell>
                    <TableCell className="text-right">{Number(r.reorder_f1).toFixed(2)}</TableCell>
                    <TableCell className="text-right">
                      {Number(r.unneeded_action_rate).toFixed(2)}
                    </TableCell>
                    <TableCell className="text-right">{String(r.steps)}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
