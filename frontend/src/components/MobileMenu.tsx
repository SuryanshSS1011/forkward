"use client";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useRef } from "react";

/** The main links on phones, where the inline nav is hidden. Closes after every navigation. */
export function MobileMenu({ links }: { links: { href: string; label: string }[] }) {
  const ref = useRef<HTMLDetailsElement>(null);
  const path = usePathname();
  useEffect(() => {
    if (ref.current) ref.current.open = false;
  }, [path]);
  return (
    <details ref={ref} className="mobile-menu relative md:hidden">
      <summary className="btn btn-secondary btn-sm list-none">Menu</summary>
      <ul className="absolute right-0 top-full z-50 mt-2 w-48 rounded-tile border border-line bg-raised p-2 shadow-card">
        {links.map((l) => (
          <li key={l.href}>
            <Link href={l.href} aria-current={path === l.href ? "page" : undefined} className="block rounded-btn px-3 py-2 text-[15px] hover:bg-sunken">{l.label}</Link>
          </li>
        ))}
      </ul>
    </details>
  );
}
