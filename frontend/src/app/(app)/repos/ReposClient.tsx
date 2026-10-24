"use client";
import Link from "next/link";
import { useMe } from "@/components/useMe";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { headline } from "@/components/job/words";
import { listRepos, saveRepo, startJob } from "@/lib/api/jobs";
import { isActive, type JobKind, type RepoRow, UPSTREAM_RE } from "@/lib/jobs";

const GITHUB_STATUS: Record<string, { text: string; ok: boolean }> = {
  installed: { text: "The Forkward GitHub App is installed. Its repositories are listed below.", ok: true },
  requested: { text: "Your organization owner has to approve the Forkward GitHub App before it can be used.", ok: false },
  "not-yours": { text: "That installation is not one your GitHub account can access, so nothing was recorded.", ok: false },
  claimed: { text: "That installation is already connected to another Forkward workspace.", ok: false },
  invalid: { text: "GitHub sent an incomplete installation link. Install the app again.", ok: false },
  "signin-github": { text: "Sign in with GitHub again, then reinstall the app.", ok: false },
  "github-failed": { text: "GitHub did not answer. Try again in a minute.", ok: false },
};

const ago = (ts: string) => {
  const s = (Date.now() - new Date(ts).getTime()) / 1000;
  if (s < 90) return "just now";
  if (s < 5400) return `${Math.round(s / 60)} minutes ago`;
  if (s < 129600) return `${Math.round(s / 3600)} hours ago`;
  return `${Math.round(s / 86400)} days ago`;
};

