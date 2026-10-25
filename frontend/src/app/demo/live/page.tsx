import { permanentRedirect } from "next/navigation";

/** The recorded run used to live here; /demo shows it now. */
export default function SampleRunMoved() {
  permanentRedirect("/demo");
}
