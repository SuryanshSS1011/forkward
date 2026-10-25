"use client";
import Link from "next/link";
import { useMe } from "./useMe";

const button =
  "inline-flex items-center gap-2 whitespace-nowrap border-2 border-[var(--pa-charcoal)] bg-[var(--pa-primary-container)] px-3 py-1.5 sm:px-4 font-label text-[14px] font-bold text-[var(--pa-on-primary)] shadow-[3px_3px_0px_#33302a] transition-all active:translate-x-[2px] active:translate-y-[2px] active:shadow-[1px_1px_0px_#33302a]";
const shape = { borderRadius: "9px 4px 17px 5px / 4px 12px 5px 9px" };

/** The header's one action: sign in with GitHub, or open the app once signed in. While the
 * session loads it uses the server's answer when it has one, and otherwise keeps the space empty
 * so the wrong button never flashes. */
export function MarketingAuth({ signedIn }: { signedIn?: boolean }) {
  const [me] = useMe();
  const known = me === undefined ? signedIn : Boolean(me);
  if (known === undefined)
    return <span className="inline-block h-9 w-28" aria-hidden />;
  return known ? (
    <Link href="/repos" className={button} style={shape}>
      Open app
    </Link>
  ) : (
    <Link href="/signin" className={button} style={shape}>
      <GitHubMark />
      Sign in
    </Link>
  );
}

export function GitHubMark({ size = 16 }: { size?: number }) {
  return (
    <svg
      aria-hidden
      viewBox="0 0 16 16"
      width={size}
      height={size}
      fill="currentColor"
    >
      <path d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27.68 0 1.36.09 2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.013 8.013 0 0016 8c0-4.42-3.58-8-8-8z" />
    </svg>
  );
}
