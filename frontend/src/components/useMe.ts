"use client";
import { useEffect, useState } from "react";

export type Me = { id: string; name: string; email: string; image: string | null; tenantId: string; role: "owner" | "member"; plan: string } | null;

// Every component on a page shares one request, so a page load asks once, not once per component.
let pending: Promise<Me> | null = null;
function fetchMe(fresh = false): Promise<Me> {
  if (!pending || fresh) {
    pending = fetch("/api/me", { cache: "no-store" })
      .then((r) => (r.ok ? r.json() : { user: null }))
      .then((b) => (b.user ?? null) as Me)
      .catch(() => null);
    const mine = pending;
    // Forget the answer shortly after, so a later navigation sees a fresh session.
    setTimeout(() => {
      if (pending === mine) pending = null;
    }, 2000);
  }
  return pending;
}

/** The signed-in user, null when signed out, undefined while loading. Refreshes on the "forkward-auth" event. */
export function useMe(): [Me | undefined, (me: Me) => void] {
  const [me, setMe] = useState<Me | undefined>(undefined);

  useEffect(() => {
    let live = true;
    const load = (fresh = false) => fetchMe(fresh).then((m) => live && setMe(m));
    load();
    const onAuth = () => load(true);
    window.addEventListener("forkward-auth", onAuth);
    return () => {
      live = false;
      window.removeEventListener("forkward-auth", onAuth);
    };
  }, []);

  return [me, setMe];
}
