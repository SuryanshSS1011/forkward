/**
 * The Forkward automaton: a sturdy little machine with a glass visor, LED eyes, an antenna beacon
 * and a chest core, working at a laptop. The laptop lid carries the merge mark: your fork's rail
 * in teal and upstream's in tangerine meet and leave as one rail once the merge is verified.
 *
 * States, each readable without motion and without color alone:
 *   idle      the eyes blink now and then and the beacon glows steadily
 *   working   grippers type, changes travel along the lid's rails, the chin lights run in turn
 *   waiting   brows up on the visor, a tangerine face and a speech bubble: a person decides
 *   verified  happy eyes, open smile, the lid's output rail lights, a check appears
 *   failed    worried brows, a red beacon, the lid's output rail breaks, a cross appears
 *
 * Drawn from plain shapes so it scales from the nav (as AgentGlyph) to the hero.
 */
export type AgentState = "idle" | "working" | "waiting" | "verified" | "failed";

const LABELS: Record<AgentState, string> = {
  idle: "idle",
  working: "resolving conflicts",
  waiting: "waiting for you",
  verified: "merge verified",
  failed: "stopped, needs a look",
};

const FORK = "M48 87 C54 87 54 92 59 92";
const UPSTREAM = "M48 97 C54 97 54 92 59 92";

export function AgentMark({ state = "idle", size = 120, title }: { state?: AgentState; size?: number; title?: string }) {
  return (
    <svg
      viewBox="0 0 120 120"
      width={size}
      height={size}
      role="img"
      aria-label={title ?? `The Forkward automaton, ${LABELS[state]}`}
      className={`am am-${state} shrink-0`}
    >
      {/* torso and chest core, behind the laptop */}
      <rect x="35" y="62" width="50" height="30" rx="11" className="am-body" />
      <path d="M41 66.5 H79" className="am-seam" />
      <circle cx="60" cy="71" r="3.4" className="am-core" />
      <rect x="53" y="56" width="14" height="8" rx="2" className="am-joint" />

      <g className="am-head">
        {/* antenna and beacon */}
        <rect x="58.5" y="8" width="3" height="10" rx="1.5" className="am-joint" />
        <circle cx="60" cy="7" r="4.4" className="am-beacon" />
        <path d="M58.2 5.6 Q59 4.4 60.4 4.3" className="am-glint" />

        {/* side receivers with tangerine bolts */}
        <rect x="26" y="30" width="9" height="18" rx="3.5" className="am-body" />
        <rect x="85" y="30" width="9" height="18" rx="3.5" className="am-body" />
        <circle cx="30.5" cy="39" r="2.3" className="am-bolt" />
        <circle cx="89.5" cy="39" r="2.3" className="am-bolt" />

        <rect x="33" y="16" width="54" height="43" rx="12" className="am-body" />
        <path d="M41 19.6 H79" className="am-sheen" />

        {/* visor: glass, with the face drawn in light */}
        <rect x="39" y="23" width="42" height="27" rx="8" className="am-screen" />
        <path d="M43 28 Q44 25.6 47.2 25.3" className="am-glint" />

        <g className="am-eyes">
          <g className="am-eye-open">
            <rect x="46.5" y="30.5" width="7" height="9.5" rx="3.5" className="am-led" />
            <rect x="66.5" y="30.5" width="7" height="9.5" rx="3.5" className="am-led" />
            <circle cx="48.6" cy="33" r="1.1" className="am-white" />
            <circle cx="68.6" cy="33" r="1.1" className="am-white" />
          </g>
        </g>
        <path d="M46 37.5 Q50 31.5 54 37.5 M66 37.5 Q70 31.5 74 37.5" className="am-led-line am-eye-happy" />
        <path d="M46 28.6 Q50 26.4 54 28.6 M66 28.6 Q70 26.4 74 28.6" className="am-led-line am-brow-up" />
        <path d="M46.5 29.6 L53.5 27.2 M73.5 29.6 L66.5 27.2" className="am-led-line am-brow-worry" />

        <path d="M54 44 Q60 48.4 66 44" className="am-led-line am-mouth-smile" />
        <path d="M53.5 43.4 Q60 50 66.5 43.4 Z" className="am-led am-mouth-open" />
        <path d="M54.5 46.6 Q60 43.2 65.5 46.6" className="am-led-line am-mouth-worry" />

        {/* chin status lights */}
        <g className="am-chin">
          <circle cx="53" cy="54.2" r="1.7" />
          <circle cx="60" cy="54.2" r="1.7" />
          <circle cx="67" cy="54.2" r="1.7" />
        </g>
      </g>

      {/* laptop, seen from behind, with the merge mark on the lid */}
      <rect x="31" y="76" width="58" height="32" rx="5" className="am-lid" />
      <path d={FORK} className="am-rail am-rail-fork" pathLength={100} />
      <path d={UPSTREAM} className="am-rail am-rail-up" pathLength={100} />
      <path d={FORK} className="am-packet" pathLength={100} />
      <path d={UPSTREAM} className="am-packet am-packet-up" pathLength={100} />
      <path d="M59 92 H71" className="am-out-wait" />
      <path d="M59 92 H71" className="am-out" pathLength={100} />
      <path d="M64 88.5 l5 7 M69 88.5 l-5 7" className="am-break" />
      <circle cx="59" cy="92" r="2.2" className="am-node" />
      <rect x="25" y="107" width="70" height="5" rx="2.5" className="am-base" />

      {/* grippers resting on the lid's top edge */}
      <g className="am-paw am-paw-l">
        <rect x="35.5" y="71.5" width="13" height="9" rx="3.2" className="am-body" />
        <path d="M38.8 76.4 H45.2" className="am-slit" />
      </g>
      <g className="am-paw am-paw-r">
        <rect x="71.5" y="71.5" width="13" height="9" rx="3.2" className="am-body" />
        <path d="M74.8 76.4 H81.2" className="am-slit" />
      </g>

      {/* outcome: a check, a cross, or a speech bubble while it waits for you */}
      <g className="am-badge">
        <path d="M92 23 L87.8 28.8 L95.4 25.8 Z" className="am-bubble-tail" />
        <circle cx="99" cy="17" r="10" className="am-badge-bg" />
        <path d="M94.5 17.2l3 3 6-6.4" className="am-badge-check" />
        <path d="M95.5 13.5l7 7M102.5 13.5l-7 7" className="am-badge-cross" />
        <g className="am-dots">
          <circle cx="94.5" cy="17" r="1.7" />
          <circle cx="99" cy="17" r="1.7" />
          <circle cx="103.5" cy="17" r="1.7" />
        </g>
      </g>
    </svg>
  );
}

