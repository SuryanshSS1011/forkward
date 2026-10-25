/** What /api/github/installed reports back in ?github=<status>, worded for the page it lands on.
 * Every status that route can send has an entry here; the test checks the route against it. */
export const GITHUB_STATUS: Record<string, { text: string; ok: boolean }> = {
  installed: {
    text: "The Forkward GitHub App is installed. Its repositories are listed below.",
    ok: true,
  },
  requested: {
    text: "Your organization owner has to approve the Forkward GitHub App before it can be used.",
    ok: false,
  },
  invalid: {
    text: "GitHub sent an incomplete installation link. Install the app again.",
    ok: false,
  },
  "not-owner": {
    text: "Only the owner of this Forkward workspace can connect a GitHub installation.",
    ok: false,
  },
  "signin-github": {
    text: "Sign in with GitHub again, then reinstall the app.",
    ok: false,
  },
  "not-yours": {
    text: "That installation is not one your GitHub account can access, so nothing was recorded.",
    ok: false,
  },
  "not-admin": {
    text: "Only an admin of that GitHub organization, or the owner of that personal account, can connect it.",
    ok: false,
  },
  "github-failed": {
    text: "GitHub did not answer. Try again in a minute.",
    ok: false,
  },
  claimed: {
    text: "That installation is already connected to another Forkward workspace.",
    ok: false,
  },
};

export function githubStatus(search: string): { text: string; ok: boolean } | null {
  const s = new URLSearchParams(search).get("github");
  return s ? (GITHUB_STATUS[s] ?? null) : null;
}
