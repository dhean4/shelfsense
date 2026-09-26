"use client";

import { useQuery } from "@tanstack/react-query";

import { ActionCard } from "@/components/review/action-card";
import { ExtractionReview } from "@/components/review/extraction-review";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useApi } from "@/lib/api.client";
import type { ReviewQueueOut } from "@/lib/types";

export function ReviewQueue({ initial }: { initial: ReviewQueueOut }) {
  const api = useApi();
  const queue = useQuery({
    queryKey: ["review-queue"],
    queryFn: () => api.get<ReviewQueueOut>("/v1/review/queue"),
    initialData: initial,
  });
  const data = queue.data;

  return (
    <Tabs defaultValue="actions">
      <TabsList>
        <TabsTrigger value="actions">Actions ({data.actions.length})</TabsTrigger>
        <TabsTrigger value="extractions">Photos ({data.extractions.length})</TabsTrigger>
      </TabsList>
      <TabsContent value="actions" className="space-y-3 pt-3">
        {data.actions.length === 0 ? (
          <Empty text="No actions waiting. The planner's next risky decision will show up here." />
        ) : (
          data.actions.map((action) => <ActionCard key={action.id} action={action} />)
        )}
      </TabsContent>
      <TabsContent value="extractions" className="space-y-3 pt-3">
        {data.extractions.length === 0 ? (
          <Empty text="No low-confidence photos. Upload one on the Photos page to see the flow." />
        ) : (
          data.extractions.map((candidate) => (
            <ExtractionReview key={candidate.extraction_id} candidate={candidate} />
          ))
        )}
      </TabsContent>
    </Tabs>
  );
}

function Empty({ text }: { text: string }) {
  return (
    <div className="rounded-md border border-dashed p-8 text-center text-sm text-muted-foreground">
      {text}
    </div>
  );
}
