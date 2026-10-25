"use client";
import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { useMe } from "@/components/useMe";
import { type Client, clients } from "@/lib/connectors";
import { githubStatus } from "@/lib/githubStatus";

const PLACEHOLDER = "fw_your_api_key";

type Repo = {
  full_name: string;
  upstream: string | null;
  account_login: string;
};

type Mode = "signin" | "hosted" | "local";
const MODE_LABEL: Record<Mode, string> = { signin: "Sign in (no key)", hosted: "Hosted (API key)", local: "On my machine" };
/** The ways a client can connect, the key-free one first when it has one. */
const modesFor = (c: Client): Mode[] => [...(c.signIn ? (["signin"] as const) : []), "hosted", ...(c.local ? (["local"] as const) : [])];

export default function ConnectClient({ installUrl }: { installUrl: string | null }) {
  const [origin, setOrigin] = useState("");
  const [key, setKey] = useState("");
  const [active, setActive] = useState(clients[0].id);
  const [mode, setMode] = useState<Mode>(modesFor(clients[0])[0]);
  const [copied, setCopied] = useState(false);
  const [me] = useMe();
  const [repos, setRepos] = useState<Repo[] | null>(null);
  const [reposError, setReposError] = useState(false);
  const [githubNote, setGithubNote] = useState<{
    text: string;
    ok: boolean;
  } | null>(null);

  useEffect(() => {
    const fromHash = () => {
      const id = window.location.hash.slice(1);
      const c = clients.find((x) => x.id === id);
      if (c) {
        setActive(c.id);
        setMode(modesFor(c)[0]);
      }
    };
    fromHash();
    window.addEventListener("hashchange", fromHash);
    return () => window.removeEventListener("hashchange", fromHash);
  }, []);

  useEffect(() => {
    setOrigin(window.location.origin);
    setGithubNote(githubStatus(window.location.search));
  }, []);

  // Repositories are only asked for once a session is known, so a signed-out visit makes no
  // request that is bound to fail.
  useEffect(() => {
    if (!me) return;
    fetch("/api/github/repositories", { cache: "no-store" })
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(String(r.status)))))
      .then((x) => setRepos(x.repositories ?? []))
      .catch(() => setReposError(true));
  }, [me]);

  const client = clients.find((c) => c.id === active) ?? clients[0];
  const url = `${origin || "https://your-forkward-host"}/mcp`;
  const k = key.trim() || PLACEHOLDER;
  const modes = modesFor(client);
  const showLocal = mode === "local" && !!client.local;
  const showSignIn = mode === "signin" && !!client.signIn;
  const snippet = useMemo(
    () => (showLocal ? client.local! : showSignIn ? client.signIn!.snippet(url) : client.hosted(url, k)),
    [showLocal, showSignIn, client, url, k],
  );
  const needsKey = mode === "hosted" && !client.urlOnly && !key.trim();
  const oneClick = mode === "hosted" && client.oneClick && key.trim() ? client.oneClick(url, key.trim()) : null;

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(snippet);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      setCopied(false);
    }
  };

  return (
    <div className="wrap pb-16 pt-10">
      <h1 className="t-h1">Connect Forkward to your tools</h1>
      <p className="mt-2 max-w-[62ch] text-muted">Pick your forks, then add Forkward to your coding assistant.</p>

      <section aria-labelledby="github-app" className="sheet mt-8 max-w-2xl p-5">
        <h2 id="github-app" className="flex items-center gap-2 font-semibold">
          <span className="step-num">1</span>Choose your forks
        </h2>
        <p className="t-label mt-1 text-muted">Forkward only sees the forks you pick here.</p>
        {githubNote && (
          <p role="status" className={`t-label mt-3 border-l-2 pl-3 ${githubNote.ok ? "border-success" : "border-danger"}`}>
            {githubNote.text}
          </p>
        )}
        {reposError && (
          <p role="alert" className="t-label mt-3 text-danger">
            Could not load the repositories Forkward can see. Reload the page to try again.
          </p>
        )}
        {installUrl ? (
          me ? (
            <a href={installUrl} className="btn btn-accent btn-sm mt-4 inline-flex">
              {repos?.length ? "Add or remove repositories" : "Install the GitHub App"}
            </a>
          ) : (
            <Link href="/signin?next=/connect" className="btn btn-accent btn-sm mt-4 inline-flex">
              Sign in with GitHub first
            </Link>
          )
        ) : (
          <p className="t-label mt-3 text-muted">The GitHub App is not set up on this server yet.</p>
        )}
        {repos && repos.length > 0 && (
          <ul className="t-label mt-4 space-y-1">
            {repos.map((r) => (
              <li key={r.full_name}>
                <span className="font-mono">{r.full_name}</span>
                {r.upstream && <span className="text-muted"> follows {r.upstream}</span>}
              </li>
            ))}
          </ul>
        )}
      </section>

      <h2 className="mt-10 flex items-center gap-2 font-semibold">
        <span className="step-num">2</span>Add Forkward to your assistant
      </h2>
      <div className="mt-4 grid gap-6 md:grid-cols-[220px_minmax(0,1fr)]">
        <nav aria-label="Clients" className="flex flex-wrap gap-2 md:flex-col md:gap-1">
          {clients.map((c) => (
            <button
              key={c.id}
              onClick={() => {
                setActive(c.id);
                setMode(modesFor(c)[0]);
              }}
              aria-pressed={c.id === active}
              className={`rounded-btn px-3 py-2 text-left text-[15px] ${c.id === active ? "bg-sunken font-semibold text-ink" : "text-muted hover:bg-sunken hover:text-ink"}`}
            >
              {c.name}
            </button>
          ))}
        </nav>

        <section aria-live="polite" className="min-w-0">
          <div className="flex flex-wrap items-center gap-3">
            <h2 className="t-h2">{client.name}</h2>
          </div>
          {modes.length > 1 && (
            <div role="group" aria-label="How to connect" className="mt-4 inline-flex flex-wrap rounded-full border border-line p-1">
              {modes.map((m) => (
                <button
                  key={m}
                  onClick={() => setMode(m)}
                  aria-pressed={mode === m}
                  className={`rounded-full px-4 py-1.5 text-[14px] ${mode === m ? "bg-ink text-surface" : "text-muted"}`}
                >
                  {MODE_LABEL[m]}
                </button>
              ))}
            </div>
          )}
          <p className="t-label mt-4 text-muted">{showLocal ? "Runs on your machine with your own model key. Free." : showSignIn ? client.signIn!.where : client.where}</p>

          {mode === "hosted" && !client.urlOnly && (
            <div className="mt-4 flex flex-wrap items-end gap-3">
              <label className="grid min-w-[240px] flex-1 gap-1 t-label">
                API key
                <input
                  type="password"
                  autoComplete="off"
                  spellCheck={false}
                  placeholder="fw_..."
                  value={key}
                  onChange={(e) => setKey(e.target.value)}
                  className="input font-mono text-[14px]"
                />
              </label>
              <Link href={me ? "/keys" : "/signin?next=/keys"} className="btn btn-secondary btn-sm">
                {me ? "Create a key" : "Sign in to get a key"}
              </Link>
            </div>
          )}
          <div className="mt-3 overflow-hidden rounded-btn border border-line bg-sunken">
            <div className="flex items-center justify-between border-b border-line px-3 py-1.5">
              <span className="t-label text-muted">{client.lang === "text" ? "URL" : client.lang}</span>
              <button onClick={copy} disabled={needsKey} className="btn btn-secondary btn-sm">
                {copied ? "Copied" : "Copy"}
              </button>
            </div>
            <pre className="overflow-x-auto p-4 font-mono text-[13px] leading-relaxed">
              <code>{snippet}</code>
            </pre>
          </div>

          {oneClick && (
            <a href={oneClick.href} className="btn btn-accent mt-4 inline-flex">
              {oneClick.label}
            </a>
          )}
                    {needsKey && <p className="t-label mt-3 text-muted">Paste a key to copy this{client.oneClick ? " or install in one click" : ""}.</p>}
          {(showSignIn || (mode === "hosted" && client.urlOnly)) && (
            <p className="t-label mt-3 text-muted">No key needed. You sign in with GitHub the first time it connects.</p>
          )}

          <div className="mt-8 border-t border-line pt-5">
            <h3 className="font-semibold">Try it</h3>
            <ul className="mt-2 list-disc space-y-1 pl-5 text-muted">
              <li>“Preview syncing my fork with its upstream.”</li>
              <li>“What changed in my forks since yesterday?”</li>
              <li>“How many Forkward units have I used this month?”</li>
            </ul>
          </div>
        </section>
      </div>
    </div>
  );
}
