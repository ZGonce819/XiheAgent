import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import { useQuery, useQueryClient } from "@tanstack/react-query"
import { Dialog } from "@base-ui/react/dialog"
import {
  ChevronDown,
  FolderOpen,
  Plus,
  Folder,
  Pencil,
  X,
} from "lucide-react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { api } from "@/lib/api"
import type { ProjectItem } from "@/lib/api"
import { cn } from "@/lib/utils"
import { validatePath } from "@/lib/utils"

export interface ProjectSelectorProps {
  /** 当前选中的项目名称 */
  selectedProject?: string | null
  /** 选中项目回调 */
  onProjectChange?: (project: string | null, localWorkDir?: string | null) => void
  /** 当前环境类型：sandbox 时隐藏本地相关选项 */
  backendType?: string
  className?: string
}

export function ProjectSelector({
  selectedProject,
  onProjectChange,
  backendType = "local",
  className,
}: ProjectSelectorProps) {
  const isSandbox = backendType === "sandbox"
  const [open, setOpen] = useState(false)
  const [createDialogOpen, setCreateDialogOpen] = useState(false)
  const [detailsDialogOpen, setDetailsDialogOpen] = useState(false)
  const [editingProject, setEditingProject] = useState<ProjectItem | null>(null)
  const [selectedWorkDir, setSelectedWorkDir] = useState<string | null>(null)
  const dropdownRef = useRef<HTMLDivElement>(null)
  const queryClient = useQueryClient()

  // 创建项目表单状态
  const [newProjectName, setNewProjectName] = useState("")
  const [localFolderPath, setLocalFolderPath] = useState("")
  const [picking, setPicking] = useState(false)
  const [saving, setSaving] = useState(false)
  const [bindError, setBindError] = useState<string | null>(null)
  const [detailsName, setDetailsName] = useState("")
  const [detailsFolder, setDetailsFolder] = useState("")
  const [detailsSaving, setDetailsSaving] = useState(false)
  const [detailsError, setDetailsError] = useState<string | null>(null)
  // 路径验证（实时）
  const folderValidation = useMemo(
    () => (localFolderPath.trim() ? validatePath(localFolderPath) : { valid: true, error: "", platform: "unknown" as const }),
    [localFolderPath]
  )
  const detailsFolderValidation = useMemo(
    () => (detailsFolder.trim() ? validatePath(detailsFolder) : { valid: true, error: "", platform: "unknown" as const }),
    [detailsFolder]
  )

  const projects = useQuery({
    queryKey: ["workspace-projects"],
    queryFn: api.listProjects,
  })

  // 点击外部关闭下拉
  useEffect(() => {
    function handleClickOutside(e: MouseEvent) {
      if (
        dropdownRef.current &&
        !dropdownRef.current.contains(e.target as Node)
      ) {
        setOpen(false)
      }
    }
    document.addEventListener("mousedown", handleClickOutside)
    return () => document.removeEventListener("mousedown", handleClickOutside)
  }, [])

  const handleSelectProject = useCallback(
    (name: string, localWorkDir: string | null = null) => {
      setOpen(false)
      setSelectedWorkDir(localWorkDir)
      onProjectChange?.(name, localWorkDir)
    },
    [onProjectChange]
  )

  const handleOpenDetails = useCallback(
    (item: ProjectItem) => {
      setOpen(false)
      setEditingProject(item)
      setDetailsName(item.name)
      setDetailsFolder(
        selectedProject === item.name && selectedWorkDir !== null
          ? selectedWorkDir
          : item.source === "workspace"
            ? item.path
            : ""
      )
      setDetailsError(null)
      setDetailsDialogOpen(true)
    },
    [selectedProject, selectedWorkDir]
  )

  const handleProjectClick = useCallback(
    (item: ProjectItem) => {
      if (selectedProject === item.name) {
        handleOpenDetails(item)
        return
      }
      handleSelectProject(
        item.name,
        item.source === "workspace" ? item.path : null
      )
    },
    [handleOpenDetails, handleSelectProject, selectedProject]
  )

  const handlePickFolder = useCallback(async () => {
    setPicking(true)
    try {
      const result = await api.pickFolder()
      if (result.path) {
        setLocalFolderPath(result.path)
        // 如果项目名称为空，自动用文件夹名填充
        if (!newProjectName.trim()) {
          const name = result.path.replace(/[\\/]+$/, "").split(/[\\/]/).pop() || ""
          setNewProjectName(name)
        }
      }
    } catch {
      // 用户取消或对话框不可用
    } finally {
      setPicking(false)
    }
  }, [newProjectName])

  const handleCreateConfirm = useCallback(async () => {
    const name = newProjectName.trim()
    if (!name) return
    // 如果有填写文件夹路径但验证不通过，阻止提交
    if (localFolderPath.trim() && !folderValidation.valid) return
    setSaving(true)
    setBindError(null)
    try {
      const folder = localFolderPath.trim()
      await api.bindProjectFolder({ project_name: name })
      await queryClient.invalidateQueries({ queryKey: ["workspace-projects"] })
      setCreateDialogOpen(false)
      setSelectedWorkDir(folder || null)
      onProjectChange?.(name, folder || null)
    } catch (err) {
      setBindError(err instanceof Error ? err.message : "绑定本地文件夹失败")
    } finally {
      setSaving(false)
    }
  }, [
    newProjectName,
    localFolderPath,
    folderValidation.valid,
    onProjectChange,
    queryClient,
  ])

  const handleOpenCreate = useCallback(() => {
    setOpen(false)
    setBindError(null)
    setNewProjectName("")
    setLocalFolderPath("")
    setCreateDialogOpen(true)
  }, [])

  const handleDetailsConfirm = useCallback(async () => {
    if (!editingProject) return
    const canRename = editingProject.source === "created"
    const nextName = canRename ? detailsName.trim() : editingProject.name
    const nextFolder = isSandbox ? "" : detailsFolder.trim()
    if (!nextName || (nextFolder && !detailsFolderValidation.valid)) return

    setDetailsSaving(true)
    setDetailsError(null)
    try {
      if (canRename && nextName !== editingProject.name) {
        await api.renameProject(editingProject.name, nextName)
      }
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["workspace-projects"] }),
        queryClient.invalidateQueries({ queryKey: ["agent-threads"] }),
      ])
      setSelectedWorkDir(nextFolder || null)
      onProjectChange?.(nextName, nextFolder || null)
      setDetailsDialogOpen(false)
    } catch (err) {
      setDetailsError(err instanceof Error ? err.message : "项目详细信息保存失败")
    } finally {
      setDetailsSaving(false)
    }
  }, [
    detailsFolder,
    detailsFolderValidation.valid,
    detailsName,
    editingProject,
    isSandbox,
    onProjectChange,
    queryClient,
  ])

  const createdProjects = projects.data?.created_projects ?? []
  const workspaceProjects = projects.data?.workspace_projects ?? []

  return (
    <>
      <div ref={dropdownRef} className={cn("relative", className)}>
        <button
          type="button"
          onClick={() => setOpen((v) => !v)}
          className="flex items-center gap-1 rounded-md border border-[var(--ui-border)] bg-[var(--ui-panel-2)] px-2 py-1 text-[0.75rem] text-[color:var(--ui-text-muted)] transition-colors hover:border-[var(--ui-accent)] hover:text-[color:var(--ui-text)]"
        >
          <Folder className="size-3" />
          <span className="max-w-[120px] truncate">
            {selectedProject || "项目"}
          </span>
          <ChevronDown
            className={cn(
              "size-3 transition-transform duration-200",
              open && "rotate-180"
            )}
          />
        </button>

        {open && (
          <div className="absolute left-0 top-full z-50 mt-1 min-w-[200px] origin-top-left animate-in fade-in-0 zoom-in-95 slide-in-from-top-1 duration-150 ease-out overflow-hidden rounded-md border border-[var(--ui-border)] bg-[var(--ui-surface)] shadow-lg">
            {/* 创建入口始终保留，方便连续创建多个项目。 */}
            <div className="border-b border-[var(--ui-border)] p-1">
              <button
                type="button"
                onClick={handleOpenCreate}
                className="flex w-full items-center gap-2 rounded-sm px-2.5 py-1.5 text-left text-xs text-[color:var(--ui-text)] transition-colors hover:bg-[var(--ui-panel-2)]"
              >
                <Plus className="size-3.5 text-[color:var(--ui-accent)]" />
                <span>创建新项目</span>
              </button>
            </div>

            <div className="max-h-[260px] overflow-y-auto">
              {/* 已创建项目：项目名是唯一展示名称。 */}
              {createdProjects.length > 0 && (
                <div className="p-1">
                  {createdProjects.map((item: ProjectItem, index: number) => (
                    <button
                      key={`created:${item.name}`}
                      type="button"
                      onClick={() => handleProjectClick(item)}
                      title={selectedProject === item.name ? "再次点击编辑项目详细信息" : undefined}
                      className={cn(
                        "flex w-full items-center gap-2 rounded-sm px-2.5 py-1.5 text-left text-xs transition-colors hover:bg-[var(--ui-panel-2)] animate-in fade-in-0 slide-in-from-left-1",
                        selectedProject === item.name
                          ? "text-[color:var(--ui-text)] bg-[var(--ui-panel-2)]/50"
                          : "text-[color:var(--ui-text-muted)]"
                      )}
                      style={{ animationDelay: `${Math.min(index * 30, 150)}ms` }}
                    >
                      <FolderOpen className="size-3 shrink-0 text-[color:var(--ui-text-dim)]" />
                      <span className="min-w-0 flex-1 truncate">{item.name}</span>
                      {selectedProject === item.name && (
                        <Pencil className="size-3 shrink-0 text-[color:var(--ui-text-dim)]" />
                      )}
                    </button>
                  ))}
                </div>
              )}

              {/* 工作目录项目：与已创建项目视觉分组，并展示完整文件夹地址。 */}
              {workspaceProjects.length > 0 && (
                <div className="border-t border-[var(--ui-border)] p-1 pt-1.5">
                  {workspaceProjects.map((item: ProjectItem) => (
                    <button
                      key={`workspace:${item.path}`}
                      type="button"
                      onClick={() => handleProjectClick(item)}
                      title={selectedProject === item.name ? "再次点击编辑项目详细信息" : item.path}
                      className={cn(
                        "flex w-full items-start gap-2 rounded-sm px-2.5 py-1.5 text-left text-xs transition-colors hover:bg-[var(--ui-panel-2)]",
                        selectedProject === item.name
                          ? "bg-[var(--ui-panel-2)]/50 text-[color:var(--ui-text)]"
                          : "text-[color:var(--ui-text-muted)]"
                      )}
                    >
                      <Folder className="mt-0.5 size-3 shrink-0 text-[color:var(--ui-text-dim)]" />
                      <span className="flex min-w-0 flex-1 flex-col gap-0.5">
                        <span className="truncate">{item.name}</span>
                        <span
                          className="truncate text-[0.65rem] text-[color:var(--ui-text-dim)]"
                          title={item.path}
                        >
                          {item.path}
                        </span>
                      </span>
                      {selectedProject === item.name && (
                        <Pencil className="mt-0.5 size-3 shrink-0 text-[color:var(--ui-text-dim)]" />
                      )}
                    </button>
                  ))}
                </div>
              )}
            </div>

            {/* 空状态 */}
            {createdProjects.length === 0 && workspaceProjects.length === 0 && !projects.isLoading && (
              <div className="px-3 py-4 text-center text-xs text-[color:var(--ui-text-dim)]">
                暂无项目，点击上方按钮创建
              </div>
            )}

            {/* 加载中 */}
            {projects.isLoading && (
              <div className="px-3 py-3 text-center text-xs text-[color:var(--ui-text-dim)]">
                加载中…
              </div>
            )}
          </div>
        )}
      </div>

      {/* 创建新项目弹窗 */}
      <Dialog.Root open={createDialogOpen} onOpenChange={setCreateDialogOpen}>
        <Dialog.Portal>
          <Dialog.Backdrop className="fixed inset-0 z-50 bg-black/40 data-open:animate-in data-open:fade-in-0 data-closed:animate-out data-closed:fade-out-0" />
          <Dialog.Popup className="fixed top-1/2 left-1/2 z-50 w-[min(28rem,calc(100vw-2rem))] -translate-x-1/2 -translate-y-1/2 rounded-lg border border-border bg-popover shadow-xl outline-none data-open:animate-in data-open:fade-in-0 data-open:zoom-in-95 data-closed:animate-out data-closed:fade-out-0 data-closed:zoom-out-95">
            {/* 标题栏 */}
            <div className="flex items-center justify-between border-b border-border px-5 py-4">
              <Dialog.Title className="text-sm font-semibold text-foreground">
                创建新项目
              </Dialog.Title>
              <Dialog.Close className="flex size-7 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-muted hover:text-foreground">
                <X className="size-4" />
              </Dialog.Close>
            </div>

            {/* 表单内容 */}
            <div className="space-y-4 px-5 py-4">
              {/* 项目名称 */}
              <div className="space-y-1.5">
                <label className="text-xs font-medium text-foreground">
                  项目名称
                </label>
                <Input
                  value={newProjectName}
                  onChange={(e) => setNewProjectName(e.target.value)}
                  placeholder="输入项目名称"
                  autoFocus
                />
              </div>

              {/* 添加本地文件夹 — 沙箱时隐藏 */}
              {!isSandbox && (
                <div className="space-y-1.5 animate-in fade-in-0 slide-in-from-top-2 duration-200">
                  <label className="text-xs font-medium text-foreground">
                    本地文件夹（可选）
                  </label>
                  <div className="flex items-center gap-2">
                    <Input
                      value={localFolderPath}
                      onChange={(e) => setLocalFolderPath(e.target.value)}
                      placeholder="选择或输入本地文件夹路径"
                      className={cn(
                        "flex-1",
                        localFolderPath.trim() && !folderValidation.valid && "border-destructive focus-visible:ring-destructive/30"
                      )}
                    />
                    <Button
                      variant="outline"
                      size="sm"
                      disabled={picking}
                      onClick={handlePickFolder}
                      className="shrink-0 gap-1.5"
                    >
                      <FolderOpen className="size-3.5" />
                      {picking ? "选择中…" : "浏览"}
                    </Button>
                  </div>
                  {/* 路径验证错误提示 */}
                  {localFolderPath.trim() && !folderValidation.valid && (
                    <p className="animate-in text-xs text-destructive fade-in-0 slide-in-from-top-1 duration-150">
                      {folderValidation.error}
                    </p>
                  )}
                  <p className="text-[0.65rem] text-[color:var(--ui-text-dim)]">
                    仅用于即将创建的当前对话，不会影响该项目下的其他对话。
                  </p>
                </div>
              )}
            </div>

            {/* 底部按钮 */}
            <div className="flex items-center justify-end gap-2 border-t border-border px-5 py-3">
              {bindError && (
                <p className="mr-auto text-xs text-destructive">{bindError}</p>
              )}
              <Button
                variant="outline"
                size="sm"
                disabled={saving}
                onClick={() => setCreateDialogOpen(false)}
              >
                取消
              </Button>
              <Button
                size="sm"
                disabled={
                  saving ||
                  !newProjectName.trim() ||
                  (Boolean(localFolderPath.trim()) && !folderValidation.valid)
                }
                onClick={handleCreateConfirm}
              >
                {saving ? "创建中…" : "创建"}
              </Button>
            </div>
          </Dialog.Popup>
        </Dialog.Portal>
      </Dialog.Root>

      {/* 当前项目详细信息弹窗 */}
      <Dialog.Root open={detailsDialogOpen} onOpenChange={setDetailsDialogOpen}>
        <Dialog.Portal>
          <Dialog.Backdrop className="fixed inset-0 z-50 bg-black/40 data-open:animate-in data-open:fade-in-0 data-closed:animate-out data-closed:fade-out-0" />
          <Dialog.Popup className="fixed top-1/2 left-1/2 z-50 w-[min(28rem,calc(100vw-2rem))] -translate-x-1/2 -translate-y-1/2 rounded-lg border border-border bg-popover shadow-xl outline-none data-open:animate-in data-open:fade-in-0 data-open:zoom-in-95 data-closed:animate-out data-closed:fade-out-0 data-closed:zoom-out-95">
            <div className="flex items-center justify-between border-b border-border px-5 py-4">
              <Dialog.Title className="text-sm font-semibold text-foreground">
                项目详细信息
              </Dialog.Title>
              <Dialog.Close
                aria-label="关闭项目详细信息"
                title="关闭"
                className="flex size-7 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-muted hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              >
                <X className="size-4" />
              </Dialog.Close>
            </div>

            <div className="space-y-4 px-5 py-4">
              <div className="space-y-1.5">
                <label htmlFor="project-details-name" className="text-xs font-medium text-foreground">
                  项目名称
                </label>
                <Input
                  id="project-details-name"
                  value={detailsName}
                  onChange={(event) => setDetailsName(event.target.value)}
                  disabled={editingProject?.source === "workspace"}
                  autoFocus={editingProject?.source === "created"}
                />
                {editingProject?.source === "workspace" && (
                  <p className="text-[0.6875rem] text-[color:var(--ui-text-dim)]">
                    名称来自工作目录文件夹，不能在此修改。
                  </p>
                )}
              </div>

              {!isSandbox && (
                <div className="space-y-1.5">
                  <label htmlFor="project-details-folder" className="text-xs font-medium text-foreground">
                    当前任务工作目录
                  </label>
                  <div className="flex items-center gap-2">
                    <Input
                      id="project-details-folder"
                      value={detailsFolder}
                      onChange={(event) => setDetailsFolder(event.target.value)}
                      placeholder="选择或输入本地文件夹路径"
                      className={cn(
                        "flex-1",
                        detailsFolder.trim() && !detailsFolderValidation.valid && "border-destructive focus-visible:ring-destructive/30"
                      )}
                    />
                    <Button
                      type="button"
                      variant="outline"
                      size="sm"
                      disabled={picking}
                      onClick={async () => {
                        setPicking(true)
                        try {
                          const result = await api.pickFolder()
                          if (result.path) setDetailsFolder(result.path)
                        } catch {
                          // 用户取消或系统文件夹选择器不可用。
                        } finally {
                          setPicking(false)
                        }
                      }}
                      className="shrink-0 gap-1.5"
                    >
                      <FolderOpen className="size-3.5" />
                      {picking ? "选择中…" : "浏览"}
                    </Button>
                  </div>
                  {detailsFolder.trim() && !detailsFolderValidation.valid && (
                    <p className="text-xs text-destructive">
                      {detailsFolderValidation.error}
                    </p>
                  )}
                  <p className="text-[0.6875rem] text-[color:var(--ui-text-dim)]">
                    仅影响即将创建的当前对话，不会改变其他对话。
                  </p>
                </div>
              )}
            </div>

            <div className="flex items-center justify-end gap-2 border-t border-border px-5 py-3">
              {detailsError && (
                <p className="mr-auto text-xs text-destructive">{detailsError}</p>
              )}
              <Button
                variant="outline"
                size="sm"
                disabled={detailsSaving}
                onClick={() => setDetailsDialogOpen(false)}
              >
                取消
              </Button>
              <Button
                size="sm"
                disabled={
                  detailsSaving ||
                  !detailsName.trim() ||
                  (Boolean(detailsFolder.trim()) && !detailsFolderValidation.valid)
                }
                onClick={handleDetailsConfirm}
              >
                {detailsSaving ? "保存中…" : "保存"}
              </Button>
            </div>
          </Dialog.Popup>
        </Dialog.Portal>
      </Dialog.Root>
    </>
  )
}
