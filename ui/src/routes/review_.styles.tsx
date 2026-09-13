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
      title="Review Style Prompts"
      description="An agent browses recent merged PR review feedback, then writes a per-repo style guide for the reviewer."
      backTo={{ to: "/review", label: "Back to PR Review" }}
    >
      <div className="rounded-lg border border-border bg-card">
        <ReviewStylesPanel />
      </div>
    </AppShell>
  );
}
