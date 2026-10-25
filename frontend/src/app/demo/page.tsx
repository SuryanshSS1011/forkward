import Link from "next/link";
import { JobView } from "@/components/job/JobView";
import { MarketingShell } from "@/components/MarketingShell";
import { SAMPLE_JOB } from "@/lib/demo/sample";
import { currentViewer } from "@/lib/server/session";

export const metadata = { title: "A recorded run | Forkward" };

const MODEL_NAMES: Record<string, string> = {
  "vultr:deepseek-v4.1-flash": "DeepSeek V4.1 Flash on Vultr",
};
const repoOf = (ref: string) => ref.split("@")[0];
const hunks = SAMPLE_JOB.hunks;
const models = Object.keys(SAMPLE_JOB.summary.models ?? {}).map(
  (m) => MODEL_NAMES[m] ?? m,
);
const passed = hunks.filter((h) => h.detail?.passed_checks).length;
const toPerson = hunks.filter((h) => h.decision === "review").length;
const linkClass = "font-bold text-[var(--pa-ink)] underline underline-offset-2";

export default async function Demo() {
  const signedIn = Boolean(await currentViewer());
  return (
    <MarketingShell active="demo" signedIn={signedIn}>
      {/* Same width as the job view below, so the two read as one page. */}
      <section className="wrap pt-10">
        <span className="font-label text-[11px] font-bold uppercase tracking-widest text-[var(--pa-primary)]">
          A recorded run
        </span>
        <h1 className="mt-1 max-w-[28ch] font-display text-[30px] font-extrabold tracking-tight text-[var(--pa-ink)] md:text-[40px]">
          One real sync, every decision on the record.
        </h1>
        <p className="mt-3 max-w-[72ch] text-[17px] leading-relaxed text-[var(--pa-ink-muted)]">
          A preview of{" "}
          <a
            className={linkClass}
            href={`https://github.com/${repoOf(SAMPLE_JOB.upstream_ref)}`}
          >
            {SAMPLE_JOB.upstream_ref}
          </a>{" "}
          merging into{" "}
          <a
            className={linkClass}
            href={`https://github.com/${SAMPLE_JOB.repo}`}
          >
            {SAMPLE_JOB.repo}
          </a>
          , resolved by {models.join(", ")}. All {toPerson} conflicts went to a
          person because the gate is not certified for this model yet. {passed}{" "}
          of {hunks.length} passed every other check.
        </p>
        <p className="mt-3 font-label text-[12px] text-[var(--pa-ink-muted)]">
          Run {SAMPLE_JOB.summary.run_id} · log in results/demo-run-tinyring
        </p>
        {!signedIn && (
          <Link
            href="/signin?next=/repos"
            className="mt-6 inline-flex border-2 border-[var(--pa-charcoal)] bg-[var(--pa-primary-container)] px-6 py-2 font-label text-[14px] font-bold text-[var(--pa-on-primary)] shadow-[4px_4px_0px_#33302a] transition-all hover:-translate-y-0.5 active:translate-x-[2px] active:translate-y-[2px] active:shadow-[1px_1px_0px_#33302a]"
            style={{ borderRadius: "8px 16px 6px 14px / 14px 6px 13px 7px" }}
          >
            Try it on your fork
          </Link>
        )}
      </section>
      <JobView
        job={SAMPLE_JOB}
        sample
        hideStages={["open_pr", "ci"]}
        note={
          <span className="flex items-baseline gap-2">
            <span className="kind-pill">Recorded run</span>
            <span>
              Pick a conflict to see both sides and every proposal.
            </span>
          </span>
        }
      />
    </MarketingShell>
  );
}
