import { createFileRoute } from "@tanstack/react-router";

import { AgentsHome } from "@/components/agents/AgentsHome";
import { AgentsShell } from "@/components/agents/AgentsSidebar";
import { useSession } from "@/lib/session";

interface AgentsIndexSearch {
  project?: string;
}

export const Route = createFileRoute("/agents/")({
  validateSearch: (search: Record<string, unknown>): AgentsIndexSearch => ({
    project: typeof search.project === "string" ? search.project : undefined,
  }),
  component: AgentsIndexPage,
});

function AgentsIndexPage() {
  const session = useSession();
  const { project } = Route.useSearch();
  if (!session.data) return null;

  return (
    <AgentsShell user={session.data}>
      {/* key 绑定 project：从侧边栏「+」切换项目时强制重挂载，
          否则 AgentsHome 的 useState 初始值不会随 search 参数更新（需刷新才生效） */}
      <AgentsHome key={project ?? "__new__"} initialProject={project ?? null} />
    </AgentsShell>
  );
}
