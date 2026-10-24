import { afterEach, describe, expect, it } from "vitest";
import { installUrl, previewSecret, safeNext } from "../github";

afterEach(() => {
  delete process.env.PREVIEW_SECRET;
  delete process.env.GITHUB_APP_SLUG;
});

describe("safeNext", () => {
  it("keeps same-site paths", () => expect(safeNext("/billing")).toBe("/billing"));
  it.each([null, "https://evil.example", "//evil.example", "/\\evil.example", "billing"])("rejects %s", (next) =>
    expect(safeNext(next)).toBe("/demo/live"),
  );
});

describe("installUrl", () => {
  it("links to the App's install page", () => {
    process.env.GITHUB_APP_SLUG = "forkward-bot";
    expect(installUrl()).toBe("https://github.com/apps/forkward-bot/installations/new");
  });
  it("refuses a slug that is not a plain name", () => {
    process.env.GITHUB_APP_SLUG = "x/../../evil";
    expect(installUrl()).toBeNull();
  });
});

describe("previewSecret", () => {
  it("matches the board's sha256 of forkward-preview:<seed>", async () => {
    const { createHash } = await import("node:crypto");
    process.env.PREVIEW_SECRET = "seed";
    expect(previewSecret()).toBe(createHash("sha256").update("forkward-preview:seed").digest("hex"));
  });
});
