/**
 * The strategy search behind the home page chart: results/learning-curve.json, run search-2.
 * Train is 70 NXP hunks and validation is 45 ST hunks. A test checks these against the file.
 */
export type Round = {
  round: number;
  /** Best strategy in the population on train. */
  train: number;
  /** The incumbent strategy on validation, after this round's promotion decision. */
  validation: number;
  promoted: boolean;
  /** The promotion gate's comparison of the best child against the incumbent. */
  decision: string | null;
};

export const SEARCH_RUN = "search-2";
export const TRAIN_N = 70;
export const VALIDATION_N = 45;

export const ROUNDS: Round[] = [
  { round: 0, train: 0.5857, validation: 0.6889, promoted: false, decision: null },
  { round: 1, train: 0.8, validation: 0.6889, promoted: false, decision: "accuracy delta -0.044 vs bootstrap std 0.070, 95% CI [-0.178, +0.089]" },
  { round: 2, train: 0.8, validation: 0.6889, promoted: false, decision: "accuracy delta +0.000 vs bootstrap std 0.044, 95% CI [-0.089, +0.089]" },
  { round: 3, train: 0.8, validation: 0.6889, promoted: false, decision: "accuracy delta -0.022 vs bootstrap std 0.059, 95% CI [-0.133, +0.089]" },
  { round: 4, train: 0.8, validation: 0.7556, promoted: true, decision: "accuracy delta +0.067 vs bootstrap std 0.058, 95% CI [-0.044, +0.178]" },
];

/** Always keeping the fork's side, the baseline any strategy has to beat. */
export const KEEP_FORK = { train: 0.7429, validation: 0.5556 };

export type Decision = { delta: number; std: number; lo: number; hi: number };

export function parseDecision(s: string | null): Decision | null {
  const m = s?.match(/delta ([+-]?[\d.]+) vs bootstrap std ([\d.]+), 95% CI \[([+-]?[\d.]+), ([+-]?[\d.]+)\]/);
  return m ? { delta: +m[1], std: +m[2], lo: +m[3], hi: +m[4] } : null;
}
