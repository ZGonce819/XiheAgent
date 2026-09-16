import { Navigate, createFileRoute } from "@tanstack/react-router";

import { AppShell } from "@/components/AppShell";
import { ReviewStylesPanel } from "@/components/ReviewStylesPanel";
import { Skeleton } from "@/components/ui/skeleton";
import { useSession } from "@/lib/session";

export const Route = createFileRoute("/review_/styles")({ component: ReviewStylesPage });

function ReviewStylesPage() {
  const session = useSession();

  if (session.isLoading) {
    return (
      <main className="p-6">
        <Skeleton className="h-64 w-full" />
      </main>
    );
  }
  if (!session.data) return <Navigate to="/login" />;

  return (
    <AppShell
      user={session.data}
      title="审查样式提示词"
      description="智能体根据近期已合并 PR 的审查反馈，为每个仓库生成审查规范。"
      backTo={{ to: "/review", label: "返回代码审查" }}
    >
      <div className="rounded-lg border border-border bg-card">
        <ReviewStylesPanel />
      </div>
    </AppShell>
  );
}
