import {
  BookOpenTextIcon,
  RobotIcon,
  TrashIcon,
} from "@phosphor-icons/react"

import { Button } from "@/components/ui/button"
import type { AgentSkill, SkillOrigin } from "@/lib/api"

/**
 * 技能来源的展示元数据。
 *
 * 只有智能体自动沉淀的技能才会在界面上标注来源——那是用户需要知道的信息
 * （这类技能是模型自己写的，可能不够准确，可以随时删除）。内置技能与用户
 * 手动创建则不需要徽章：前者不可删、后者是用户自己的操作，标注只会增加噪音。
 */
export const ORIGIN_META: Record<
  SkillOrigin,
  { label: string | null; icon: typeof BookOpenTextIcon; description: string }
> = {
  builtin: {
    label: null,
    icon: BookOpenTextIcon,
    description: "随产品发布，升级时不会被覆盖用户改动。",
  },
  user: {
    label: null,
    icon: BookOpenTextIcon,
    description: "由你通过界面或上传创建。",
  },
  agent: {
    label: "智能体创建",
    icon: RobotIcon,
    description: "由智能体在任务中自动沉淀，可随时删除。",
  },
}

interface SkillRowProps {
  skill: AgentSkill
  index: number
  onDelete: () => void
}

export function SkillRow({ skill, index, onDelete }: SkillRowProps) {
  const meta = ORIGIN_META[skill.origin] ?? ORIGIN_META.user
  const OriginIcon = meta.icon
  return (
    <article
      className="skill-row flex gap-3 px-4 py-3.5"
      style={{ animationDelay: `${Math.min(index * 30, 150)}ms` }}
    >
      <div className="skill-icon mt-0.5 flex size-8 shrink-0 items-center justify-center rounded-md bg-primary/10 text-primary">
        <OriginIcon className="size-4" />
      </div>
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-2">
          <h3 className="text-xs font-medium">{skill.name}</h3>
          <span className="rounded-sm border border-border bg-muted px-1.5 py-0.5 text-[0.625rem] text-muted-foreground">
            {skill.scopeLabel}
            {skill.projectName ? ` · ${skill.projectName}` : ""}
          </span>
          {meta.label && (
            <span
              className="rounded-sm border border-border bg-muted px-1.5 py-0.5 text-[0.625rem] text-muted-foreground"
              title={meta.description}
            >
              {meta.label}
            </span>
          )}
        </div>
        <p className="mt-1 text-xs leading-relaxed text-muted-foreground">
          {skill.description}
        </p>
        <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-[0.625rem] text-muted-foreground">
          <span className="flex items-center gap-1.5">
            <BookOpenTextIcon className="size-3" />
            <code className="truncate font-mono">{skill.virtualPath}</code>
          </span>
          {skill.extraFiles.length > 0 && (
            <span>{skill.extraFiles.length} 个附属文件</span>
          )}
        </div>
      </div>
      <Button
        size="sm"
        variant="ghost"
        className="shrink-0 text-muted-foreground hover:text-destructive"
        aria-label={`删除技能 ${skill.name}`}
        title="删除技能"
        onClick={onDelete}
      >
        <TrashIcon className="size-3.5" />
      </Button>
    </article>
  )
}
