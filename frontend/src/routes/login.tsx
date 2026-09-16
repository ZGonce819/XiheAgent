import { Navigate, createFileRoute } from "@tanstack/react-router";

import { buttonVariants } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { loginUrl } from "@/lib/api";
import { useSession } from "@/lib/session";
import { cn } from "@/lib/utils";
import { Logo } from "@/components/agents/ported/Logo";

export const Route = createFileRoute("/login")({ component: Login });

function Login() {
  const session = useSession();

  if (session.isLoading) {
    return (
      <main className="flex min-h-svh items-center justify-center p-6">
        <Skeleton className="h-40 w-80" />
      </main>
    );
  }

  if (session.data) {
    return <Navigate to="/my-settings" />;
  }

  return (
    <main className="relative flex min-h-svh items-center justify-center bg-background p-6">
      <div className="absolute top-6 left-6 text-foreground">
        <Logo className="h-auto" />
      </div>
      <Card className="w-full max-w-md rounded-lg border-border shadow-sm">
        <CardHeader className="border-b border-border pb-5">
          <CardTitle className="text-lg">XiheAgent</CardTitle>
          <CardDescription>
            登录后继续管理代码任务、仓库和自动化。
          </CardDescription>
        </CardHeader>
        <CardContent>
          <a href={loginUrl()} className={cn(buttonVariants({ size: "lg" }), "w-full")}>
            使用控制台账号继续
          </a>
        </CardContent>
      </Card>
    </main>
  );
}
