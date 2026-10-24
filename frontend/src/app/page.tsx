import Link from "next/link";
import { redirect } from "next/navigation";
import { ConnectsTo } from "@/components/ConnectsTo";
import { HeroTitle } from "@/components/HeroTitle";
import { Icon, type IconName } from "@/components/Icon";
import { LearningCurve } from "@/components/LearningCurve";
import { LiveMerge } from "@/components/LiveMerge";
import { sampleScenes } from "@/lib/demo/scenes";
import { currentViewer } from "@/lib/server/session";

const features: { icon: IconName; h: string; p: string }[] = [
  { icon: "loop", h: "Reuses what you approve", p: "A resolution you merge is reused when that change conflicts again." },
  { icon: "fan", h: "2.5x faster", p: "Agents resolve conflicts in parallel." },
  { icon: "clock", h: "Syncs on a schedule", p: "Hourly, daily or weekly, per repository." },
  { icon: "branch", h: "Never commits to main", p: "Every change is a PR you review." },
];

const steps: { icon: IconName; h: string; p: string }[] = [
  { icon: "plug", h: "Connect", p: "Install the GitHub app on the forks you want synced." },
  { icon: "fan", h: "Propose", p: "Seven rules and several AI agents resolve each conflict." },
  { icon: "shield", h: "Gate", p: "A hunk is applied on its own only under a certified error bound." },
  { icon: "review", h: "Review", p: "Everything else waits for you in the pull request, and what you merge informs the next sync." },
];

const why: { value: string; label: string; note: string }[] = [
  { value: "29 to 158", label: "Stable releases behind", note: "How far the vendor device kernels in our study trailed their upstream." },
  { value: "65", label: "Weekly syncs, zero conflicts", note: "Raspberry Pi merged 65 stable releases in a row into rpi-6.12.y without one conflict. Syncing often keeps each merge small." },
  { value: "300,000", label: "Verified hunks for five nines", note: "Certifying 1 wrong auto-merge in 100,000 at 95% confidence takes about that many. Every merged pull request adds to the record." },
];

const results: { value: string; label: string; note: string; tone?: "lead" | "warn" }[] = [
  { value: "76.1%", label: "Matched the engineers' resolution", note: "54 of 71 hunks on Rockchip, a vendor never used in tuning, tested once.", tone: "lead" },
  { value: "71.8%", label: "Always keeping the fork's side", note: "51 of 71. Forkward gets 3 more right, a gap not yet shown to be significant." },
  { value: "2.5x", label: "Faster with agents in parallel", note: "1.9 s a hunk against 4.7 s, measured on 8 hunks." },
  { value: "8 of 18", label: "Gate-accepted hunks that were wrong", note: "At the frozen threshold on Rockchip. Auto-apply stays off until the gate certifies its error bound.", tone: "warn" },
];

