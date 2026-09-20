import { Outlet, createFileRoute } from "@tanstack/react-router";

import { Skeleton } from "@/components/ui/skeleton";
import agentsCss from "@/styles/agents.css?url";
import { useSession } from "@/lib/session";

export const Route = createFileRoute("/agents")({
  head: () => ({
    links: [{ rel: "stylesheet", href: agentsCss }],
  }),
  component: AgentsLayout,
});

function AgentsLayout() {
  const session = useSession();

  if (session.isLoading) {
    return (
      <main className="agents-ui flex h-svh items-center justify-center bg-[var(--ui-bg)] p-6">
        <Skeleton className="h-40 w-full max-w-md" />
      </main>
    );
  }

  if (!session.data) {
    return (
      <main className="agents-ui flex h-svh items-center justify-center p-6">
        <p className="text-sm text-muted-foreground">读取会话信息失败，请刷新页面重试。</p>
      </main>
    );
  }

  return (
    <div className="agents-ui h-svh overflow-hidden">
      <Outlet />
    </div>
  );
}
