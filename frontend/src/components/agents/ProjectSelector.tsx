import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import { useQuery } from "@tanstack/react-query"
import { Dialog } from "@base-ui/react/dialog"
import {
  ChevronDown,
  FolderOpen,
  Plus,
  Folder,
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
  onProjectChange?: (project: string | null) => void
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
  const [open, setOpen] = useState(false)
  const [createDialogOpen, setCreateDialogOpen] = useState(false)
  const dropdownRef = useRef<HTMLDivElement>(null)

  // 创建项目表单状态
  const [newProjectName, setNewProjectName] = useState("")
  const [localFolderPath, setLocalFolderPath] = useState("")
  const [picking, setPicking] = useState(false)
  // 已创建的项目名：非空时顶部按钮变为项目行样式，再次点击可编辑
  const [createdProject, setCreatedProject] = useState<string | null>(null)

  // 路径验证（实时）
  const folderValidation = useMemo(
    () => (localFolderPath.trim() ? validatePath(localFolderPath) : { valid: true, error: "", platform: "unknown" as const }),
    [localFolderPath]
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
    (name: string) => {
      setOpen(false)
      onProjectChange?.(name)
    },
    [onProjectChange]
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

  const handleCreateConfirm = useCallback(() => {
    if (!newProjectName.trim()) return
    // 如果有填写文件夹路径但验证不通过，阻止提交
    if (localFolderPath.trim() && !folderValidation.valid) return
    setCreateDialogOpen(false)
    // 记录已创建项目；表单内容保留，供再次点击时编辑
    setCreatedProject(newProjectName.trim())
    onProjectChange?.(newProjectName.trim())
  }, [newProjectName, localFolderPath, folderValidation.valid, onProjectChange])

  const handleOpenCreate = useCallback(() => {
    setOpen(false)
    setCreateDialogOpen(true)
  }, [])

  const projectList = projects.data?.projects ?? []
  const isSandbox = backendType === "sandbox"

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
            {/* 创建操作：已创建项目后显示为项目行，再次点击可编辑 */}
            <div className="border-b border-[var(--ui-border)] p-1">
              {createdProject ? (
                <button
                  type="button"
                  onClick={handleOpenCreate}
                  title="点击编辑项目"
                  className="flex w-full items-center gap-2 rounded-sm px-2.5 py-1.5 text-left text-xs text-[color:var(--ui-text-muted)] transition-colors hover:bg-[var(--ui-panel-2)]"
                >
                  <FolderOpen className="size-3 shrink-0 text-[color:var(--ui-text-dim)]" />
                  <span className="flex-1 truncate">{createdProject}</span>
                </button>
              ) : (
                <button
                  type="button"
                  onClick={handleOpenCreate}
                  className="flex w-full items-center gap-2 rounded-sm px-2.5 py-1.5 text-left text-xs text-[color:var(--ui-text)] transition-colors hover:bg-[var(--ui-panel-2)]"
                >
                  <Plus className="size-3.5 text-[color:var(--ui-accent)]" />
                  <span>创建新项目</span>
                </button>
              )}
            </div>

            {/* 项目列表 */}
            {projectList.length > 0 && (
              <div className="max-h-[200px] overflow-y-auto p-1">
                {projectList.map((item: ProjectItem, index: number) => (
                  <button
                    key={item.path}
                    type="button"
                    onClick={() => handleSelectProject(item.name)}
                    className={cn(
                      "flex w-full items-center gap-2 rounded-sm px-2.5 py-1.5 text-left text-xs transition-colors hover:bg-[var(--ui-panel-2)] animate-in fade-in-0 slide-in-from-left-1",
                      selectedProject === item.name
                        ? "text-[color:var(--ui-text)] bg-[var(--ui-panel-2)]/50"
                        : "text-[color:var(--ui-text-muted)]"
                    )}
                    style={{ animationDelay: `${Math.min(index * 30, 150)}ms` }}
                  >
                    <FolderOpen className="size-3 shrink-0 text-[color:var(--ui-text-dim)]" />
                    <span className="flex-1 truncate">{item.name}</span>
                    {selectedProject === item.name && (
                      <span className="text-[color:var(--ui-text-dim)]">✓</span>
                    )}
                  </button>
                ))}
              </div>
            )}

            {/* 空状态 */}
            {projectList.length === 0 && !projects.isLoading && (
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
                {createdProject ? "编辑项目" : "创建新项目"}
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
                    添加本地文件夹
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
                </div>
              )}
            </div>

            {/* 底部按钮 */}
            <div className="flex justify-end gap-2 border-t border-border px-5 py-3">
              <Button
                variant="outline"
                size="sm"
                onClick={() => setCreateDialogOpen(false)}
              >
                取消
              </Button>
              <Button
                size="sm"
                disabled={
                  !newProjectName.trim() ||
                  (Boolean(localFolderPath.trim()) && !folderValidation.valid)
                }
                onClick={handleCreateConfirm}
              >
                {createdProject ? "保存" : "创建"}
              </Button>
            </div>
          </Dialog.Popup>
        </Dialog.Portal>
      </Dialog.Root>
    </>
  )
}
