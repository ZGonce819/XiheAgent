import { useEffect, useState } from "react"
import { FolderGit2 } from "lucide-react"

import type { ModelSelection } from "@/lib/agents/useModelOptions"
import type { ImageChunk } from "@/lib/agents/types"
import { AgentPromptBar } from "@/components/agents/AgentPromptBar"
import { useCreateAgentThread } from "@/lib/agents/queries"
import { useModelOptions } from "@/lib/agents/useModelOptions"
import { useProfile, useRepos } from "@/lib/profile"
import { ConfirmDialog } from "@/components/ui/confirm-dialog"

export function AgentsHome({ initialProject = null }: { initialProject?: string | null } = {}) {
  const createThread = useCreateAgentThread()
  const createError =
    createThread.error instanceof Error ? createThread.error.message : null
  const { models, defaultSelection } = useModelOptions()
  const [selection, setSelection] = useState<ModelSelection | null>(null)
  const [submission, setSubmission] = useState<{
    prompt: string
    images: Array<ImageChunk>
  } | null>(null)
  const activeSelection = selection ?? defaultSelection

  const reposQuery = useRepos()
  const profileQuery = useProfile()
  const sandboxAvailable = profileQuery.data?.sandbox_available ?? false
  // 从侧边栏项目「+」进入时强制本地环境；否则沿用 profile 默认
  const [backendType, setBackendType] = useState<string>(
    initialProject ? "local" : (profileQuery.data?.backendType ?? "local")
  )
  const [sandboxWarning, setSandboxWarning] = useState(false)
  const [repoWarning, setRepoWarning] = useState(false)
  const [selectedProject, setSelectedProject] = useState<string | null>(initialProject)

  const handleBackendTypeChange = (next: string) => {
    if (next === "sandbox" && !sandboxAvailable) {
      setSandboxWarning(true)
      return
    }
    setBackendType(next)
  }

  // undefined = untouched (fall back to the profile default); null = explicitly "no repo".
  const [repoOverride, setRepoOverride] = useState<string | null | undefined>(
    undefined
  )
  const repo =
    repoOverride === undefined
      ? (profileQuery.data?.default_repo ?? null)
      : repoOverride

  const hasSubmitted = submission !== null

  // 后端返回沙箱未开启错误时，也弹出提示
  useEffect(() => {
    if (createError && /沙盒|沙箱/i.test(createError)) {
      setSandboxWarning(true)
    }
  }, [createError])

  return (
    <div className="new-task-page-enter flex min-w-0 flex-1 flex-col overflow-y-auto">
      <header className="flex h-14 shrink-0 items-center justify-between border-b border-[var(--ui-border)] px-5 max-md:pl-14 md:px-8">
        <div className="flex items-center gap-3">
          <FolderGit2 className="size-4 text-[color:var(--ui-text-dim)]" />
          <h1 className="text-sm font-medium text-[color:var(--ui-text)]">
            新任务
          </h1>
        </div>
        <span className="text-[0.6875rem] text-[color:var(--ui-text-dim)]">
          {backendType === "sandbox" ? "沙箱工作区" : "本地工作区"}
        </span>
      </header>
      <main
        className={`mx-auto flex w-full flex-1 flex-col px-5 pb-4 md:px-8 ${hasSubmitted ? "new-task-conversation max-w-4xl" : "max-w-3xl pt-[18vh] pb-12 max-md:pt-28"}`}
      >
        {!hasSubmitted && (
          <div className="mb-5 flex items-end justify-between gap-4 border-b border-[var(--ui-border)] pb-3">
            <div>
              <span className="font-heading text-sm font-semibold text-[color:var(--ui-text)]">XiheAgent</span>
              <p className="mt-1 text-xs text-[color:var(--ui-text-dim)]">
                本地代码任务
              </p>
            </div>
          </div>
        )}
        {hasSubmitted ? (
          <div className="flex min-h-0 flex-1 flex-col">
            <div className="min-h-0 flex-1 overflow-y-auto py-6">
              <div className="new-task-user-message ml-auto flex w-full flex-col items-end">
                {submission.images.length > 0 ? (
                  <div className="mb-2 flex flex-wrap justify-end gap-2">
                    {submission.images.map((image, index) => (
                      <img
                        key={`${image.fileName ?? "image"}-${index}`}
                        src={`data:${image.mimeType};base64,${image.base64}`}
                        alt={image.fileName || "已发送的图片"}
                        className="max-h-48 max-w-48 rounded-md border border-[var(--ui-border)]"
                      />
                    ))}
                  </div>
                ) : null}
                {submission.prompt ? (
                  <div className="w-fit max-w-[min(74%,52rem)] rounded-xl bg-[var(--ui-accent-bubble)] px-4 py-3 text-[0.9375rem] leading-7 text-[color:var(--ui-text)]">
                    {submission.prompt}
                  </div>
                ) : null}
              </div>
              <div className="new-task-status mt-16 border-b border-[var(--ui-border)] pb-2 text-xs text-[color:var(--ui-text-muted)]">
                正在启动智能体运行...
              </div>
            </div>
            <div className="new-task-input shrink-0 pt-4">
              <AgentPromptBar
                placeholder="继续输入指令"
                compact
                busy
                disabled
                onSubmit={() => undefined}
                models={models}
                selection={activeSelection}
                onSelectionChange={setSelection}
              />
            </div>
          </div>
        ) : (
          <div className="w-full">
            <AgentPromptBar
              onSubmit={(prompt, images) => {
                if (!repo) {
                  setRepoWarning(true)
                  return false
                }
                setSubmission({ prompt, images })
                createThread.mutate(
                  {
                    prompt,
                    images,
                    repo,
                    repo_explicitly_none: repoOverride === null,
                    model_id: activeSelection?.modelId ?? null,
                    effort: activeSelection?.effort ?? null,
                    backend_type: backendType,
                    project_name: selectedProject,
                  },
                  {
                    onError: () => setSubmission(null),
                  }
                )
              }}
              disabled={createThread.isPending}
              models={models}
              selection={activeSelection}
              onSelectionChange={setSelection}
              repos={reposQuery.data?.repositories}
              selectedRepo={repo}
              onRepoChange={setRepoOverride}
              backendType={backendType}
              onBackendTypeChange={handleBackendTypeChange}
              selectedProject={selectedProject}
              onProjectChange={setSelectedProject}
            />
            {createError ? (
              <p className="mt-3 w-full border-l-2 border-[var(--ui-danger)] bg-[var(--ui-panel)] px-3 py-2 text-sm text-[color:var(--ui-danger)]">
                {createError}
              </p>
            ) : null}
          </div>
        )}
      </main>
      <ConfirmDialog
        open={sandboxWarning}
        title="沙箱未开启"
        description="您未开启沙盒配置，请开启后再尝试"
        confirmText="知道了"
        showCancel={false}
        onOpenChange={(open) => {
          if (!open) setSandboxWarning(false)
        }}
        onConfirm={() => setSandboxWarning(false)}
      />
      <ConfirmDialog
        open={repoWarning}
        title="未选择仓库"
        description="请先选择一个代码仓库后再发送消息"
        confirmText="知道了"
        showCancel={false}
        onOpenChange={(open) => {
          if (!open) setRepoWarning(false)
        }}
        onConfirm={() => setRepoWarning(false)}
      />
    </div>
  )
}
