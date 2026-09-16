import { Navigate, createFileRoute } from "@tanstack/react-router"
import { useEffect, useRef, useState } from "react"

import type { ModelOption } from "@/lib/api"
import { AppShell, SettingsRow, SettingsSection } from "@/components/AppShell"
import { Button } from "@/components/ui/button"
import { RepoSelector } from "@/components/agents/RepoSelector"
import { Input } from "@/components/ui/input"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Skeleton } from "@/components/ui/skeleton"
import { Switch } from "@/components/ui/switch"
import {
  buildProfileUpdate,
  useOptions,
  useProfile,
  useRepos,
  useSaveProfile,
} from "@/lib/profile"
import { useSession } from "@/lib/session"

export const Route = createFileRoute("/cloud-agents")({
  component: CloudAgentsPage,
})

function CloudAgentsPage() {
  const session = useSession()
  const profile = useProfile()
  const options = useOptions()
  const repos = useRepos()
  const save = useSaveProfile()

  const [modelId, setModelId] = useState("")
  const [effort, setEffort] = useState("")
  const [subagentModelId, setSubagentModelId] = useState("")
  const [subagentEffort, setSubagentEffort] = useState("")
  const [defaultRepo, setDefaultRepo] = useState("")
  const [baseBranch, setBaseBranch] = useState("")
  const [branchPrefix, setBranchPrefix] = useState("")
  const [error, setError] = useState<string | null>(null)
  const initialized = useRef(false)

  const firstModel: ModelOption | undefined = options.data?.models[0]
  const defaultAgentModel =
    options.data?.default_agent_model ?? firstModel?.id ?? ""
  const defaultAgentEffort =
    options.data?.default_agent_reasoning_effort ??
    firstModel?.default_effort ??
    ""
  const defaultSubagentModel =
    options.data?.default_agent_subagent_model ?? defaultAgentModel
  const defaultSubagentEffort =
    options.data?.default_agent_subagent_reasoning_effort ?? defaultAgentEffort
  const currentModel: ModelOption | undefined =
    options.data?.models.find((m) => m.id === modelId) ?? firstModel
  const currentSubagentModel: ModelOption | undefined =
    options.data?.models.find((m) => m.id === subagentModelId) ?? firstModel

  useEffect(() => {
    if (!profile.data || initialized.current) return
    const hasModel = !!profile.data.default_model || !!defaultAgentModel
    if (!hasModel) return
    initialized.current = true
    setModelId(profile.data.default_model ?? defaultAgentModel)
    setEffort(profile.data.reasoning_effort ?? defaultAgentEffort)
    setSubagentModelId(
      profile.data.default_subagent_model ??
        profile.data.default_model ??
        defaultSubagentModel
    )
    setSubagentEffort(
      profile.data.subagent_reasoning_effort ??
        profile.data.reasoning_effort ??
        defaultSubagentEffort
    )
    setDefaultRepo(profile.data.default_repo ?? "")
    setBaseBranch(profile.data.base_branch ?? "")
    setBranchPrefix(profile.data.branch_prefix ?? "")
  }, [
    profile.data,
    defaultAgentModel,
    defaultAgentEffort,
    defaultSubagentModel,
    defaultSubagentEffort,
  ])

  useEffect(() => {
    if (currentModel && !currentModel.efforts.includes(effort)) {
      setEffort(currentModel.default_effort)
    }
  }, [currentModel, effort])

  useEffect(() => {
    if (
      currentSubagentModel &&
      !currentSubagentModel.efforts.includes(subagentEffort)
    ) {
      setSubagentEffort(currentSubagentModel.default_effort)
    }
  }, [currentSubagentModel, subagentEffort])

  if (session.isLoading) {
    return (
      <main className="p-6">
        <Skeleton className="h-64 w-full" />
      </main>
    )
  }
  if (!session.data) return <Navigate to="/login" />

  const fallbackModel = defaultAgentModel
  const fallbackEffort = defaultAgentEffort

  const persist = (patch: Parameters<typeof buildProfileUpdate>[1]) => {
    setError(null)
    save
      .mutateAsync(
        buildProfileUpdate(profile.data, patch, fallbackModel, fallbackEffort)
      )
      .catch((e: Error) => setError(e.message))
  }

  const persistDefaults = () => {
    persist({
      default_model: modelId,
      reasoning_effort: effort,
      default_subagent_model: subagentModelId,
      subagent_reasoning_effort: subagentEffort,
      default_repo: defaultRepo || null,
      base_branch: baseBranch || null,
      branch_prefix: branchPrefix || null,
    })
  }

  return (
    <AppShell
      user={session.data}
      title="XiheAgent 智能体"
      description="配置 XiheAgent 默认使用的模型、仓库和 PR 行为。"
    >
      <SettingsSection title="默认设置">
        <div className="divide-y divide-border">
          <SettingsRow
            label="默认模型"
            description="未明确指定模型时使用"
            control={
              <Select value={modelId} onValueChange={(v) => v && setModelId(v)}>
                <SelectTrigger className="w-40">
                  <SelectValue placeholder="选择模型" />
                </SelectTrigger>
                <SelectContent>
                  {options.data?.models.map((m) => (
                    <SelectItem key={m.id} value={m.id}>
                      {m.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            }
          />
          <SettingsRow
            label="推理强度"
            description="控制模型回答前的推理深度"
            control={
              <Select value={effort} onValueChange={(v) => v && setEffort(v)}>
                <SelectTrigger className="w-32">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {currentModel?.efforts.map((e) => (
                    <SelectItem key={e} value={e}>
                      {e}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            }
          />
          <SettingsRow
            label="默认子智能体模型"
            description="用于主智能体委派的任务"
            control={
              <Select
                value={subagentModelId}
                onValueChange={(v) => v && setSubagentModelId(v)}
              >
                <SelectTrigger className="w-40">
                  <SelectValue placeholder="选择模型" />
                </SelectTrigger>
                <SelectContent>
                  {options.data?.models.map((m) => (
                    <SelectItem key={m.id} value={m.id}>
                      {m.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            }
          />
          <SettingsRow
            label="子智能体推理强度"
            description="控制子智能体的推理深度"
            control={
              <Select
                value={subagentEffort}
                onValueChange={(v) => v && setSubagentEffort(v)}
              >
                <SelectTrigger className="w-32">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {currentSubagentModel?.efforts.map((e) => (
                    <SelectItem key={e} value={e}>
                      {e}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            }
          />
          <SettingsRow
            label="默认仓库"
            description="未指定仓库时使用，支持 Gitee URL 和 owner/repo 格式。"
            control={
              repos.data?.repositories?.length ? (
                <div className="w-56">
                  <RepoSelector
                    repos={repos.data.repositories}
                    selectedRepo={defaultRepo || null}
                    onRepoChange={(repo) => setDefaultRepo(repo ?? "")}
                    placeholder="选择或输入仓库"
                    emptySelectionLabel="不设置默认仓库"
                    triggerClassName="h-7 w-full max-w-none rounded-md border border-input bg-input/20 px-2 py-1.5 text-xs/relaxed text-foreground transition-colors hover:opacity-100 dark:bg-input/30"
                    dropdownClassName="w-56"
                  />
                </div>
              ) : (
                <Input
                  className="w-56"
                  placeholder="owner/repo"
                  value={defaultRepo}
                  onChange={(e) => setDefaultRepo(e.target.value)}
                />
              )
            }
          />
          <SettingsRow
            label="基础分支"
            description="留空时使用仓库的默认分支。"
            htmlFor="base-branch"
            control={
              <Input
                id="base-branch"
                className="w-56"
                placeholder="分支名称"
                value={baseBranch}
                onChange={(e) => setBaseBranch(e.target.value)}
              />
            }
          />
          <SettingsRow
            label="分支前缀"
            description="智能体创建分支时使用的名称前缀"
            htmlFor="branch-prefix"
            control={
              <Input
                id="branch-prefix"
                className="w-56"
                placeholder="ai-coding/"
                value={branchPrefix}
                onChange={(e) => setBranchPrefix(e.target.value)}
              />
            }
          />
          <div className="flex justify-end px-4 py-3">
            <Button
              size="sm"
              onClick={persistDefaults}
              disabled={save.isPending}
            >
              {save.isPending ? "正在保存..." : "保存默认设置"}
            </Button>
          </div>
        </div>
      </SettingsSection>

      <SettingsSection title="拉取请求">
        <div className="divide-y divide-border">
          <SettingsRow
            label="自动修复 CI 失败"
            description="智能体会尝试修复其创建的 PR 中失败的 CI 检查。"
            comingSoon
            control={
              <Switch
                checked={profile.data?.auto_fix_ci ?? true}
                onCheckedChange={(v) => persist({ auto_fix_ci: v })}
                disabled
              />
            }
          />
          <SettingsRow
            label="始终创建 PR"
            description="代码变更后始终创建 PR；关闭时仅在必要或明确要求时创建。"
            control={
              <Switch
                checked={profile.data?.create_prs ?? false}
                onCheckedChange={(v) => persist({ create_prs: v })}
              />
            }
          />
        </div>
      </SettingsSection>

      {error && <p className="text-xs text-destructive">{error}</p>}
    </AppShell>
  )
}

