import Link from "next/link";
import { MarketingShell } from "@/components/MarketingShell";
import { currentViewer } from "@/lib/server/session";

export const metadata = { title: "How it works | Forkward" };

const sheet = "border-2 border-[var(--pa-outline-variant)] bg-[var(--pa-surface)] shadow-[3px_3px_0px_#ddc0bc]";
const edge = { borderRadius: "14px 6px 16px 7px / 7px 15px 6px 14px" };
const eyebrow = "font-label text-[11px] uppercase tracking-widest text-[var(--pa-primary)]";
const h2 = "mt-1 font-display text-[26px] font-bold leading-tight text-[var(--pa-ink)] md:text-[32px]";
const body = "mt-3 max-w-[64ch] text-[16px] leading-relaxed text-[var(--pa-ink-muted)]";

// The resolution algebra in forkward/algebra.py: every one runs without a model.
const RULES = [
  ["Keep your fork", "Your side stays as it is."],
  ["Take upstream", "Upstream's side replaces yours."],
  ["Fork, then upstream", "Both sides, yours first."],
  ["Upstream, then fork", "Both sides, upstream's first."],
  ["Upstream's change on your side", "Replays what upstream changed onto your lines."],
  ["Your change on upstream's side", "Replays what you changed onto upstream's lines."],
  ["Delete the block", "Neither side survives."],
];
// The resolver personas in forkward/agents.py.
const PERSONAS = [
  ["Fork-first", "Protects what your team changed."],
  ["Fix-first", "Makes sure upstream's fix lands."],
  ["Refactor-aware", "Follows code that moved or was renamed."],
];
const STEPS = [
  ["Merge on a new branch", "Forkward merges upstream into a fresh forkward/sync branch. Your default branch is never touched."],
  ["Propose", "Every conflict gets candidates from seven rules and three AI models, each written without seeing the others."],
  ["Vote", "Identical candidates pool their votes. A classifier says separately whether the fix should keep your side, take upstream's or keep both."],
  [
    "Gate",
    "A candidate applies on its own only if it wins the vote, a rule agrees with the models, the classifier agrees, and its confidence clears a bar measured for that model.",
  ],
  ["Pull request", "Everything else waits for you in a draft pull request, one note per conflict, and your own CI checks the build."],
  ["Learn", "What you merge is remembered. The same upstream change conflicting again reuses your fix."],
];
const SPLITS = [
  ["Train", "NXP lf-6.6.y", "70"],
  ["Validation", "ST v6.6-stm32mp", "45"],
  ["Test", "Rockchip develop-6.6", "71"],
];
const SAFETY = [
  ["New branches only", "Forkward pushes a new forkward/ branch and opens a pull request. It never pushes to your default branch or force-pushes."],
  ["Short-lived, scoped access", "The GitHub App sees only the forks you pick, and each token expires within an hour."],
  ["Your CI decides", "Builds run in your own CI, never on Forkward's servers."],
  ["A record anyone can check", "Each sync's decisions are hashed and written to Solana devnet, so a resolution changed later no longer matches."],
];
const STACK = [
  ["Vultr", "Runs the app and serves DeepSeek V4.1 Flash through Serverless Inference."],
  ["Tiger Data", "Postgres with row-level security per workspace. Run logs live in a TimescaleDB hypertable."],
  ["Auth0", "Signs in coding assistants that connect over MCP."],
  ["Solana", "Holds the tamper-evident record of every sync's decisions."],
  ["Gemini API", "An optional resolver model, chosen per run."],
  ["GoDaddy Registry", "mergesthat.work, on a .work domain."],
];