export default async function Landing() {
  // The landing page is for visitors; a signed-in person starts in their workspace.
  if (await currentViewer()) redirect("/repos");
  return (
    <>
      <section className="wrap grid grid-cols-1 items-center gap-8 pb-10 pt-6 md:pt-10 lg:grid-cols-[1fr_minmax(0,460px)] lg:gap-10">
        <div>
          <HeroTitle>Forkward</HeroTitle>
          <p className="mt-5 text-[clamp(20px,2.4vw,26px)] font-medium leading-[1.3] tracking-[-0.01em]">Every upstream fix, forwarded to your fork.</p>
          <p className="mt-3 max-w-[44ch] text-[17px] leading-[1.5] text-muted">
            Forkward merges upstream into your fork and proposes a resolution for every conflict, from deterministic rules and a team of AI agents. It applies one on its own only under a certified error bound, and the rest wait for you in a pull request.
          </p>
          <ul className="mt-7 grid max-w-[560px] gap-x-6 gap-y-5 sm:grid-cols-2">
            {features.map((f) => (
              <li key={f.h} className="grid grid-cols-[36px_1fr] items-start gap-3">
                <span className="grid h-9 w-9 place-items-center rounded-tile bg-raised text-rust shadow-card"><Icon name={f.icon} size={18} /></span>
                <span>
                  <span className="block text-[15px] font-semibold leading-[20px]">{f.h}</span>
                  <span className="mt-0.5 block text-[14px] leading-[20px] text-muted">{f.p}</span>
                </span>
              </li>
            ))}
          </ul>
          <div className="mt-7 flex flex-wrap gap-3">
            <Link href="/signin?next=/repos" className="btn btn-outline">Get started</Link>
            <Link href="/demo/live" className="btn btn-outline-teal">Watch it work</Link>
          </div>
        </div>
        <LiveMerge scenes={sampleScenes()} />
      </section>

      <ConnectsTo />

      <section className="wrap mb-10" aria-labelledby="why-title">
        <div className="border-t border-line pt-6">
          <p className="t-label text-muted">Why forks fall behind</p>
          <h2 id="why-title" className="t-h2 mt-1">A fix upstream is not yet a fix in your fork.</h2>
          <p className="mt-2 max-w-[62ch] text-muted">Every sync means conflicts, so long-lived forks put syncs off. The longer they wait, the more security and stability fixes they miss, and the bigger the next merge gets.</p>
          <ul className="mt-6 grid gap-3 sm:grid-cols-3">
            {why.map((w) => (
              <li key={w.label} className="card p-5">
                <p className="t-stat">{w.value}</p>
                <p className="mt-1 font-medium">{w.label}</p>
                <p className="mt-1 text-[14px] text-muted">{w.note}</p>
              </li>
            ))}
          </ul>
        </div>
      </section>

      <section id="how" className="wrap scroll-mt-24" aria-labelledby="how-title">
        <div className="border-t border-line pt-6">
          <h2 id="how-title" className="t-label text-muted">How it works</h2>
          <ol className="mt-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            {steps.map((s, i) => (
              <li key={s.h} className="card flex gap-3 p-4 transition-[transform,border-color] duration-200 hover:-translate-y-0.5 hover:border-rust sm:block">
                <div className="flex shrink-0 items-center justify-between">
                  <span className="grid h-9 w-9 place-items-center rounded-full bg-sunken text-rust"><Icon name={s.icon} /></span>
                  <span className="hidden font-mono text-[12px] text-faint sm:inline">0{i + 1}</span>
                </div>
                <div className="sm:mt-3">
                  <h3 className="text-[17px] font-semibold">{s.h}</h3>
                  <p className="mt-1 text-[14px] leading-[20px] text-muted">{s.p}</p>
                </div>
              </li>
            ))}
          </ol>
        </div>
      </section>

      <section className="wrap mt-10" aria-labelledby="search-title">
        <div className="border-t border-line pt-6">
          <p className="t-label text-muted">Strategy search</p>
          <h2 id="search-title" className="t-h2 mt-1">Each new strategy has to earn its place.</h2>
          <p className="mt-2 text-muted">A child replaces the incumbent only when it wins on validation by more than the noise.</p>
          <div className="mt-6"><LearningCurve /></div>
        </div>
      </section>

      <section className="wrap mt-10" aria-labelledby="results-title">
        <div className="border-t border-line pt-6">
          <p className="t-label text-muted">Results</p>
          <h2 id="results-title" className="t-h2 mt-1">Measured on real kernel forks.</h2>
          <p className="mt-2 text-muted">Scored line for line, ignoring whitespace, against what NXP, ST and Rockchip engineers committed, with DeepSeek V4.1 Flash as the resolver model.</p>
          <ul className="mt-6 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            {results.map((r) => (
              <li key={r.label} className={`card p-5 ${r.tone === "lead" ? "!border-rust" : r.tone === "warn" ? "!border-honey" : ""}`}>
                <p className="t-stat">{r.value}</p>
                <p className="mt-1 font-medium">{r.label}</p>
                <p className="mt-1 text-[14px] text-muted">{r.note}</p>
              </li>
            ))}
          </ul>
          <p className="mt-6"><Link href="/demo/live" className="link">Open the recorded sample run</Link></p>
        </div>
      </section>
    </>
  );
}
