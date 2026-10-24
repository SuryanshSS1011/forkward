import { redirect } from "next/navigation";
import { currentPath, currentViewer } from "@/lib/server/session";

// Everything in this group works on the signed-in workspace, so it is checked on the server
// before anything renders.
export const dynamic = "force-dynamic";

export default async function SignedIn({ children }: { children: React.ReactNode }) {
  if (!(await currentViewer())) redirect(`/signin?next=${encodeURIComponent(await currentPath())}`);
  return children;
}
