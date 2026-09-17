export interface CronPreset {
  id: "hourly" | "daily" | "weekly"
  label: string
  value: string
}

export const CRON_PRESETS: Array<CronPreset> = [
  { id: "hourly", label: "每小时", value: "0 * * * *" },
  { id: "daily", label: "每天", value: "0 9 * * *" },
  { id: "weekly", label: "每周", value: "0 9 * * 1" },
]

const DAY_NAMES = ["周日", "周一", "周二", "周三", "周四", "周五", "周六"]

function pad(n: number): string {
  return n.toString().padStart(2, "0")
}

function formatTime(minute: number, hour: number): string {
  return `UTC ${pad(hour)}:${pad(minute)}`
}

/** Best-effort human description of a 5-field cron expression. */
export function describeCron(expr: string): string {
  const parts = expr.trim().split(/\s+/)
  if (parts.length !== 5) return expr
  const [min, hour, dom, mon, dow] = parts
  if (!min || !hour || !dom || !mon || !dow) return expr

  if (
    min === "0" &&
    hour === "*" &&
    dom === "*" &&
    mon === "*" &&
    dow === "*"
  ) {
    return "每小时"
  }

  const everyNHours = hour.match(/^\*\/(\d+)$/)
  if (everyNHours && min === "0" && dom === "*" && mon === "*" && dow === "*") {
    return `每 ${everyNHours[1]} 小时`
  }

  const minNum = Number(min)
  const hourNum = Number(hour)
  const timeKnown =
    Number.isInteger(minNum) &&
    Number.isInteger(hourNum) &&
    minNum >= 0 &&
    hourNum >= 0

  if (timeKnown && dom === "*" && mon === "*") {
    if (dow === "*") return `每天 ${formatTime(minNum, hourNum)}`
    if (dow === "1-5") return `工作日 ${formatTime(minNum, hourNum)}`
    const dowNum = Number(dow)
    if (Number.isInteger(dowNum) && dowNum >= 0 && dowNum <= 7) {
      const name = DAY_NAMES[dowNum % 7]
      return `每${name} ${formatTime(minNum, hourNum)}`
    }
  }

  return expr
}

/** Match a cron expression back to a known preset id, if any. */
export function presetForCron(expr: string): CronPreset["id"] | "custom" {
  const normalized = expr.trim().split(/\s+/).join(" ")
  const match = CRON_PRESETS.find((p) => p.value === normalized)
  return match?.id ?? "custom"
}
