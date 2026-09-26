import { ReviewQueue } from "@/components/review/review-queue";
import { apiGetOrNull } from "@/lib/api.server";
import type { ReviewQueueOut } from "@/lib/types";

export const dynamic = "force-dynamic";

export default async function ReviewPage() {
  const queue = await apiGetOrNull<ReviewQueueOut>("/v1/review/queue");
  if (!queue) {
    return (
      <div className="space-y-2">
        <h1 className="text-2xl font-semibold tracking-tight">Review queue</h1>
        <p className="text-sm text-muted-foreground">
          Only owners, managers and reviewers can see the queue. Switch role with the identity
          control at the top right.
        </p>
      </div>
    );
  }
  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Review queue</h1>
        <p className="text-sm text-muted-foreground">
          Actions the guardrails held back, and photos the model was not confident about. Your
          corrections become labelled examples you can promote into the eval golden set.
        </p>
      </div>
      <ReviewQueue initial={queue} />
    </div>
  );
}
