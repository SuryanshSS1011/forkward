import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";
import { GITHUB_STATUS, githubStatus } from "../githubStatus";

describe("GitHub install statuses", () => {
  it("cover every status /api/github/installed can send", () => {
    const route = readFileSync(resolve(__dirname, "../../app/api/github/installed/route.ts"), "utf8");
    const sent = [...route.matchAll(/back\("([a-z-]+)"\)/g)].map((m) => m[1]);
    expect(sent.length).toBeGreaterThan(5);
    for (const s of sent) expect(GITHUB_STATUS, s).toHaveProperty(s);
  });

  it("read the status from the query string", () => {
    expect(githubStatus("?github=installed")?.ok).toBe(true);
    expect(githubStatus("?github=not-admin")?.ok).toBe(false);
    expect(githubStatus("?github=made-up")).toBeNull();
    expect(githubStatus("")).toBeNull();
  });
});
