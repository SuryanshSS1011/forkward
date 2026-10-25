import { serverEnv } from "./env";

const API = "https://api.github.com";

/** Where a signed-in user installs the forkward-bot GitHub App. GitHub then sends them to the App's
 * Setup URL, /api/github/installed. */
export function installUrl(): string | null {
  const slug = serverEnv("GITHUB_APP_SLUG");
  return slug && /^[A-Za-z0-9-]{1,100}$/.test(slug) ? `https://github.com/apps/${slug}/installations/new` : null;
}

async function gh<T>(path: string, token: string): Promise<T> {
  const res = await fetch(`${API}${path}`, {
    headers: {
      Authorization: `Bearer ${token}`,
      Accept: "application/vnd.github+json",
      "X-GitHub-Api-Version": "2022-11-28",
      "User-Agent": "forkward-web",
    },
    cache: "no-store",
  });
  if (!res.ok) throw new Error(`GitHub ${path} returned ${res.status}`);
  return (await res.json()) as T;
}

export type Installation = {
  id: number;
  account: { id: number; login: string; type: string } | null;
  repository_selection: "all" | "selected";
  suspended_at: string | null;
};

export type Repository = { id: number; full_name: string; default_branch?: string; fork: boolean };

/** The installations of this GitHub App that the user's token can access. */
export async function userInstallations(token: string): Promise<Installation[]> {
  const out: Installation[] = [];
  for (let page = 1; page <= 10; page++) {
    const r = await gh<{ installations: Installation[] }>(`/user/installations?per_page=100&page=${page}`, token);
    out.push(...r.installations);
    if (r.installations.length < 100) break;
  }
  return out;
}

/** Whether the user may connect this installation to their tenant: it is their own account, or they
 * are an active admin of its organization. Seeing an installation is not enough, because any org
 * member with read access to one repository sees it, and the bot then pushes on the tenant's behalf.
 * The org check needs the App's "Members: read" permission. */
export async function canManageInstallation(token: string, installation: Installation): Promise<boolean> {
  const account = installation.account;
  if (!account) return false;
  if (account.type === "User") return (await gh<{ id: number }>("/user", token)).id === account.id;
  if (account.type !== "Organization") return false;
  try {
    const m = await gh<{ state: string; role: string }>(`/user/memberships/orgs/${encodeURIComponent(account.login)}`, token);
    return m.state === "active" && m.role === "admin";
  } catch {
    return false;
  }
}

/** The repositories one installation grants that the user can also access. */
export async function installationRepositories(token: string, installationId: number): Promise<Repository[]> {
  const out: Repository[] = [];
  for (let page = 1; page <= 10; page++) {
    const r = await gh<{ repositories: Repository[] }>(`/user/installations/${installationId}/repositories?per_page=100&page=${page}`, token);
    out.push(...r.repositories);
    if (r.repositories.length < 100) break;
  }
  return out;
}

/** The upstream a fork follows, as owner/name@branch, or null if GitHub reports no parent. */
export async function forkUpstream(token: string, fullName: string): Promise<string | null> {
  const r = await gh<{ parent?: { full_name: string; default_branch: string } }>(`/repos/${fullName}`, token);
  return r.parent ? `${r.parent.full_name}@${r.parent.default_branch}` : null;
}
