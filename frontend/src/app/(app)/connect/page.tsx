import { installUrl } from "@/lib/server/github";
import ConnectClient from "./ConnectClient";

export const metadata = { title: "Connect | Forkward" };

// The install link comes from GITHUB_APP_SLUG at request time, not at build time.
export const dynamic = "force-dynamic";

export default function Connect() {
  return <ConnectClient installUrl={installUrl()} />;
}