/** The automaton's head for 14 to 32 pixels: the nav wordmark, the favicon and the proposer list. */
export function AgentGlyph({ size = 24, title }: { size?: number; title?: string }) {
  return (
    <svg viewBox="0 0 32 32" width={size} height={size} role={title ? "img" : undefined} aria-label={title} aria-hidden={title ? undefined : true} className="am ag shrink-0">
      <rect x="15.2" y="4" width="1.6" height="4.5" rx=".8" className="am-joint" />
      <circle cx="16" cy="3.6" r="2.5" className="ag-beacon" />
      <rect x="2.6" y="12.5" width="3.8" height="8.5" rx="1.5" className="am-body" />
      <rect x="25.6" y="12.5" width="3.8" height="8.5" rx="1.5" className="am-body" />
      <rect x="5" y="7.5" width="22" height="20.5" rx="6" className="am-body" />
      <rect x="8" y="11" width="16" height="11.8" rx="3.6" className="am-screen" />
      <rect x="11" y="13.6" width="3.4" height="4.8" rx="1.7" className="am-led" />
      <rect x="17.6" y="13.6" width="3.4" height="4.8" rx="1.7" className="am-led" />
      <path d="M13.6 20 Q16 21.6 18.4 20" className="am-led-line ag-smile" />
    </svg>
  );
}
