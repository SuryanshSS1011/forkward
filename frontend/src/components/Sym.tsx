/** A Material Symbols glyph. The font is loaded in src/app/layout.tsx and styled in globals.css.
 * Used across the parchment marketing pages (landing, demo, pricing). */
export function Sym({ name, className = "" }: { name: string; className?: string }) {
  return (
    <span aria-hidden className={`material-symbols-outlined ${className}`}>
      {name}
    </span>
  );
}
