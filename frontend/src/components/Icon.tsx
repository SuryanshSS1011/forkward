/** A small set of line icons, drawn at 24 units on currentColor. */
const PATHS: Record<string, string> = {
  branch: "M6 4v16 M6 14c0-4 3-5 6-5h2 M17 9a2.5 2.5 0 1 0 0-.01",
  person: "M12 12a4 4 0 1 0 0-8 4 4 0 0 0 0 8z M4.5 20c1.4-3.6 4.2-5.5 7.5-5.5s6.1 1.9 7.5 5.5",
  loop: "M20 12a8 8 0 0 1-13.9 5.4 M4 12a8 8 0 0 1 13.9-5.4 M18 3v4h-4 M6 21v-4h4",
  plug: "M9 3v5 M15 3v5 M6.5 8h11v3a5.5 5.5 0 0 1-11 0z M12 16.5V21",
  fan: "M4 12h4 M8 12l11-6.5 M8 12h11 M8 12l11 6.5 M16.5 4l2.5 1.5-1.5 2.5 M17 10l2 2-2 2 M17.5 16.5l1.5 2.5-2.5 1.5",
  shield: "M12 3l7.5 3v5.5c0 4.5-3.2 8-7.5 9.5-4.3-1.5-7.5-5-7.5-9.5V6z M8.8 12l2.2 2.2 4.2-4.4",
  clock: "M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18z M12 7v5l3.5 2",
  review: "M6 4a2 2 0 1 0 0 .01 M6 6v12 M6 20a2 2 0 1 0 0 .01 M18 20a2 2 0 1 0 0 .01 M18 18v-7a4 4 0 0 0-4-4h-4 M12 4.5L9.5 7l2.5 2.5",
};

export type IconName = keyof typeof PATHS;

export function Icon({ name, size = 20, className = "" }: { name: IconName; size?: number; className?: string }) {
  return (
    <svg viewBox="0 0 24 24" width={size} height={size} aria-hidden className={`shrink-0 ${className}`} fill="none" stroke="currentColor" strokeWidth={1.9} strokeLinecap="round" strokeLinejoin="round">
      <path d={PATHS[name]} />
    </svg>
  );
}
