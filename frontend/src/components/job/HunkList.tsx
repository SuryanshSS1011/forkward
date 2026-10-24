import type { HunkRow } from "@/lib/jobs";
import { HUNK_STATE_LABEL, type HunkState, hunkState } from "./words";

const GROUPS: { key: string; title: string; states: HunkState[] }[] = [
  { key: "needs", title: "Needs you", states: ["needs"] },
  { key: "decided", title: "Decided by you", states: ["accepted", "rejected", "committed"] },
  { key: "auto", title: "Applied automatically", states: ["auto"] },
  { key: "error", title: "Could not be resolved", states: ["error"] },
];

export function HunkMark({ state }: { state: HunkState }) {
  return <span className={`hunk-mark hunk-${state}`} aria-hidden />;
}

/** Splits a path so the file name is always visible and the directory truncates first. */
function PathLabel({ file, n }: { file: string; n?: string }) {
  const cut = file.lastIndexOf("/");
  return (
    <span className="flex min-w-0 font-mono text-[13px] leading-5">
      <span className="truncate text-muted">{cut >= 0 ? file.slice(0, cut + 1) : ""}</span>
      <span className="shrink-0 text-ink">{file.slice(cut + 1)}</span>
      {n && <span className="shrink-0 text-faint">#{n}</span>}
    </span>
  );
}

export function HunkList({ hunks, selected, onSelect }: { hunks: HunkRow[]; selected: string | null; onSelect: (id: string) => void }) {
  return (
    <nav aria-label="Conflicts" className="space-y-5">
      {GROUPS.map((g) => {
        const rows = hunks.filter((h) => g.states.includes(hunkState(h)));
        if (!rows.length) return null;
        return (
          <section key={g.key} aria-labelledby={`grp-${g.key}`}>
            <h3 id={`grp-${g.key}`} className="mb-1 flex items-baseline justify-between text-[14px] font-semibold">
              {g.title}
              <span className="t-label tabular-nums text-faint">{rows.length}</span>
            </h3>
            <ul className="border-t border-line">
              {rows.map((h) => {
                const st = hunkState(h);
                const n = h.hunk_ref?.split("#").pop();
                return (
                  <li key={h.id} className="border-b border-line">
                    <button
                      type="button"
                      onClick={() => onSelect(h.id)}
                      aria-current={selected === h.id ? "true" : undefined}
                      className={`hunk-row grid w-full grid-cols-[14px_1fr_auto] items-center gap-3 px-2 py-2.5 text-left ${selected === h.id ? "hunk-row-on" : ""}`}
                    >
                      <HunkMark state={st} />
                      <PathLabel file={h.file} n={n} />
                      <span className="t-label tabular-nums text-faint" title="Confidence">{h.confidence != null ? h.confidence.toFixed(2) : ""}</span>
                      <span className="sr-only">{HUNK_STATE_LABEL[st]}</span>
                    </button>
                  </li>
                );
              })}
            </ul>
          </section>
        );
      })}
    </nav>
  );
}

export { PathLabel };
