import { Navigate, createFileRoute } from "@tanstack/react-router";

import { buttonVariants } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { loginUrl } from "@/lib/api";
import { useSession } from "@/lib/session";
import { cn } from "@/lib/utils";

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
    <main className="flex min-h-svh items-center justify-center p-6">
      <Card className="w-full max-w-md">
        <CardHeader>
          <CardTitle>登录 xihe-agent</CardTitle>
          <CardDescription>
            使用控制台账号配置模型、Gitee 仓库和手动编码任务。
          </CardDescription>
        </CardHeader>
        <CardContent>
          <a href={loginUrl()} className={cn(buttonVariants({ size: "lg" }), "w-full")}>
            继续
          </a>
        </CardContent>
      </Card>
    </main>
  );
}
