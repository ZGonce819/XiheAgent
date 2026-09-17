import {  clsx } from "clsx"
import { twMerge } from "tailwind-merge"
import type {ClassValue} from "clsx";

export function cn(...inputs: Array<ClassValue>) {
  return twMerge(clsx(inputs))
}

/**
 * 跨平台路径验证结果。
 */
export interface PathValidation {
  /** 路径格式是否有效 */
  valid: boolean
  /** 错误信息（valid 为 false 时有值） */
  error: string
  /** 检测到的平台类型 */
  platform: "windows" | "unix" | "unknown"
}

/**
 * 验证文件路径格式是否合法，兼容 Windows 和 macOS/Linux。
 *
 * 规则：
 * - Windows: 盘符格式 `X:\path`，不允许 `<>:"|?*` 等非法字符
 * - macOS/Linux: 必须以 `/` 开头
 * - 不允许空路径
 *
 * @param rawPath 用户输入的原始路径
 * @param options 可选配置
 */
export function validatePath(
  rawPath: string,
  options?: {
    /** 禁止使用的盘符（如 ["C"] 表示不允许 C 盘） */
    forbiddenDrives?: string[]
  }
): PathValidation {
  const path = rawPath.trim()

  if (!path) {
    return { valid: false, error: "路径不能为空", platform: "unknown" }
  }

  // Windows 路径: X:\... 或 X:/...
  const windowsMatch = /^([a-zA-Z]):[/\\]/.exec(path)
  if (windowsMatch) {
    const drive = windowsMatch[1]!.toUpperCase()

    // 检查非法字符 (Windows 不允许 <>:"|?*)，需排除盘符后的冒号
    const pathWithoutDrive = path.slice(3) // 去掉 "X:/" 或 "X:\" 前缀
    const illegalChars = /[<>:"|?*]/
    if (illegalChars.test(pathWithoutDrive)) {
      return {
        valid: false,
        error: "路径包含非法字符（不能使用 <>:\"|?*）",
        platform: "windows",
      }
    }

    // 检查禁止的盘符
    if (options?.forbiddenDrives?.map(d => d.toUpperCase()).includes(drive)) {
      return {
        valid: false,
        error: `不支持将 ${drive} 盘设为工作目录，请选择其他磁盘`,
        platform: "windows",
      }
    }

    return { valid: true, error: "", platform: "windows" }
  }

  // Unix/macOS 路径: 必须以 / 开头
  if (path.startsWith("/")) {
    // macOS/Linux 路径中不允许空字节
    if (path.includes("\0")) {
      return {
        valid: false,
        error: "路径包含非法字符",
        platform: "unix",
      }
    }
    return { valid: true, error: "", platform: "unix" }
  }

  // 既不是 Windows 也不是 Unix 格式
  return {
    valid: false,
    error: "路径格式无效。Windows 需以盘符开头（如 D:\\），macOS/Linux 需以 / 开头",
    platform: "unknown",
  }
}
