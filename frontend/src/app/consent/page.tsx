"use client";
import { useEffect, useState } from "react";
import { authClient } from "@/lib/auth-client";

const SCOPE_TEXT: Record<string, string> = {
  openid: "Know who you are on Forkward",
  profile: "See your name and avatar",
  email: "See your email address",
  offline_access: "Stay connected until you revoke it",
};

/**
 * Better Auth sends an MCP client's authorization request here when it needs the signed-in user's
 * consent. The query is signed by the server, and the consent call sends it back with the decision,
 * so this page cannot widen what the client asked for.
 */
export default function Consent() {
  const [client, setClient] = useState<string | null>(null);
  const [scopes, setScopes] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const q = new URLSearchParams(window.location.search);
    const id = q.get("client_id");
    setScopes((q.get("scope") ?? "").split(" ").filter(Boolean));
    if (!id || !q.get("sig")) {
      setError("This authorization link is incomplete. Start again from your editor.");
      return;
    }
    setClient(id);
    authClient.oauth2
      .publicClient({ query: { client_id: id } })
      .then(({ data }) => {
        const name = (data as { client_name?: string } | null)?.client_name;
        if (name) setClient(name);
      })
      .catch(() => undefined);
  }, []);

  const decide = async (accept: boolean) => {
    setBusy(true);
    setError(null);
    const { data, error: e } = await authClient.oauth2.consent({ accept });
    const url = (data as { url?: string } | null)?.url;
    if (url) {
      window.location.assign(url);
      return;
    }
    setError(e?.message ?? "Forkward could not record your answer. Start again from your editor.");
    setBusy(false);
  };

  return (
    <div className="wrap max-w-md pb-16 pt-10">
      <h1 className="t-h1">Allow access to Forkward?</h1>
      {client && (
        <>
          <p className="mt-3 text-muted">
            <span className="font-semibold text-ink">{client}</span> wants to use Forkward as you. It can call the Forkward MCP tools, and its usage counts against your plan.
          </p>
          <ul className="mt-6 list-disc space-y-1 pl-5 text-muted">
            <li>Use the Forkward MCP server</li>
            {scopes.map((s) => (
              <li key={s}>{SCOPE_TEXT[s] ?? s}</li>
            ))}
          </ul>
          <div className="mt-8 flex gap-3">
            <button className="btn btn-primary" onClick={() => decide(true)} disabled={busy}>Allow</button>
            <button className="btn btn-secondary" onClick={() => decide(false)} disabled={busy}>Deny</button>
          </div>
        </>
      )}
      {error && <p role="alert" className="t-label mt-4 border-l-2 border-[var(--danger)] pl-3 text-[var(--danger)]">{error}</p>}
    </div>
  );
}
