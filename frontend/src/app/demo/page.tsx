import Link from "next/link";
import { Icon } from "@/components/Icon";
import { currentViewer } from "@/lib/server/session";

export const metadata = { title: "Try Forkward" };

export default async function Demo() {
  const signedIn = Boolean(await currentViewer());
  return (
    <div className="wrap pb-6 pt-12 md:pt-16">
      <h1 className="t-h1 max-w-[20ch]">How do you want to start?</h1>
      <div className="mt-8 grid gap-4 md:grid-cols-2">
        <section className="card flex flex-col items-start p-6">
          <span className="grid h-10 w-10 place-items-center rounded-full bg-sunken text-rust"><Icon name="review" /></span>
          <h2 className="t-h2 mt-4">See a real sync</h2>
          <p className="mb-6 mt-2 max-w-[40ch] text-muted">Step through a recorded sync of a real fork, with five conflicts and the votes behind each one. No account needed.</p>
          <Link href="/demo/live" className="btn btn-accent mt-auto">Open the sample sync</Link>
        </section>
        <section className="card flex flex-col items-start p-6">
          <span className="grid h-10 w-10 place-items-center rounded-full bg-sunken text-rust"><Icon name="plug" /></span>
          <h2 className="t-h2 mt-4">{signedIn ? "Your repositories" : "Get started"}</h2>
          <p className="mb-6 mt-2 max-w-[40ch] text-muted">Connect your own fork and preview a sync before anything is pushed. Starter is free and includes 50 units.</p>
          <Link href={signedIn ? "/repos" : "/signin?next=/repos"} className="btn btn-accent mt-auto">{signedIn ? "Open your repositories" : "Sign in and connect a fork"}</Link>
        </section>
      </div>
    </div>
  );
}
