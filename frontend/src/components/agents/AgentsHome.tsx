import { useState } from "react"
import { FolderGit2 } from "lucide-react"

import type { ModelSelection } from "@/lib/agents/useModelOptions"
import { AgentPromptBar } from "@/components/agents/AgentPromptBar"
import { SlackConnectDialog } from "@/components/agents/SlackConnectDialog"
import { Logo } from "@/components/agents/ported/Logo"
import { useCreateAgentThread } from "@/lib/agents/queries"
import { useModelOptions } from "@/lib/agents/useModelOptions"
import { useProfile, useRepos } from "@/lib/profile"

export function AgentsHome() {
  const createThread = useCreateAgentThread()
  const createError =
    createThread.error instanceof Error ? createThread.error.message : null
  const { models, defaultSelection } = useModelOptions()
  const [selection, setSelection] = useState<ModelSelection | null>(null)
  const activeSelection = selection ?? defaultSelection

  const reposQuery = useRepos()
  const profileQuery = useProfile()
  // undefined = untouched (fall back to the profile default); null = explicitly "no repo".
  const [repoOverride, setRepoOverride] = useState<string | null | undefined>(
    undefined
  )
  const repo =
    repoOverride === undefined
      ? (profileQuery.data?.default_repo ?? null)
      : repoOverride

  return (
    <div className="flex min-w-0 flex-1 flex-col overflow-y-auto">
      <SlackConnectDialog />
      <header className="flex h-14 shrink-0 items-center justify-between border-b border-[var(--ui-border)] px-5 md:px-8 max-md:pl-14">
        <div className="flex items-center gap-3">
          <FolderGit2 className="size-4 text-[color:var(--ui-text-dim)]" />
          <h1 className="text-sm font-medium text-[color:var(--ui-text)]">新任务</h1>
        </div>
        <span className="text-[0.6875rem] text-[color:var(--ui-text-dim)]">本地工作区</span>
      </header>
      <main className="mx-auto flex w-full max-w-3xl flex-1 flex-col px-5 pt-[18vh] pb-12 md:px-8 max-md:pt-28">
        <div className="mb-5 flex items-end justify-between gap-4 border-b border-[var(--ui-border)] pb-3">
          <div>
            <Logo className="h-auto" />
            <p className="mt-1 text-xs text-[color:var(--ui-text-dim)]">本地代码任务</p>
          </div>
        </div>
        <div className="w-full">
          <AgentPromptBar
            onSubmit={(prompt, images) =>
              createThread.mutate({
                prompt,
                images,
                repo,
                repo_explicitly_none: repoOverride === null,
                model_id: activeSelection?.modelId ?? null,
                effort: activeSelection?.effort ?? null,
              })
            }
            disabled={createThread.isPending}
            models={models}
            selection={activeSelection}
            onSelectionChange={setSelection}
            repos={reposQuery.data?.repositories}
            selectedRepo={repo}
            onRepoChange={setRepoOverride}
          />
          {createError ? (
            <p className="mt-3 w-full border-l-2 border-[var(--ui-danger)] bg-[var(--ui-panel)] px-3 py-2 text-sm text-[color:var(--ui-danger)]">
              {createError}
            </p>
          ) : null}
        </div>
      </main>
    </div>
  )
}
