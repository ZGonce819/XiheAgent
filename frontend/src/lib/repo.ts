export type RepoProvider = "github" | "gitee" | "codeup"

export function detectRepoProvider(raw: string): RepoProvider | null {
  const value = raw.trim().toLowerCase()
  if (value.includes("github.com")) return "github"
  if (value.includes("gitee.com")) return "gitee"
  if (value.includes("codeup.aliyun.com")) return "codeup"
  return null
}

/** Normalize a repository input for matching connected GitHub/Gitee repos.
 * Codeup keeps its three-part org/owner/repo path intact for manual entries.
 */
export function normalizeRepoFullName(raw: string): string | null {
  let v = raw.trim();
  for (const prefix of [
    "https://gitee.com/",
    "http://gitee.com/",
    "gitee.com/",
    "https://github.com/",
    "http://github.com/",
    "github.com/",
    "https://codeup.aliyun.com/",
    "http://codeup.aliyun.com/",
    "codeup.aliyun.com/",
  ]) {
    if (v.toLowerCase().startsWith(prefix)) {
      v = v.slice(prefix.length);
    }
  }
  v = v.replace(/\/$/, "").replace(/\.git$/, "");
  const parts = v.split("/").filter(Boolean);
  if (parts.length === 2) return `${parts[0]}/${parts[1]}`;
  if (parts.length === 3 && detectRepoProvider(raw) === "codeup") {
    return parts.join("/");
  }
  return null;
}
