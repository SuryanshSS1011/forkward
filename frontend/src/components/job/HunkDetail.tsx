"use client";
import { useEffect, useState } from "react";
import type { Candidate, HunkRow } from "@/lib/jobs";
import { PathLabel } from "./HunkList";
import { candidateTitle, dedent, HUNK_STATE_LABEL, hunkState, sourceName, statusLine } from "./words";

function Code({ title, lines, empty }: { title: string; lines: string[]; empty: string }) {
  return (
    <figure className="min-w-0">
      <figcaption className="mb-1 flex items-baseline justify-between text-[14px] font-semibold">
        {title}
        <span className="t-label text-faint">{lines.length === 1 ? "1 line" : `${lines.length} lines`}</span>
      </figcaption>
      {lines.length ? (
        <pre className="code-pane"><code>{dedent(lines).join("\n")}</code></pre>
      ) : (
        <p className="code-pane code-empty">{empty}</p>
      )}
    </figure>
  );
}

function Votes({ c, proposers }: { c: Candidate; proposers: number }) {
  const models = c.sources.filter((s) => s.startsWith("model:"));
  const rules = c.sources.filter((s) => !s.startsWith("model:"));
  return (
    <div className="flex flex-wrap items-center gap-x-4 gap-y-1 t-label text-muted">
      <span className="inline-flex items-center gap-1.5" aria-label={`${models.length} of ${proposers} models proposed this`}>
        {Array.from({ length: proposers }, (_, i) => (
          <span key={i} className={`vote-dot ${i < models.length ? "vote-on" : ""}`} aria-hidden />
        ))}
        <span>{models.length} of {proposers} models</span>
      </span>
      {rules.length > 0 && <span>{rules.map(sourceName).filter((v, i, a) => a.indexOf(v) === i).join(", ")}</span>}
    </div>
  );
}

export type ReviewFn = (h: HunkRow, decision: "accepted" | "rejected" | null, resolution?: string) => Promise<void>;

export function HunkDetail({ hunk, onReview, readOnly }: { hunk: HunkRow; onReview?: ReviewFn; readOnly?: boolean }) {
  const d = hunk.detail ?? {};
  const st = hunkState(hunk);
  const proposed = hunk.final_resolution ?? hunk.resolution ?? "";
  const [text, setText] = useState(proposed);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    setText(proposed);
    setError(null);
  }, [hunk.id, proposed]);

  const reviewable = !readOnly && onReview && (st === "needs" || st === "accepted" || st === "rejected");
  const edited = text !== (hunk.resolution ?? "");
  const act = async (decision: "accepted" | "rejected" | null) => {
    if (!onReview) return;
    setBusy(true);
    setError(null);
    try {
      await onReview(hunk, decision, decision === "accepted" && edited ? text : undefined);
    } catch (e) {
      setError(e instanceof Error ? e.message : "That did not save. Try again.");
    } finally {
      setBusy(false);
    }
  };
  const n = hunk.hunk_ref?.split("#").pop();
  const candidates = d.candidates ?? [];

  return (
    <article aria-labelledby="hunk-title" className="min-w-0">
      <header className="mb-5">
        <h2 id="hunk-title" className="min-w-0 text-[18px] font-semibold"><PathLabel file={hunk.file} n={n} /></h2>
        <p className={`mt-1 max-w-[70ch] text-[14px] hunk-status-${st}`}>{statusLine(HUNK_STATE_LABEL[st], hunk.reason, st === "needs" || st === "error")}</p>
      </header>

      <div className="grid gap-4 lg:grid-cols-2">
        <Code title="Your fork" lines={d.vendor ?? []} empty="Your fork deleted these lines." />
        <Code title="Upstream" lines={d.upstream ?? []} empty="Upstream deleted these lines." />
      </div>

      <section aria-labelledby="res-title" className="resolution mt-6">
        <div className="flex flex-wrap items-baseline justify-between gap-2">
          <h3 id="res-title" className="text-[16px] font-semibold">{st === "auto" ? "Applied resolution" : "Proposed resolution"}</h3>
          {edited && reviewable && <span className="t-label text-muted">Edited by you</span>}
        </div>
        {reviewable && st !== "rejected" ? (
          <textarea
            aria-label="Resolution"
            className="code-pane code-edit mt-2"
            spellCheck={false}
            value={text}
            rows={Math.min(18, Math.max(3, text.split("\n").length + 1))}
            onChange={(e) => setText(e.target.value)}
          />
        ) : (
          <pre className="code-pane mt-2"><code>{proposed || "(empty: the block is deleted)"}</code></pre>
        )}
        {reviewable && (
          <div className="mt-3 flex flex-wrap items-center gap-2">
            {st !== "accepted" && (
              <button className="btn btn-accent btn-sm" disabled={busy} onClick={() => act("accepted")}>{edited ? "Accept with my edit" : "Accept"}</button>
            )}
            {st === "accepted" && edited && (
              <button className="btn btn-accent btn-sm" disabled={busy} onClick={() => act("accepted")}>Save my edit</button>
            )}
            {st !== "rejected" && (
              <button className="btn btn-secondary btn-sm" disabled={busy} onClick={() => act("rejected")}>I&rsquo;ll fix this myself</button>
            )}
            {(st === "accepted" || st === "rejected") && (
              <button className="btn btn-secondary btn-sm" disabled={busy} onClick={() => act(null)}>Undo</button>
            )}
            {busy && <span className="spinner text-muted" aria-label="Saving" />}
          </div>
        )}
        {error && <p role="alert" className="mt-2 t-label text-danger">{error}</p>}
      </section>

      {candidates.length > 0 && (
        <section aria-labelledby="cand-title" className="mt-8">
          <h3 id="cand-title" className="text-[16px] font-semibold">What each resolver proposed</h3>
          <p className="mt-1 text-[14px] text-muted">Dots are AI agent votes. Rules, which need no model, are named on the right.</p>
          <ul className="mt-3 divide-y divide-line border-y border-line">
            {candidates.map((c, i) => (
              <li key={i} className="py-3">
                <details>
                  <summary className="cand-summary flex cursor-pointer list-none flex-wrap items-baseline justify-between gap-2">
                    <span className="font-medium">
                      {candidateTitle(c)}
                      {c.chosen && <span className="ml-2 t-label text-[var(--rust)]">Proposed</span>}
                    </span>
                    <Votes c={c} proposers={d.proposers ?? 3} />
                  </summary>
                  <pre className="code-pane mt-2"><code>{dedent(c.lines).join("\n") || "(empty: the block is deleted)"}</code></pre>
                  {reviewable && st !== "rejected" && c.lines.join("\n") !== text && (
                    <button className="btn btn-secondary btn-sm mt-2" onClick={() => setText(c.lines.join("\n"))}>Use this one</button>
                  )}
                </details>
              </li>
            ))}
          </ul>
        </section>
      )}
    </article>
  );
}
