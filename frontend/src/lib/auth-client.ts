"use client";
import { oauthProviderClient } from "@better-auth/oauth-provider/client";
import { createAuthClient } from "better-auth/react";

/** Better Auth in the browser, on this origin's /api/auth. The OAuth provider plugin forwards the
 * signed authorization request when an MCP client sends someone through sign-in and consent. */
export const authClient = createAuthClient({ plugins: [oauthProviderClient()] });
