/**
 * Three hunks from the recorded sample run, cut down to what the home page replays: both sides of
 * the conflict, which agent voted for which resolution, the gate's checks and its decision. Every
 * value comes from SAMPLE_JOB, so the hero shows what Forkward actually did on that merge.
 */
import { LABELS, candidateTitle, dedent, sourceName } from "@/components/job/words";
import type { Candidate } from "@/lib/jobs";
import { SAMPLE_JOB } from "./sample";

export type SceneOption = { title: string; rules: string[]; votes: number; chosen: boolean };
export type Scene = {
  ref: string;
  /** A two or three word name for the outcome, for the hunk picker. */
  tag: string;
  fork: string[];
  upstream: string[];
  options: SceneOption[];
  /** The model agents in the order they report, each with the option it voted for. */
  agents: { name: string; option: number }[];
  checks: { text: string; ok: boolean }[];
  confidence: number;
  applied: boolean;
  reason: string;
};

/** One where the agents split, one where the classifier disagreed, and one applied, so the replay
 * ends on a merge. */
export const SCENE_REFS = [
  "crates/pytorch/utils/src/translate/ops.rs#1",
  "src/frontend/movement.rs#2",
  "crates/pytorch/utils/src/translate/attention.rs#1",
];
const AGENTS = ["vendor_first", "upstream_fix_first", "refactor_aware"];
const lower = (s: string) => s.charAt(0).toLowerCase() + s.slice(1);
const isRule = (s: string) => !s.startsWith("model:");

export function sampleScenes(): Scene[] {
  return SCENE_REFS.flatMap((ref) => {
    const h = SAMPLE_JOB.hunks.find((x) => x.hunk_ref === ref);
    const d = h?.detail;
    const cands = d?.candidates ?? [];
    const chosen = cands.find((c) => c.chosen);
    if (!h || !d || !chosen) return [];
    // The chosen resolution, then the strongest alternative.
    const rest = cands.filter((c) => c !== chosen).sort((a, b) => b.votes - a.votes);
    const shown: Candidate[] = [chosen, ...rest.slice(0, 1)];
    const proposers = d.proposers ?? AGENTS.length;
    const rule = chosen.sources.find(isRule);
    const expected = d.classifier ?? chosen.label;
    const split = chosen.votes !== proposers;
    const tag = h.decision === "auto" ? "Applied" : split ? "Split vote" : expected !== chosen.label ? "Classifier veto" : rule ? "Low confidence" : "No rule agreed";
    return [
      {
        ref,
        tag,
        fork: dedent(d.vendor ?? []).slice(0, 3),
        upstream: dedent(d.upstream ?? []).slice(0, 3),
        options: shown.map((c) => ({
          title: candidateTitle(c),
          rules: c.sources.filter(isRule).map(sourceName),
          votes: c.votes,
          chosen: c.chosen,
        })),
        agents: AGENTS.flatMap((a) => {
          const option = shown.findIndex((c) => c.sources.includes(`model:${a}`));
          return option < 0 ? [] : [{ name: sourceName(`model:${a}`), option }];
        }),
        checks: [
          {
            ok: chosen.votes === proposers,
            text: split ? `Agents split ${chosen.votes} to ${proposers - chosen.votes}` : `All ${proposers} agents agree`,
          },
          {
            ok: Boolean(rule),
            text: rule ? `A rule agrees (${lower(sourceName(rule)).replace(/ rule$/, "")})` : "No rule produced it",
          },
          {
            ok: expected === chosen.label,
            text: expected === chosen.label ? "Classifier agrees" : `Classifier expected ${lower(LABELS[expected] ?? expected)}`,
          },
        ],
        confidence: h.confidence ?? 0,
        applied: h.decision === "auto",
        reason: h.reason ? h.reason.replace(/\.?$/, ".") : "",
      },
    ];
  });
}
