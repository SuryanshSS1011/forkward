"use client";
import { useEffect, useState } from "react";
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
  if (me && !oauthFlow) return <p className="wrap pb-16 pt-10 text-muted" role="status">Taking you to your workspace…</p>;

  return (
    <div className="wrap max-w-md pb-16 pt-10">
      <h1 className="t-h1">Sign in</h1>
      <p className="mt-2 text-muted">
        {oauthFlow ? "Sign in to let your editor use Forkward on your behalf." : "Forkward uses your GitHub account. Free to start, no card needed."}
      </p>
      <button className="btn btn-accent mt-8 w-full" onClick={signIn} disabled={busy}>
        <svg aria-hidden viewBox="0 0 16 16" width="18" height="18" fill="currentColor"><path d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27.68 0 1.36.09 2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.013 8.013 0 0016 8c0-4.42-3.58-8-8-8z"/></svg>
        {busy ? "One moment…" : "Continue with GitHub"}
      </button>
      {error && <p role="alert" className="t-label mt-4 border-l-2 border-[var(--danger)] pl-3 text-[var(--danger)]">{error}</p>}
      <p className="t-label mt-6 text-muted">Signing in creates your Forkward account and a workspace for your API keys, repositories and billing.</p>
    </div>
  );
}