export default async function How() {
  const signedIn = Boolean(await currentViewer());
  return (
    <MarketingShell active="how" signedIn={signedIn}>
      <article className="mx-auto w-full max-w-5xl px-6 pb-20 pt-10 lg:px-12">
        <header>
          <span className={eyebrow}>How it works</span>
          <h1 className="mt-1 max-w-[20ch] font-display text-[34px] font-extrabold leading-[1.05] tracking-tight text-[var(--pa-ink)] md:text-[48px]">
            Many proposals, one careful gate.
          </h1>
          <p className={body}>
            A fix upstream is not a fix in your fork until it merges. Device kernels fall 29 to 158 stable releases behind because every merge hits conflicts
            someone has to resolve by hand. Forkward resolves them the way a careful team would: several independent proposals, an agreement check, and a person
            for anything uncertain.
          </p>
        </header>

        <section className="mt-14" aria-labelledby="flow">
          <span className={eyebrow}>One sync, in order</span>
          <h2 id="flow" className={h2}>
            From upstream to your pull request
          </h2>
          <ol className="mt-6 grid gap-3 md:grid-cols-2">
            {STEPS.map(([t, d], i) => (
              <li key={t} className={`${sheet} flex gap-4 p-5`} style={edge}>
                <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full border-2 border-[var(--pa-primary)] font-label text-[13px] text-[var(--pa-primary)]">
                  {i + 1}
                </span>
                <div>
                  <h3 className="font-display text-[18px] font-bold text-[var(--pa-ink)]">{t}</h3>
                  <p className="mt-1 text-[15px] leading-relaxed text-[var(--pa-ink-muted)]">{d}</p>
                </div>
              </li>
            ))}
          </ol>
        </section>

        <section className="mt-14 grid gap-8 md:grid-cols-[1.2fr_1fr]" aria-labelledby="proposers">
          <div>
            <span className={eyebrow}>Who proposes</span>
            <h2 id="proposers" className={h2}>
              Seven rules and three models
            </h2>
            <p className={body}>
              Rules are deterministic and need no model, so they cost nothing and cannot hallucinate. Models cover what rules cannot, like keeping both sides
              when code moved. A fix that a rule and the models reach independently is far more likely to be right.
            </p>
            <ul className="mt-5 grid gap-2 sm:grid-cols-2">
              {RULES.map(([t, d]) => (
                <li key={t} className="border-l-2 border-[var(--pa-tertiary)] pl-3">
                  <p className="font-label text-[14px] text-[var(--pa-ink)]">{t}</p>
                  <p className="text-[14px] text-[var(--pa-ink-muted)]">{d}</p>
                </li>
              ))}
            </ul>
          </div>
          <div className={`${sheet} self-start p-5`} style={edge}>
            <p className="font-label text-[12px] uppercase tracking-wider text-[var(--pa-ink-muted)]">The three models</p>
            <ul className="mt-3 space-y-3">
              {PERSONAS.map(([t, d]) => (
                <li key={t} className="flex items-start gap-3">
                  <span className="mt-1.5 h-2.5 w-2.5 shrink-0 rounded-full bg-[var(--pa-primary)]" aria-hidden />
                  <p className="text-[15px] text-[var(--pa-ink)]">
                    <span className="font-bold">{t}.</span> <span className="text-[var(--pa-ink-muted)]">{d}</span>
                  </p>
                </li>
              ))}
            </ul>
            <p className="mt-4 border-t border-dashed border-[var(--pa-outline-variant)] pt-3 text-[13px] text-[var(--pa-ink-muted)]">
              Each is DeepSeek V4.1 Flash with a different brief, so they disagree for real reasons.
            </p>
          </div>
        </section>

        <section className="mt-14" aria-labelledby="gate">
          <span className={eyebrow}>The safety gate</span>
          <h2 id="gate" className={h2}>
            It only acts on what it can prove
          </h2>
          <div className="mt-6 grid gap-4 md:grid-cols-3">
            <div className={`${sheet} p-5`} style={edge}>
              <p className="font-display text-[40px] font-extrabold leading-none text-[var(--pa-ink)]">3</p>
              <p className="mt-2 text-[15px] text-[var(--pa-ink-muted)]">
                checks every fix must pass: a clear vote, a rule that agrees, and a classifier that agrees.
              </p>
            </div>
            <div className={`${sheet} p-5`} style={edge}>
              <p className="font-display text-[40px] font-extrabold leading-none text-[var(--pa-ink)]">59</p>
              <p className="mt-2 text-[15px] text-[var(--pa-ink-muted)]">
                accepted fixes with zero mistakes are needed to certify at most 5% wrong at 95% confidence. Our validation set has 45, so today every fix waits
                for you.
              </p>
            </div>
            <div className={`${sheet} p-5`} style={edge}>
              <p className="font-display text-[40px] font-extrabold leading-none text-[var(--pa-ink)]">~300k</p>
              <p className="mt-2 text-[15px] text-[var(--pa-ink-muted)]">
                verified fixes would certify 1 wrong in 100,000. A shared service collects them: every team&rsquo;s merges calibrate the gate for everyone.
              </p>
            </div>
          </div>
          <p className={body}>
            The bar is set per model with a one-sided Clopper-Pearson bound on a held-out vendor, tested from the strictest threshold down. The guarantee is
            statistical and covers only what the gate accepts. Everything else goes to a person.
          </p>
        </section>

        <section className="mt-14 grid gap-8 md:grid-cols-[1fr_1fr]" aria-labelledby="evidence">
          <div>
            <span className={eyebrow}>The evidence</span>
            <h2 id="evidence" className={h2}>
              Graded against real kernel merges
            </h2>
            <p className={body}>
              The answer key is what each vendor&rsquo;s engineers actually committed, and a fix only counts if it matches exactly. The strategy is tuned on one
              vendor, calibrated on a second and tested once on a third.
            </p>
            <p className="mt-5 text-[16px] text-[var(--pa-ink)]">
              <span className="font-display text-[28px] font-extrabold">76%</span> of Rockchip test conflicts matched (54 of 71), with DeepSeek V4.1 Flash,
              measured before this event. Always keeping your fork&rsquo;s side scores 71.8%, so the gain is real but small.
            </p>
          </div>
          <table className={`${sheet} self-start text-left text-[15px]`} style={edge}>
            <thead className="font-label text-[12px] uppercase tracking-wider text-[var(--pa-ink-muted)]">
              <tr>
                <th className="px-4 pb-2 pt-4 font-semibold">Split</th>
                <th className="px-4 pb-2 pt-4 font-semibold">Vendor fork</th>
                <th className="px-4 pb-2 pt-4 text-right font-semibold">Conflicts</th>
              </tr>
            </thead>
            <tbody>
              {SPLITS.map(([s, v, n]) => (
                <tr key={s} className="border-t border-dashed border-[var(--pa-outline-variant)]">
                  <td className="px-4 py-2.5 font-bold text-[var(--pa-ink)]">{s}</td>
                  <td className="px-4 py-2.5 font-mono text-[13px] text-[var(--pa-ink)]">{v}</td>
                  <td className="px-4 py-2.5 text-right tabular-nums text-[var(--pa-ink)]">{n}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>

        <section className="mt-14" aria-labelledby="safety">
          <span className={eyebrow}>Your repository stays yours</span>
          <h2 id="safety" className={h2}>
            What Forkward will never do
          </h2>
          <ul className="mt-6 grid gap-3 md:grid-cols-2">
            {SAFETY.map(([t, d]) => (
              <li key={t} className={`${sheet} p-5`} style={edge}>
                <h3 className="font-display text-[18px] font-bold text-[var(--pa-ink)]">{t}</h3>
                <p className="mt-1 text-[15px] leading-relaxed text-[var(--pa-ink-muted)]">{d}</p>
              </li>
            ))}
          </ul>
        </section>

        <section className="mt-14" aria-labelledby="stack">
          <span className={eyebrow}>What it runs on</span>
          <h2 id="stack" className={h2}>
            Built this weekend on
          </h2>
          <dl className="mt-6 grid gap-x-8 gap-y-4 md:grid-cols-2">
            {STACK.map(([t, d]) => (
              <div key={t} className="border-l-2 border-[var(--pa-secondary)] pl-3">
                <dt className="font-label text-[15px] text-[var(--pa-ink)]">{t}</dt>
                <dd className="text-[15px] text-[var(--pa-ink-muted)]">{d}</dd>
              </div>
            ))}
          </dl>
        </section>

        <section className={`${sheet} mt-14 flex flex-wrap items-center justify-between gap-4 p-6`} style={edge}>
          <p className="max-w-[48ch] font-display text-[20px] font-bold text-[var(--pa-ink)]">See every decision of a real run, conflict by conflict.</p>
          <Link
            href="/demo"
            className="inline-flex border-2 border-[var(--pa-charcoal)] bg-[var(--pa-primary-container)] px-6 py-2.5 font-label text-[14px] text-[var(--pa-on-primary)] shadow-[4px_4px_0px_#33302a] transition-all hover:-translate-y-0.5"
            style={{ borderRadius: "8px 16px 6px 14px / 14px 6px 13px 7px" }}
          >
            See a real run
          </Link>
        </section>
      </article>
    </MarketingShell>
  );
}
