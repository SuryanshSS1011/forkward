"use client";
import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { clients } from "@/lib/connectors";

const PLACEHOLDER = "fw_your_api_key";

const GITHUB_STATUS: Record<string, string> = {
  installed: "The Forkward GitHub App is installed. Its repositories are listed below.",
  requested: "Your organization owner has to approve the Forkward GitHub App before it can be used.",
  "not-yours": "That installation is not one your GitHub account can access, so nothing was recorded.",
  "not-admin": "Only an owner of that GitHub organization (or of the personal account) can connect its installation.",
  "not-owner": "Only the owner of this Forkward account can connect a GitHub installation.",
  claimed: "That installation is already connected to another Forkward account.",
  invalid: "GitHub sent an incomplete installation link. Try installing again.",
  "signin-github": "Sign in with GitHub again, then reinstall the app.",
  "github-failed": "GitHub did not answer. Try again in a minute.",
};

type Repo = { full_name: string; upstream: string | null; account_login: string };

export default function ConnectClient({ installUrl }: { installUrl: string | null }) {
  const [origin, setOrigin] = useState("");
  const [key, setKey] = useState("");
  const [active, setActive] = useState(clients[0].id);
  const [mode, setMode] = useState<"hosted" | "local">("hosted");
  const [copied, setCopied] = useState(false);
  const [me, setMe] = useState<{ name: string; email: string } | null>(null);
  const [repos, setRepos] = useState<Repo[] | null>(null);
  const [githubNote, setGithubNote] = useState<string | null>(null);

  useEffect(() => {
    const fromHash = () => {
      const id = window.location.hash.slice(1);
      const c = clients.find((x) => x.id === id);
      if (c) {
        setActive(c.id);
        if (!c.local) setMode("hosted");
      }
    };
    fromHash();
    window.addEventListener("hashchange", fromHash);
    return () => window.removeEventListener("hashchange", fromHash);
  }, []);

  useEffect(() => {
    setOrigin(window.location.origin);
    const status = new URLSearchParams(window.location.search).get("github");
    if (status && GITHUB_STATUS[status]) setGithubNote(GITHUB_STATUS[status]);
    // Repositories are only asked for once a session is known, so a signed-out visit makes no
    // request that is bound to fail.
    fetch("/api/me", { cache: "no-store" })
      .then((r) => (r.ok ? r.json() : { user: null }))
      .then((b) => {
        setMe(b.user ?? null);
        if (!b.user) return;
        return fetch("/api/github/repositories", { cache: "no-store" })
          .then((r) => (r.ok ? r.json() : { repositories: null }))
          .then((x) => setRepos(x.repositories ?? null));
      })
      .catch(() => setMe(null));
  }, []);

  const client = clients.find((c) => c.id === active) ?? clients[0];
  const url = `${origin || "https://your-forkward-host"}/mcp`;
  const k = key.trim() || PLACEHOLDER;
  const showLocal = mode === "local" && !!client.local;
  const snippet = useMemo(() => (showLocal ? client.local! : client.hosted(url, k)), [showLocal, client, url, k]);
  const needsKey = !showLocal && !client.urlOnly && !key.trim();
  const oneClick = !showLocal && client.oneClick && key.trim() ? client.oneClick(url, key.trim()) : null;

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
      <p className="mt-2 max-w-[62ch] text-muted">
        Install the GitHub App, then add Forkward to your assistant and ask it to sync a fork.
      </p>

      <section aria-labelledby="github-app" className="mt-8 max-w-2xl rounded-btn border border-line bg-raised p-4">
        <h2 id="github-app" className="flex items-center gap-2 font-semibold"><span className="step-num">1</span>Give Forkward your repositories</h2>
        <p className="t-label mt-1 text-muted">
          Pick the forks Forkward can see. You can change this on GitHub at any time.
        </p>
        {githubNote && <p role="status" className="t-label mt-3">{githubNote}</p>}
        {installUrl ? (
          me ? (
            <a href={installUrl} className="btn btn-primary btn-sm mt-4 inline-flex">{repos?.length ? "Add or remove repositories" : "Install the GitHub App"}</a>
          ) : (
            <Link href="/signin?next=/connect" className="btn btn-primary btn-sm mt-4 inline-flex">Sign in with GitHub first</Link>
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

      <h2 className="mt-10 flex items-center gap-2 font-semibold"><span className="step-num">2</span>Add Forkward to your assistant</h2>
      <div className="mt-3 max-w-2xl rounded-btn border border-line bg-raised p-4">
        <label htmlFor="apikey" className="t-label">Your API key</label>
        <input
          id="apikey"
          type="password"
          autoComplete="off"
          spellCheck={false}
          placeholder="fw_..."
          value={key}
          onChange={(e) => setKey(e.target.value)}
          className="mt-1 w-full rounded-btn border border-line bg-surface px-3 py-2 font-mono text-[14px] focus:outline-none focus-visible:ring-2 focus-visible:ring-[var(--focus)]"
        />
        <p className="t-label mt-2 text-muted">
          {me ? <>Signed in as {me.name || me.email}. <Link href="/keys" className="underline underline-offset-4">Create or revoke keys</Link>. </> : <><Link href="/signin?next=/keys" className="underline underline-offset-4">Sign in with GitHub</Link> to get a key. </>}
          It fills the setup below and never leaves this page.
        </p>
      </div>

      <div className="mt-8 grid gap-6 md:grid-cols-[220px_minmax(0,1fr)]">
        <nav aria-label="Clients" className="flex flex-wrap gap-2 md:flex-col md:gap-1">
          {clients.map((c) => (
            <button
              key={c.id}
              onClick={() => { setActive(c.id); if (!c.local) setMode("hosted"); }}
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
          {client.local && (
            <div role="group" aria-label="Where Forkward runs" className="mt-4 inline-flex rounded-full border border-line p-1">
              {(["hosted", "local"] as const).map((m) => (
                <button key={m} onClick={() => setMode(m)} aria-pressed={mode === m}
                  className={`rounded-full px-4 py-1.5 text-[14px] ${mode === m ? "bg-ink text-surface" : "text-muted"}`}>
                  {m === "hosted" ? "Hosted (API key)" : "On my machine"}
                </button>
              ))}
            </div>
          )}
          <p className="t-label mt-4 text-muted">{showLocal ? "Runs Forkward locally with your own model key; nothing is metered." : client.where}</p>

          <div className="mt-3 overflow-hidden rounded-btn border border-line bg-sunken">
            <div className="flex items-center justify-between border-b border-line px-3 py-1.5">
              <span className="t-label text-muted">{client.lang === "text" ? "URL" : client.lang}</span>
              <button onClick={copy} disabled={needsKey} className="btn btn-secondary btn-sm">{copied ? "Copied" : "Copy"}</button>
            </div>
            <pre className="overflow-x-auto p-4 font-mono text-[13px] leading-relaxed"><code>{snippet}</code></pre>
          </div>

          {oneClick && (
            <a href={oneClick.href} className="btn btn-primary mt-4 inline-flex">{oneClick.label}</a>
          )}
          {!showLocal && client.oneClick && !key.trim() && (
            <p className="t-label mt-3 text-muted">Paste your key above to enable one-click install.</p>
          )}
          {needsKey && <p className="t-label mt-3 text-muted">Paste your API key above to copy this setup.</p>}
          {!showLocal && client.urlOnly && (
            <p className="t-label mt-3 text-muted">No key needed. The app signs you in with GitHub the first time it connects.</p>
          )}

          <div className="mt-8 border-t border-line pt-5">
            <h3 className="font-semibold">Try it</h3>
            <ul className="mt-2 list-disc space-y-1 pl-5 text-muted">
              <li>“Preview merging egraphs-good/egglog@main into my fork.”</li>
              <li>“What changed in my forks since yesterday?”</li>
              <li>“How many Forkward units have I used this month?”</li>
            </ul>
          </div>
        </section>
      </div>
    </div>
  );
}
