import { installUrl } from "@/lib/server/github";
import ReposClient from "./ReposClient";

export const dynamic = "force-dynamic";
export const metadata = { title: "Repositories | Forkward" };

export default function Repos() {
  return <ReposClient installUrl={installUrl()} />;
}
