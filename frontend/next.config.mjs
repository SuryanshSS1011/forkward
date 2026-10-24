// The web app reads Postgres through its own route handlers (src/app/api) and never proxies to the
// Python services, so there are no rewrites.
export default {
  reactStrictMode: true,
  // Next.js writes an AGENTS.md into the project on `next dev`; it is not part of the app.
  agentRules: false,
};
