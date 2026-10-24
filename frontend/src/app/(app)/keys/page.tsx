"use client";
import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { useMe } from "@/components/useMe";
import { type ApiKey, createKey, listKeys, revokeKey } from "@/lib/api/keys";

const date = (s: string | null) => (s ? new Date(s).toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" }) : "never");

export default function Keys() {
  const [me] = useMe();
  const [keys, setKeys] = useState<ApiKey[] | null>(null);
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [created, setCreated] = useState<{ apiKey: string; keyId: string } | null>(null);
  const [copied, setCopied] = useState(false);

  const load = useCallback(() => {
    listKeys()
      .then(setKeys)
      .catch((e) => setError(e instanceof Error ? e.message : "Could not load your keys."));
  }, []);

  useEffect(() => {
    if (me) load();
  }, [me, load]);

  const create = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    setCopied(false);
    try {
      const r = await createKey(name.trim() || "API key");
      setCreated({ apiKey: r.apiKey, keyId: r.key.key_id });
      setName("");
      load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not create a key.");
    } finally {
      setBusy(false);
    }
  };

  const revoke = async (k: ApiKey) => {
    if (!window.confirm(`Revoke ${k.key_id}? Anything using it stops working on its next call.`)) return;
    setError(null);
    try {
      await revokeKey(k.id);
      if (created?.keyId === k.key_id) setCreated(null);
      load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not revoke the key.");
    }
  };

  const copy = async () => {
    if (!created) return;
    try {
      await navigator.clipboard.writeText(created.apiKey);
      setCopied(true);
    } catch {
      setCopied(false);
    }
  };

  if (me === undefined) return <div className="wrap pb-16 pt-10" />;
  if (!me)
    return (
      <div className="wrap max-w-xl pb-16 pt-10">
        <h1 className="t-h1">API keys</h1>
        <p className="mt-3 text-muted">Sign in to create keys for the hosted MCP server.</p>
        <Link href="/signin?next=/keys" className="btn btn-primary mt-6">Sign in with GitHub</Link>
      </div>
    );

  return (
    <div className="wrap max-w-2xl pb-16 pt-10">
      <h1 className="t-h1">API keys</h1>
      <p className="mt-2 text-muted">A key lets an editor or script call the hosted MCP server as your workspace. Usage from every key counts against your plan.</p>

      {created && (
        <div className="mt-8 rounded-btn border border-line bg-raised p-4">
          <p className="font-semibold">Copy your new key now. It is shown once, and only its hash is stored.</p>
          <div className="mt-3 flex flex-wrap items-center gap-3">
            <code className="break-all font-mono text-[14px]" aria-label="Your new API key">{created.apiKey}</code>
            <button className="btn btn-secondary btn-sm" onClick={copy}>{copied ? "Copied" : "Copy"}</button>
          </div>
          <p className="t-label mt-3 text-muted">
            Next, <Link href="/connect" className="underline underline-offset-4">add Forkward to your editor</Link>.
          </p>
        </div>
      )}

      <form onSubmit={create} className="mt-8 flex flex-wrap items-end gap-3">
        <div className="min-w-[16rem] flex-1">
          <label htmlFor="keyname" className="t-label">Name</label>
          <input id="keyname" value={name} maxLength={100} placeholder="For example, laptop or CI" onChange={(e) => setName(e.target.value)}
            className="mt-1 w-full rounded-btn border border-line bg-raised px-3 py-2 focus:outline-none focus-visible:ring-2 focus-visible:ring-[var(--focus)]" />
        </div>
        <button type="submit" className="btn btn-primary" disabled={busy}>{busy ? "Creating…" : "Create key"}</button>
      </form>
      {error && <p role="alert" className="t-label mt-4 border-l-2 border-[var(--danger)] pl-3 text-[var(--danger)]">{error}</p>}

      <ul className="mt-8 divide-y divide-line border-y border-line">
        {keys?.length === 0 && <li className="py-4 text-muted">No keys yet.</li>}
        {keys?.map((k) => (
          <li key={k.id} className="flex flex-wrap items-center justify-between gap-3 py-4">
            <div>
              <p className="font-semibold">{k.name}</p>
              <p className="t-label text-muted">
                <span className="font-mono">{k.key_id}</span> · created {date(k.created_at)} · last used {date(k.last_used_at)}
                {!k.active && <> · revoked {date(k.revoked_at)}</>}
              </p>
            </div>
            {k.active ? (
              <button className="btn btn-secondary btn-sm" onClick={() => revoke(k)}>Revoke</button>
            ) : (
              <span className="chip">revoked</span>
            )}
          </li>
        ))}
      </ul>
    </div>
  );
}
