import { Navigate, createFileRoute, useNavigate } from "@tanstack/react-router"
import { useQuery, useQueryClient } from "@tanstack/react-query"
import { useState } from "react"
import { IoLogoSlack } from "react-icons/io5"

import type { SessionUser } from "@/lib/api"
import { AppShell, SettingsRow, SettingsSection } from "@/components/AppShell"
import { Button } from "@/components/ui/button"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Skeleton } from "@/components/ui/skeleton"
import { api, slackConnectUrl } from "@/lib/api"
import {
  buildProfileUpdate,
  useOptions,
  useProfile,
  useSaveProfile,
} from "@/lib/profile"
import { useSession } from "@/lib/session"
import { cn } from "@/lib/utils"

export const Route = createFileRoute("/my-settings")({
  component: MySettingsPage,
})

type DraftReviewChoice = "team_default" | "always_on" | "always_off"

function toChoice(value: boolean | null | undefined): DraftReviewChoice {
  if (value === true) return "always_on"
  if (value === false) return "always_off"
  return "team_default"
}

function fromChoice(choice: DraftReviewChoice): boolean | null {
  if (choice === "always_on") return true
  if (choice === "always_off") return false
  return null
}

function UserMappingSection({ session }: { session: SessionUser }) {
  const qc = useQueryClient()
  const mapping = useQuery({ queryKey: ["myMapping"], queryFn: api.myMapping })
  const [connecting, setConnecting] = useState(false)

  const slackUserId = mapping.data?.slack_user_id ?? null
  const workEmail = mapping.data?.work_email ?? null
  const connected = !!slackUserId

  const connect = () => {
    setConnecting(true)
    // Refresh the cached mapping when the user returns from the OAuth redirect.
    void qc.invalidateQueries({ queryKey: ["myMapping"] })
    window.location.assign(slackConnectUrl())
  }

  return (
    <SettingsSection
      title="账号映射"
      description="仅当你计划从 Slack 触发 XiheAgent 时才需要连接 Slack。手动 Gitee 任务使用服务端仓库令牌。"
    >
      <div className="divide-y divide-border">
        <SettingsRow
          label="当前账号"
          control={
            <span className="text-xs text-muted-foreground">
              {session.login}
            </span>
          }
        />
        <SettingsRow
          label="Slack 账号"
          description={
            connected
              ? `已关联 Slack 成员 ${slackUserId}${workEmail ? ` · ${workEmail}` : ""}。`
              : "尚未连接；手动执行 Gitee 任务不需要 Slack。"
          }
          control={
            <div className="flex items-center gap-2">
              <span
                className={cn(
                  "rounded-full px-2 py-0.5 text-[0.625rem] font-medium",
                  connected
                    ? "bg-primary/10 text-primary"
                    : "bg-muted text-muted-foreground"
                )}
              >
                {connected ? "已连接" : "未连接"}
              </span>
              {session.slack_oauth_enabled ? (
                <Button
                  size="sm"
                  variant={connected ? "outline" : "default"}
                  onClick={connect}
                  disabled={connecting || mapping.isLoading}
                >
                  <IoLogoSlack className="size-4" />
                  {connecting
                    ? "正在跳转..."
                    : connected
                      ? "重新连接"
                      : "连接 Slack"}
                </Button>
              ) : (
                <span className="text-[0.625rem] text-muted-foreground">
                  当前无法使用 Slack 登录
                </span>
              )}
            </div>
          }
        />
      </div>
    </SettingsSection>
  )
}

function MySettingsPage() {
  const session = useSession()
  const qc = useQueryClient()
  const navigate = useNavigate()
  const profile = useProfile()
  const options = useOptions()
  const save = useSaveProfile()
  const teamSettings = useQuery({
    queryKey: ["teamSettings"],
    queryFn: api.getTeamSettings,
    enabled: !!session.data,
  })
  const [error, setError] = useState<string | null>(null)

  if (session.isLoading) {
    return (
      <main className="p-6">
        <Skeleton className="h-40 w-full" />
      </main>
    )
  }
  if (!session.data) return <Navigate to="/login" />

  const handleLogout = async () => {
    await api.logout()
    qc.setQueryData(["session"], null)
    void navigate({ to: "/login" })
  }

  const firstModel = options.data?.models[0]
  const fallbackModel = options.data?.default_agent_model ?? firstModel?.id ?? ""
  const fallbackEffort =
    options.data?.default_agent_reasoning_effort ??
    firstModel?.default_effort ??
    ""

  const draftChoice = toChoice(profile.data?.review_draft_prs)
  const teamDefaultOn = teamSettings.data?.review_draft_prs ?? false
  const teamDefaultLabel = `使用团队默认值（当前：${teamDefaultOn ? "开启" : "关闭"}）`

  const handleDraftChoiceChange = (next: DraftReviewChoice) => {
    setError(null)
    save
      .mutateAsync(
        buildProfileUpdate(
          profile.data,
          { review_draft_prs: fromChoice(next) },
          fallbackModel,
          fallbackEffort
        )
      )
      .catch((e: Error) => setError(e.message))
  }

  return (
    <AppShell user={session.data} title="个人设置">
      <SettingsSection title="个人资料">
        <SettingsRow
          label="邮箱"
          control={
            <span className="text-xs text-muted-foreground">
              {session.data.email ?? "-"}
            </span>
          }
        />
      </SettingsSection>

      <UserMappingSection session={session.data} />

      <SettingsSection title="代码审查">
        <SettingsRow
          label="审查我的草稿 PR"
          description="决定是否审查你创建的草稿 PR；选择团队默认值时使用管理员的全局设置。"
          control={
            <Select
              value={draftChoice}
              onValueChange={(v) =>
                handleDraftChoiceChange(v as DraftReviewChoice)
              }
              disabled={profile.isLoading || save.isPending}
            >
              <SelectTrigger className="w-56">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="team_default">{teamDefaultLabel}</SelectItem>
                <SelectItem value="always_on">
                  始终审查我的草稿
                </SelectItem>
                <SelectItem value="always_off">
                  从不审查我的草稿
                </SelectItem>
              </SelectContent>
            </Select>
          }
        />
      </SettingsSection>

      <SettingsSection title="账号">
        <SettingsRow
          label="退出登录"
          description="结束当前会话。"
          control={
            <Button
              size="sm"
              variant="outline"
              onClick={() => void handleLogout()}
            >
              退出登录
            </Button>
          }
        />
      </SettingsSection>

      {error && <p className="text-xs text-destructive">{error}</p>}
    </AppShell>
  )
}
