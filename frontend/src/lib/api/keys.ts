export type ApiKey = {
  id: string;
  key_id: string;
  name: string;
  active: boolean;
  created_at: string;
  revoked_at: string | null;
  last_used_at: string | null;
};

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, { cache: "no-store", ...init });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(body.error ?? `Request failed (${res.status}).`);
  return body as T;
}

export const listKeys = () => call<{ keys: ApiKey[] }>("/api/keys").then((b) => b.keys);

export const createKey = (name: string) =>
  call<{ apiKey: string; key: ApiKey }>("/api/keys", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name }) });

export const revokeKey = (id: string) => call<{ ok: true }>(`/api/keys/${encodeURIComponent(id)}`, { method: "DELETE" });
