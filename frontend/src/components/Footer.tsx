import Link from "next/link";
import { APP_FOOTER, PUBLIC_FOOTER } from "@/lib/nav";
import { currentViewer } from "@/lib/server/session";

export async function Footer() {
  const links = (await currentViewer()) ? APP_FOOTER : PUBLIC_FOOTER;
  return (
    <footer className="mt-10 bg-inverse text-on-inverse">
      <div className="wrap flex flex-wrap items-center justify-between gap-x-8 gap-y-2 py-5 text-[14px]">
        <p className="font-semibold">Forkward</p>
        <ul className="flex flex-wrap gap-x-6 gap-y-1">
          {links.map((l) => (
            <li key={l.href + l.label}><Link href={l.href} className="underline underline-offset-4">{l.label}</Link></li>
          ))}
        </ul>
      </div>
    </footer>
  );
}
