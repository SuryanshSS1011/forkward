"use client";
import { useEffect, useState } from "react";
import { GitHubMark } from "@/components/MarketingAuth";
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
      <MarketingShell auth={false}>
        <section className="mx-auto w-full max-w-7xl px-6 py-20 lg:px-12">
          <p className="text-[var(--pa-ink-muted)]" role="status">Taking you to your workspace…</p>
        </section>
      </MarketingShell>
    );

  return (
    <MarketingShell auth={false}>
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
            <GitHubMark size={18} />
            {busy ? "One moment…" : "Continue with GitHub"}
          </button>
          {error && <p role="alert" className="mt-4 border-l-2 border-[var(--danger)] pl-3 font-label text-[13px] text-[var(--danger)]">{error}</p>}
        </div>
        <p className="mt-6 font-label text-[11px] text-[var(--pa-ink-muted)]">Signing in creates your Forkward account and a workspace for your API keys, repositories and billing.</p>
      </section>
    </MarketingShell>
  );
}
