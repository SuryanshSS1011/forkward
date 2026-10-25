import { afterEach, describe, expect, it } from "vitest";
import { installUrl } from "../github";

afterEach(() => {
  delete process.env.GITHUB_APP_SLUG;
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
