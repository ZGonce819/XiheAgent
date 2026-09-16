import { Link, Navigate, createFileRoute } from "@tanstack/react-router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CaretRightIcon } from "@phosphor-icons/react";
import { useEffect, useMemo, useState } from "react";
import { IoLogoGithub } from "react-icons/io5";

import type { AutofixMode, ReposPayload, TeamSettings, TriggerMode } from "@/lib/api";
import { AppShell, SettingsRow, SettingsSection } from "@/components/AppShell";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { Switch } from "@/components/ui/switch";
import { Textarea } from "@/components/ui/textarea";
import { ApiError, api } from "@/lib/api";
import { useSession } from "@/lib/session";

export const Route = createFileRoute("/review")({ component: ReviewPage });

const TRIGGER_MODES: Array<{ value: TriggerMode; label: string; description: string }> = [
  {
    value: "every_push",
    label: "Every Push",
    description: "Review on every push to the PR",
  },
  {
    value: "once_per_pr",
    label: "Once Per PR",
    description: "Review once when the PR is opened, skip subsequent pushes",
  },
  {
    value: "manual",
    label: "Manual Only",
    description: "Only review when the configured review command is commented",
  },
];

const AUTOFIX_MODES: Array<{ value: AutofixMode; label: string }> = [
  { value: "off", label: "Off" },
  { value: "low", label: "Low" },
  { value: "medium", label: "Medium" },
  { value: "high", label: "High" },
];

const DEFAULT_SETTINGS: TeamSettings = {
  trigger_mode: "every_push",
  review_draft_prs: false,
  pr_summaries: true,
  review_trace_links: true,
  autofix_mode: "off",
  autofix_severity_threshold: "medium",
  org_guidelines: null,
  default_agent_model: null,
  default_agent_reasoning_effort: null,
  default_agent_subagent_model: null,
  default_agent_subagent_reasoning_effort: null,
  default_reviewer_model: null,
  default_reviewer_reasoning_effort: null,
  default_reviewer_subagent_model: null,
  default_reviewer_subagent_reasoning_effort: null,
};

