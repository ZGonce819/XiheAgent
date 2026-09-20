import { useEffect, useState } from "react";
import { Navigate, createFileRoute } from "@tanstack/react-router";

import { AgentThreadView } from "@/components/agents/AgentThreadView";
import { Skeleton } from "@/components/ui/skeleton";
import { useAgentThread } from "@/lib/agents/queries";
import { useSession } from "@/lib/session";

export const Route = createFileRoute("/agents/$threadId")({
  component: AgentThreadPage,
});

function AgentThreadPage() {
  const { threadId } = Route.useParams();
  const session = useSession();
  const [pollMs, setPollMs] = useState<number | false>(false);
  const threadQuery = useAgentThread(threadId, { refetchInterval: pollMs });

  useEffect(() => {
    setPollMs(threadQuery.data?.status === "running" ? 3_000 : false);
  }, [threadQuery.data?.status]);

  if (session.isLoading) return null;
  if (!session.data) {
    return (
      <main className="agents-ui flex h-svh items-center justify-center p-6">
        <p className="text-sm text-muted-foreground">读取会话信息失败，请刷新页面重试。</p>
      </main>
    );
  }

  if (threadQuery.isLoading) {
    return (
      <main className="agents-ui flex h-svh items-center justify-center bg-[var(--ui-bg)] p-6">
        <Skeleton className="h-40 w-full max-w-3xl" />
      </main>
    );
  }

  if (threadQuery.isError || !threadQuery.data) {
    return <Navigate to="/agents" />;
  }

  return <AgentThreadView user={session.data} thread={threadQuery.data} />;
}
