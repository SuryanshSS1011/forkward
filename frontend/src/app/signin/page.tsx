"use client";
import { useEffect, useState } from "react";
import { MarketingShell } from "@/components/MarketingShell";
import { useMe } from "@/components/useMe";
import { authClient } from "@/lib/auth-client";

/** Only same-site paths, so `next` cannot send someone to another site after sign-in. */
const safeNext = (next: string | null) =>
  next && next.startsWith("/") && !next.startsWith("//") && !next.startsWith("/\\") ? next : "/repos";

export default function SignIn() {
  const [me] = useMe();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [next, setNext] = useState("/repos");
  const [oauthFlow, setOauthFlow] = useState(false);

  useEffect(() => {
    const q = new URLSearchParams(window.location.search);
    setNext(safeNext(q.get("next")));
    // An MCP client sent this person here to authorize it; Better Auth resumes that flow after sign-in.
    setOauthFlow(q.has("client_id") && q.has("sig"));
    if (q.get("error")) setError("GitHub did not finish signing you in. Try again.");
  }, []);

  const signIn = async () => {
    setBusy(true);
    setError(null);
    const { error: e } = await authClient.signIn.social({
      provider: "github",
      callbackURL: next,
      errorCallbackURL: "/signin?error=github",
    });
    if (e) {
      setError(e.message ?? "GitHub sign-in is not available right now.");
      setBusy(false);
    }
  };

  // Already signed in: go straight on to where they were headed.
  useEffect(() => {
    if (me && !oauthFlow) window.location.replace(next);
  }, [me, oauthFlow, next]);

  if (me && !oauthFlow)
    return (
      <MarketingShell>
        <section className="mx-auto w-full max-w-7xl px-6 py-20 lg:px-12">
          <p className="text-[var(--pa-ink-muted)]" role="status">Taking you to your workspace…</p>
        </section>
      </MarketingShell>
    );

  return (
    <MarketingShell>
      <section className="mx-auto w-full max-w-md px-6 py-16 lg:px-12">
        <span className="font-label text-[11px] font-bold uppercase tracking-wider text-[var(--pa-primary)]">Welcome</span>
        <h1 className="mt-1 font-display text-[32px] font-extrabold tracking-tight text-[var(--pa-ink)] md:text-[44px]">Sign in</h1>
        <p className="mt-3 text-[18px] leading-relaxed text-[var(--pa-ink-muted)]">
          {oauthFlow ? "Sign in to let your editor use Forkward on your behalf." : "Forkward uses your GitHub account. Free to start, no card needed."}
        </p>
        <div className="mt-8 border-2 border-[var(--pa-outline)] bg-[var(--pa-surface)] p-6 shadow-[4px_4px_0px_#8a716e]" style={{ borderRadius: "12px 5px 16px 8px / 6px 15px 7px 14px" }}>
          <button
            className="flex w-full items-center justify-center gap-2 border-2 border-[var(--pa-charcoal)] bg-[var(--pa-primary-container)] px-6 py-3 font-label text-[14px] font-bold text-[var(--pa-on-primary)] shadow-[4px_4px_0px_#33302a] transition-all hover:-translate-y-0.5 active:translate-x-[2px] active:translate-y-[2px] active:shadow-[1px_1px_0px_#33302a] disabled:opacity-60"
            style={{ borderRadius: "8px 16px 6px 14px / 14px 6px 13px 7px" }}
            onClick={signIn}
            disabled={busy}
          >
            <svg aria-hidden viewBox="0 0 16 16" width="18" height="18" fill="currentColor"><path d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27.68 0 1.36.09 2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.013 8.013 0 0016 8c0-4.42-3.58-8-8-8z"/></svg>
            {busy ? "One moment…" : "Continue with GitHub"}
          </button>
          {error && <p role="alert" className="mt-4 border-l-2 border-[var(--danger)] pl-3 font-label text-[13px] text-[var(--danger)]">{error}</p>}
        </div>
        <p className="mt-6 font-label text-[11px] text-[var(--pa-ink-muted)]">Signing in creates your Forkward account and a workspace for your API keys, repositories and billing.</p>
      </section>
    </MarketingShell>
  );
}
