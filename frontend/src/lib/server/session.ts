import { headers } from "next/headers";
import { cache } from "react";
import { viewer, type Viewer } from "./auth";

/** The signed-in viewer for this request, read once however many components ask. Null when signed
 * out, and also when the database cannot be reached, so public pages still render. */
export const currentViewer = cache(async (): Promise<Viewer | null> => {
  try {
    return await viewer(await headers());
  } catch {
    return null;
  }
});

/** The path and query of this request, set by src/proxy.ts, for sending people back after sign-in. */
export async function currentPath(): Promise<string> {
  return (await headers()).get("x-forkward-path") ?? "/";
}
