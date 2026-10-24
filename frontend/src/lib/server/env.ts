import { readFileSync } from "node:fs";
import { resolve } from "node:path";

/** Reads a variable from the environment, falling back to the repository's root .env in development. */
export function serverEnv(name: string): string | undefined {
  if (process.env[name]) return process.env[name];
  if (process.env.NODE_ENV === "production") return undefined;
  try {
    const line = readFileSync(resolve(process.cwd(), "..", ".env"), "utf8")
      .split("\n")
      .find((l) => l.startsWith(`${name}=`));
    const value = line?.slice(name.length + 1).trim().replace(/^["']|["']$/g, "");
    return value || undefined;
  } catch {
    return undefined;
  }
}

/** The public origin of the web app, such as https://forkward.example. */
export function appUrl(): string {
  return (serverEnv("BETTER_AUTH_URL") ?? "http://localhost:3000").replace(/\/+$/, "");
}