function Settings({ repo, onSaved, onCancel }: { repo: RepoRow; onSaved: () => void; onCancel: () => void }) {
  const [upstream, setUpstream] = useState(repo.upstream ?? "");
  const [schedule, setSchedule] = useState(repo.schedule ?? "");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const save = async (e: React.FormEvent) => {
    e.preventDefault();
    const up = upstream.trim();
    if (up && !UPSTREAM_RE.test(up)) return setError("Use owner/name@branch, for example torvalds/linux@master.");
    setBusy(true);
    setError(null);
    try {
      await saveRepo(repo.id, up || null, schedule || null);
      onSaved();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <form onSubmit={save} className="mt-3 grid gap-3 rounded-tile border border-line bg-raised p-4 sm:grid-cols-[1fr_160px_auto] sm:items-end">
      <label className="grid gap-1 text-[14px] font-medium">
        Upstream it follows
        <input className="input font-mono text-[14px]" value={upstream} onChange={(e) => setUpstream(e.target.value)} placeholder="torvalds/linux@master" aria-invalid={!!error} />
      </label>
      <label className="grid gap-1 text-[14px] font-medium">
        Sync on its own
        <select className="input" value={schedule} onChange={(e) => setSchedule(e.target.value)}>
          <option value="">Never</option>
          <option value="hourly">Every hour</option>
          <option value="daily">Every day</option>
          <option value="weekly">Every week</option>
        </select>
      </label>
      <div className="flex gap-2">
        <button className="btn btn-accent btn-sm" disabled={busy}>Save</button>
        <button type="button" className="btn btn-secondary btn-sm" onClick={onCancel}>Cancel</button>
      </div>
      {error && <p role="alert" className="t-label text-danger sm:col-span-3">{error}</p>}
    </form>
  );
}

function Repo({ repo, reload }: { repo: RepoRow; reload: () => void }) {
  const router = useRouter();
  const [editing, setEditing] = useState(!repo.upstream);
  const [busy, setBusy] = useState<JobKind | null>(null);
  const [error, setError] = useState<string | null>(null);
  const running = repo.latest && isActive(repo.latest.status);

  const start = async (kind: JobKind) => {
    setBusy(kind);
    setError(null);
    try {
      const id = await startJob(repo.id, kind);
      router.push(`/jobs/${id}`);
    } catch (e) {
      setError((e as Error).message);
      setBusy(null);
    }
  };

  const [owner, name] = repo.full_name.split("/");
  const schedule = { hourly: "every hour", daily: "every day", weekly: "every week" }[repo.schedule ?? ""];
  return (
    <li className="py-5">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="min-w-0">
          <h2 className="font-mono text-[17px]">
            <span className="text-muted">{owner}/</span><span className="font-semibold">{name}</span>
          </h2>
          <p className="mt-1 text-[14px] text-muted">
            {repo.upstream ? <>Follows <span className="font-mono text-ink">{repo.upstream}</span>. {schedule ? `Syncs ${schedule}.` : "Syncs when you ask."}</> : "Set the upstream this fork follows to start syncing."}{" "}
            {!editing && <button className="link text-[14px]" onClick={() => setEditing(true)}>Change</button>}
          </p>
        </div>
        {repo.upstream && (
          <div className="flex flex-wrap gap-2">
            <button className="btn btn-secondary btn-sm" disabled={!!busy || !!running} onClick={() => start("preview")}>{busy === "preview" ? "Starting…" : "Preview"}</button>
            <button className="btn btn-accent btn-sm" disabled={!!busy || !!running} onClick={() => start("sync")}>{busy === "sync" ? "Starting…" : "Sync now"}</button>
          </div>
        )}
      </div>
      {editing && (
        <Settings repo={repo} onCancel={() => setEditing(false)} onSaved={() => { setEditing(false); reload(); }} />
      )}
      {error && <p role="alert" className="mt-2 t-label text-danger">{error}</p>}
      {repo.latest && (
        <Link href={`/jobs/${repo.latest.id}`} className="latest mt-3 flex flex-wrap items-baseline gap-x-3 gap-y-1 rounded-btn px-3 py-2 text-[14px]">
          <span className={`font-medium ${repo.latest.status === "failed" ? "text-danger" : ""}`}>
            {repo.latest.kind === "preview" ? "Last preview" : "Last sync"}: {headline(repo.latest)}
          </span>
          <span className="text-faint">{ago(repo.latest.created_at)}</span>
          <span className="link ml-auto">{running ? "Watch it" : "Open"}</span>
        </Link>
      )}
    </li>
  );
}

export default function ReposClient({ installUrl }: { installUrl: string | null }) {
  const [repos, setRepos] = useState<RepoRow[] | null>(null);
  const [error, setError] = useState<{ message: string; status?: number } | null>(null);
  const [note, setNote] = useState<{ text: string; ok: boolean } | null>(null);

  const load = useCallback(async () => {
    try {
      setRepos(await listRepos());
      setError(null);
    } catch (e) {
      const err = e as Error & { status?: number };
      setError({ message: err.message, status: err.status });
    }
  }, []);

  const [me] = useMe();
  useEffect(() => {
    const s = new URLSearchParams(window.location.search).get("github");
    if (s && GITHUB_STATUS[s]) setNote(GITHUB_STATUS[s]);
  }, []);
  useEffect(() => {
    if (!me) return;
    load();
    // Keeps "Last sync" lines current while a run is going.
    const t = setInterval(load, 8000);
    return () => clearInterval(t);
  }, [load, me]);

  if (me === null || error?.status === 401)
    return (
      <div className="wrap pb-16 pt-10">
        <h1 className="t-h1">Your repositories</h1>
        <p className="mt-2 text-muted">Sign in to see the forks Forkward keeps in sync.</p>
        <Link href="/signin?next=/repos" className="btn btn-accent mt-6">Sign in with GitHub</Link>
      </div>
    );

  return (
    <div className="wrap max-w-[880px] pb-16 pt-10">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="t-h1">Your repositories</h1>
          <p className="mt-2 max-w-[60ch] text-muted">Preview is a dry run that pushes nothing. Sync now opens a pull request on a new branch. Both use units.</p>
        </div>
        {installUrl && <a href={installUrl} className="btn btn-secondary btn-sm">Add repositories on GitHub</a>}
      </div>
      {note && <p role="status" className={`mt-5 border-l-2 pl-3 text-[14px] ${note.ok ? "border-success" : "border-danger"}`}>{note.text}</p>}
      {error && <p role="alert" className="mt-5 text-danger">{error.message}</p>}
      {(me === undefined || repos === null) && !error && <p className="mt-8 text-muted">Loading your repositories…</p>}
      {repos?.length === 0 && (
        <div className="mt-8 border-t border-line pt-6">
          <h2 className="t-h2">Connect a fork</h2>
          <p className="mt-2 max-w-[56ch] text-muted">Install the GitHub App on the forks to keep in step. Forkward only pushes new forkward/ branches.</p>
          {installUrl ? <a href={installUrl} className="btn btn-accent mt-5">Install the GitHub App</a> : <p className="mt-4 t-label text-danger">The GitHub App is not set up on this server yet.</p>}
        </div>
      )}
      {repos && repos.length > 0 && (
        <ul className="mt-6 divide-y divide-line border-y border-line">
          {repos.map((r) => <Repo key={r.id} repo={r} reload={load} />)}
        </ul>
      )}
    </div>
  );
}