function ReviewPage() {
  const session = useSession();
  const qc = useQueryClient();
  const settings = useQuery({
    queryKey: ["teamSettings"],
    queryFn: api.getTeamSettings,
    enabled: !!session.data,
  });
  const [local, setLocal] = useState<TeamSettings>(DEFAULT_SETTINGS);
  const [guidelinesDraft, setGuidelinesDraft] = useState("");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (settings.data) {
      setLocal(settings.data);
      setGuidelinesDraft(settings.data.org_guidelines ?? "");
    }
  }, [settings.data]);

  const save = useMutation({
    mutationFn: (body: TeamSettings) => api.saveTeamSettings(body),
    onSuccess: (saved) => {
      qc.setQueryData(["teamSettings"], saved);
      setError(null);
    },
    onError: (e: Error) => setError(e.message),
  });

  if (session.isLoading) {
    return (
      <main className="p-6">
        <Skeleton className="h-64 w-full" />
      </main>
    );
  }
  if (!session.data) return <Navigate to="/login" />;

  const current: TeamSettings = local;
  const canEdit = session.data.is_admin;

  const persist = (patch: Partial<TeamSettings>) => {
    const next: TeamSettings = { ...current, ...patch };
    setLocal(next);
    if (canEdit) save.mutate(next);
  };

  const triggerDescription =
    TRIGGER_MODES.find((m) => m.value === current.trigger_mode)?.description ??
    "PR Review will automatically review every push to a PR";

  const trimmedGuidelines = guidelinesDraft.trim();
  const savedGuidelines = current.org_guidelines ?? "";
  const guidelinesDirty = trimmedGuidelines !== savedGuidelines.trim();

  const saveGuidelines = () => {
    if (!canEdit) return;
    persist({ org_guidelines: trimmedGuidelines || null });
  };

  return (
    <AppShell
      user={session.data}
      title="代码审查"
      description="自动检查 Pull Request 中的缺陷和风险，运行用量按实际智能体调用计算。"
    >
      <RepositoriesSection canEdit={canEdit} />

      <SettingsSection title="规则">
        <Link
          to="/review/styles"
          className="flex items-center justify-between gap-6 px-4 py-3 hover:bg-muted/40"
        >
          <div className="flex flex-col gap-0.5">
            <span className="text-xs font-medium text-foreground">审查样式提示词</span>
            <span className="text-xs text-muted-foreground">
              Per-repo style guides learned from past PR review feedback.
            </span>
          </div>
          <CaretRightIcon className="size-3.5 shrink-0 text-muted-foreground" />
        </Link>
      </SettingsSection>

      <SettingsSection
        title="组织规范"
        description="应用于所有仓库审查的全局指令；发生冲突时，以仓库专属提示词为准。"
      >
        <div className="flex flex-col gap-2 p-4">
          <Textarea
            className="min-h-[200px] w-full font-mono text-xs"
            value={guidelinesDraft}
            onChange={(e) => setGuidelinesDraft(e.target.value)}
            placeholder="例如：新 API 端点缺少输入校验时必须提示；优先使用结构化日志。"
            disabled={!canEdit}
          />
          {canEdit && (
            <div className="flex items-center gap-2">
              <Button
                size="sm"
                disabled={!guidelinesDirty || save.isPending}
                onClick={saveGuidelines}
              >
                Save guidelines
              </Button>
              {guidelinesDirty && (
                <span className="text-xs text-muted-foreground">有未保存的修改</span>
              )}
            </div>
          )}
        </div>
      </SettingsSection>

      <SettingsSection title="配置">
        <div className="divide-y divide-border">
          <SettingsRow
            label="触发方式"
            description={triggerDescription}
            comingSoon
            control={
              <Select
                value={current.trigger_mode}
                onValueChange={(v) => persist({ trigger_mode: v as TriggerMode })}
                disabled
              >
                <SelectTrigger className="w-40">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {TRIGGER_MODES.map((m) => (
                    <SelectItem key={m.value} value={m.value}>
                      {m.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            }
          />
          <SettingsRow
            label="审查草稿 PR"
            description="是否默认审查草稿 PR；每位用户可以在个人设置中覆盖此项。"
            control={
              <Switch
                checked={current.review_draft_prs}
                onCheckedChange={(v) => persist({ review_draft_prs: v })}
                disabled={!canEdit}
              />
            }
          />
          <SettingsRow
            label="PR 摘要"
            description="自动为 Pull Request 生成说明"
            control={
              <Switch
                checked={current.pr_summaries}
                onCheckedChange={(v) => persist({ pr_summaries: v })}
                disabled={!canEdit}
              />
            }
          />
          <SettingsRow
            label="跟踪链接"
            description="在审查评论中附加 LangSmith 跟踪链接，仅工作区成员可以打开。"
            control={
              <Switch
                checked={current.review_trace_links}
                onCheckedChange={(v) => persist({ review_trace_links: v })}
                disabled={!canEdit}
              />
            }
          />
          <SettingsRow
            label="自动修复模式"
            description="开启后，审查器会提出修复方案，用量按计划执行计算。"
            comingSoon
            control={
              <Select
                value={current.autofix_mode}
                onValueChange={(v) => persist({ autofix_mode: v as AutofixMode })}
                disabled
              >
                <SelectTrigger className="w-32">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {AUTOFIX_MODES.map((m) => (
                    <SelectItem key={m.value} value={m.value}>
                      {m.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            }
          />
          <SettingsRow
            label="自动修复严重程度阈值"
            description="达到此严重程度及以上的问题将自动修复"
            comingSoon
            control={
              <Select
                value={current.autofix_severity_threshold}
                onValueChange={(v) =>
                  persist({ autofix_severity_threshold: v as AutofixMode })
                }
                disabled
              >
                <SelectTrigger className="w-32">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {AUTOFIX_MODES.map((m) => (
                    <SelectItem key={m.value} value={m.value}>
                      {m.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            }
          />
        </div>
      </SettingsSection>

      {!canEdit && (
        <p className="text-xs text-muted-foreground">
          These settings are read-only. Ask a workspace admin to change them.
        </p>
      )}

      {error && <p className="text-xs text-destructive">{error}</p>}
    </AppShell>
  );
}

function RepositoriesSection({ canEdit: _canEdit }: { canEdit: boolean }) {
  const repos = useQuery<ReposPayload>({
    queryKey: ["repos"],
    queryFn: async () => {
      try {
        return await api.repos();
      } catch (e) {
        if (e instanceof ApiError && e.status === 401)
          return { installations: [], repositories: [] };
        throw e;
      }
    },
  });

  const enabled = useQuery({
    queryKey: ["enabledReviewRepos"],
    queryFn: api.listEnabledReviewRepos,
  });

  const enabledSet = useMemo(
    () => new Set(enabled.data?.repos ?? []),
    [enabled.data?.repos],
  );

  const grouped = useMemo(() => {
    const byOwner = new Map<string, Array<{ full_name: string; private: boolean }>>();
    for (const r of repos.data?.repositories ?? []) {
      const [owner] = r.full_name.split("/");
      if (!owner) continue;
      const arr = byOwner.get(owner) ?? [];
      arr.push(r);
      byOwner.set(owner, arr);
    }
    return Array.from(byOwner.entries()).sort(([a], [b]) => a.localeCompare(b));
  }, [repos.data?.repositories]);

  const loading = repos.isLoading || enabled.isLoading;

  return (
    <SettingsSection
      title="仓库"
      description="选择代码托管安装，配置需要自动审查的仓库。"
    >
      <div className="divide-y divide-border">
        {loading && (
          <div className="p-4">
            <Skeleton className="h-16 w-full" />
          </div>
        )}
        {!loading && grouped.length === 0 && (
          <p className="px-4 py-3 text-xs text-muted-foreground">
            暂未找到仓库集成。请先为账号或组织配置 Gitee/GitHub 访问权限。
          </p>
        )}
        {grouped.map(([owner, list]) => {
          const enabledCount = list.filter((r) => enabledSet.has(r.full_name)).length;
          return (
            <Link
              key={owner}
              to="/review/repositories/$owner"
              params={{ owner }}
              className="flex items-center justify-between gap-4 px-4 py-3 hover:bg-muted/40"
            >
              <div className="flex items-center gap-3">
                <IoLogoGithub className="size-5 shrink-0 text-muted-foreground" />
                <div className="flex flex-col gap-0.5">
                  <div className="flex items-center gap-2 text-xs">
                    <span className="font-medium text-foreground">{owner}</span>
                  </div>
                  <span className="text-xs text-muted-foreground">GitHub</span>
                </div>
              </div>
              <div className="flex items-center gap-2 text-xs text-muted-foreground">
                <span>
                  已启用 {enabledCount}/{list.length} 个仓库
                </span>
                <CaretRightIcon className="size-3.5" />
              </div>
            </Link>
          );
        })}
      </div>
    </SettingsSection>
  );
}

