"use client";
import Link from "next/link";
import { authClient } from "@/lib/auth-client";

/** Signed out: Sign in and Get started. Signed in: the avatar and Sign out, which returns to the
 * public site with a full reload so the server renders the signed-out navigation. */
export function AuthButton({ user }: { user: { name: string; image: string | null } | null }) {
  if (!user)
    return (
      <>
        <Link href="/signin" className="btn btn-outline-teal btn-sm">Sign in</Link>
        <Link href="/signin?next=/repos" className="btn btn-outline btn-sm hidden sm:inline-flex">Get started</Link>
      </>
    );
  const signOut = async () => {
    // Deletes this session row, so the cookie stops working everywhere at once.
    await authClient.signOut().catch(() => null);
    window.location.assign("/");
  };
  return (
    <span className="inline-flex items-center gap-2">
      <Avatar name={user.name} src={user.image} />
      <button className="btn btn-outline-teal btn-sm" onClick={signOut}>Sign out</button>
    </span>
  );
}

/** The GitHub avatar, or the first letter of the account name when there is none. */
function Avatar({ name, src }: { name: string; src: string | null }) {
  const label = `Signed in as ${name}`;
  if (src)
    // eslint-disable-next-line @next/next/no-img-element
    return <img src={`${src}${src.includes("?") ? "&" : "?"}s=64`} alt={label} title={name} width={32} height={32} className="h-8 w-8 rounded-full border border-line" />;
  return (
    <span role="img" aria-label={label} title={name} className="inline-flex h-8 w-8 items-center justify-center rounded-full border border-line bg-sunken text-[14px] font-semibold uppercase text-ink">
      {name.slice(0, 1)}
    </span>
  );
}
