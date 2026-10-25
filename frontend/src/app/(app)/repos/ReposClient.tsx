"use client";
import Link from "next/link";
import { useMe } from "@/components/useMe";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { DriftRail } from "@/components/DriftRail";
import { headline } from "@/components/job/words";
import { listRepos, saveRepo, startJob } from "@/lib/api/jobs";
import { githubStatus } from "@/lib/githubStatus";
import { isActive, type JobKind, type RepoRow, UPSTREAM_RE } from "@/lib/jobs";

const ago = (ts: string) => {
  const s = (Date.now() - new Date(ts).getTime()) / 1000;
  if (s < 90) return "just now";
  if (s < 5400) return `${Math.round(s / 60)} minutes ago`;
  if (s < 129600) return `${Math.round(s / 3600)} hours ago`;
  return `${Math.round(s / 86400)} days ago`;
};

function Settings({ repo, onSaved, onCancel }: { repo: RepoRow; onSaved: () => void; onCancel?: () => void }) {
  const [upstream, setUpstream] = useState(repo.upstream ?? "");
  const [schedule, setSchedule] = useState(repo.schedule ?? "");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const first = !repo.upstream;
  const valid = UPSTREAM_RE.test(upstream.trim());
  const save = async (e: React.FormEvent) => {
    e.preventDefault();
    const up = upstream.trim();
    if (up && !UPSTREAM_RE.test(up)) return setError("Use owner/name@branch, for example torvalds/linux@master.");
    setBusy(true);
    setError(null);
    try {
      await saveRepo(repo.id, up || null, up ? schedule || null : null);
      onSaved();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <form onSubmit={save} className="fork-form mt-4 grid gap-3 sm:grid-cols-[minmax(0,1fr)_170px_auto] sm:items-end">
      <label className="grid gap-1.5 text-[14px] font-medium">
        {first ? "Which project does this fork follow?" : "Upstream"}
        <input
          className="input font-mono text-[14px]"
          value={upstream}
          onChange={(e) => setUpstream(e.target.value)}
          placeholder="owner/name@branch"
          spellCheck={false}
          autoCapitalize="off"
          aria-invalid={!!error}
          aria-describedby={`up-hint-${repo.id}`}
        />
      </label>
      <label className="grid gap-1.5 text-[14px] font-medium">
        Sync on its own
        <select className="input" value={schedule} onChange={(e) => setSchedule(e.target.value)} disabled={!valid}>
          <option value="">Only when I ask</option>
          <option value="hourly">Every hour</option>
          <option value="daily">Every day</option>
          <option value="weekly">Every week</option>
        </select>
      </label>
      <div className="flex gap-2">
        <button className="btn btn-accent btn-sm" disabled={busy || (first && !valid)}>
          {busy ? "Saving…" : first ? "Follow upstream" : "Save changes"}
        </button>
        {onCancel && (
          <button type="button" className="btn btn-secondary btn-sm" onClick={onCancel}>
            Cancel
          </button>
        )}
      </div>
      <p id={`up-hint-${repo.id}`} className={`t-label sm:col-span-3 ${error ? "text-danger" : "text-muted"}`} role={error ? "alert" : undefined}>
        {error ?? "For example torvalds/linux@master."}
      </p>
    </form>
  );
}

function Repo({ repo, reload }: { repo: RepoRow; reload: () => void }) {
  const router = useRouter();
  const [editing, setEditing] = useState(false);
  const [busy, setBusy] = useState<JobKind | null>(null);
  const [error, setError] = useState<{ message: string; status?: number } | null>(null);
  const running = repo.latest && isActive(repo.latest.status);

  const start = async (kind: JobKind) => {
    setBusy(kind);
    setError(null);
    try {
      const id = await startJob(repo.id, kind);
      router.push(`/jobs/${id}`);
    } catch (e) {
      const err = e as Error & { status?: number };
      setError({ message: err.message, status: err.status });
      setBusy(null);
    }
  };

  const [owner, name] = repo.full_name.split("/");
  const schedule = {
    hourly: "Syncs every hour",
    daily: "Syncs every day",
    weekly: "Syncs every week",
  }[repo.schedule ?? ""];
  return (
    <li className="fork-card">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <h2 className="min-w-0 break-all font-mono text-[17px] leading-snug">
          <span className="text-muted">{owner}/</span>
          <span className="font-semibold">{name}</span>
        </h2>
        {repo.upstream && (
          <div className="flex gap-2">
            <button className="btn btn-secondary btn-sm" disabled={!!busy || !!running} onClick={() => start("preview")} title="A dry run that pushes nothing">
              {busy === "preview" ? "Starting…" : "Preview"}
            </button>
            <button className="btn btn-accent btn-sm" disabled={!!busy || !!running} onClick={() => start("sync")} title="Opens a pull request on a new branch">
              {busy === "sync" ? "Starting…" : "Sync now"}
            </button>
          </div>
        )}
      </div>

      <div className="mt-3">
        <DriftRail upstream={repo.upstream} latest={repo.latest} />
        <div className="flex justify-between gap-3 font-mono text-[12px] text-muted">
          <span>your fork{repo.default_branch ? ` · ${repo.default_branch}` : ""}</span>
          <span className="truncate text-right">{repo.upstream ? `upstream · ${repo.upstream}` : "upstream not set"}</span>
        </div>
      </div>

      {repo.latest && (
        <Link href={`/jobs/${repo.latest.id}`} className="latest mt-4 flex flex-wrap items-baseline gap-x-3 gap-y-1 rounded-btn px-3 py-2 text-[14px]">
          <span className={`font-medium ${repo.latest.status === "failed" ? "text-danger" : ""}`}>
            {repo.latest.kind === "preview" ? "Last preview" : "Last sync"}: {headline(repo.latest)}
          </span>
          <span className="text-faint">{ago(repo.latest.created_at)}</span>
          <span className="link ml-auto">{running ? "Watch it" : "Open"}</span>
        </Link>
      )}

      {repo.upstream && !editing && (
        <p className="mt-3 text-[14px] text-muted">
          {schedule ?? "Syncs only when you ask"}.{" "}
          <button className="link text-[14px]" onClick={() => setEditing(true)}>
            Change
          </button>
        </p>
      )}
      {(editing || !repo.upstream) && (
        <Settings
          repo={repo}
          onCancel={repo.upstream ? () => setEditing(false) : undefined}
          onSaved={() => {
            setEditing(false);
            reload();
          }}
        />
      )}
      {error && (
        <p role="alert" className="mt-3 t-label text-danger">
          {error.message}{" "}
          {error.status === 402 && (
            <Link href="/billing" className="link">
              See usage
            </Link>
          )}
        </p>
      )}
    </li>
  );
}

export default function ReposClient({ installUrl }: { installUrl: string | null }) {
  const [repos, setRepos] = useState<RepoRow[] | null>(null);
  const [error, setError] = useState<{
    message: string;
    status?: number;
  } | null>(null);
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
    setNote(githubStatus(window.location.search));
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
        <p className="mt-2 text-muted">Sign in to see your forks.</p>
        <Link href="/signin?next=/repos" className="btn btn-accent mt-6">
          Sign in with GitHub
        </Link>
      </div>
    );

  return (
    <div className="wrap max-w-[880px] pb-16 pt-10">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="t-h1">
            Your forks{repos && repos.length > 0 && <span className="ml-3 align-middle font-mono text-[18px] font-normal text-faint">{repos.length}</span>}
          </h1>
          <p className="mt-2 max-w-[62ch] text-muted">
            <strong className="font-semibold text-ink">Preview</strong> is a dry run. <strong className="font-semibold text-ink">Sync now</strong> opens a pull request.
          </p>
        </div>
        {installUrl && (
          <a href={installUrl} className="btn btn-secondary btn-sm">
            Add forks on GitHub
          </a>
        )}
      </div>
      {note && (
        <p role="status" className={`mt-5 border-l-2 pl-3 text-[14px] ${note.ok ? "border-success" : "border-danger"}`}>
          {note.text}
        </p>
      )}
      {error && (
        <p role="alert" className="mt-5 text-danger">
          {error.message}
        </p>
      )}
      {(me === undefined || repos === null) && !error && <p className="mt-8 text-muted">Loading your repositories…</p>}
      {repos?.length === 0 && (
        <div className="mt-8 border-t border-line pt-6">
          <h2 className="t-h2">Connect a fork</h2>
          <p className="mt-2 max-w-[56ch] text-muted">Install the GitHub App on the forks you want kept current.</p>
          {installUrl ? (
            <a href={installUrl} className="btn btn-accent mt-5">
              Install the GitHub App
            </a>
          ) : (
            <p className="mt-4 t-label text-danger">The GitHub App is not set up on this server yet.</p>
          )}
        </div>
      )}
      {repos && repos.length > 0 && (
        <ul className="mt-8 grid gap-5">
          {repos.map((r) => (
            <Repo key={r.id} repo={r} reload={load} />
          ))}
        </ul>
      )}
    </div>
  );
}
