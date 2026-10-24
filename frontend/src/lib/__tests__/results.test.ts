import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";
import { KEEP_FORK, ROUNDS, SEARCH_RUN, parseDecision } from "../results";

const file = JSON.parse(readFileSync(resolve(__dirname, "../../../../results/learning-curve.json"), "utf8"));

describe("learning curve on the home page", () => {
  it("matches results/learning-curve.json", () => {
    expect(file.run).toBe(SEARCH_RUN);
    expect(ROUNDS).toHaveLength(file.rounds.length);
    file.rounds.forEach((r: Record<string, number | string | null>, i: number) => {
      const prev = i ? file.rounds[i - 1].incumbent : r.incumbent;
      expect(ROUNDS[i].round).toBe(r.round);
      expect(ROUNDS[i].train).toBeCloseTo(r.best_train_accuracy as number, 3);
      expect(ROUNDS[i].validation).toBeCloseTo(r.incumbent_validation_accuracy as number, 3);
      expect(ROUNDS[i].promoted).toBe(r.incumbent !== prev);
      expect(ROUNDS[i].decision).toBe(r.promotion);
    });
    expect(KEEP_FORK.train).toBeCloseTo(file.keep_vendor.train, 3);
    expect(KEEP_FORK.validation).toBeCloseTo(file.keep_vendor.validation, 3);
  });

  it("reads the promotion gate's comparison", () => {
    expect(parseDecision(ROUNDS[4].decision)).toEqual({ delta: 0.067, std: 0.058, lo: -0.044, hi: 0.178 });
    expect(parseDecision(null)).toBeNull();
  });
});
