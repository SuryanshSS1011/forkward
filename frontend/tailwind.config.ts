import type { Config } from "tailwindcss";

const v = (name: string) => `var(--${name})`;

export default {
  content: ["./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        surface: v("surface"), raised: v("surface-raised"), sunken: v("surface-sunken"),
        line: v("line"), ink: v("ink"), muted: v("ink-muted"), faint: v("ink-faint"),
        honey: v("honey"), "honey-hover": v("honey-hover"), sunshine: v("sunshine"),
        butter: v("butter"), coral: v("coral"), rust: v("rust"), inverse: v("surface-inverse"),
        "on-inverse": v("on-inverse"), success: v("success"), danger: v("danger"),
      },
      fontFamily: { sans: ["var(--font-sans)", "system-ui", "sans-serif"], mono: ["var(--font-mono)", "ui-monospace", "monospace"] },
      borderRadius: { btn: "8px", tile: "12px", card: "20px" },
      boxShadow: {
        card: "0 1px 2px rgba(15,42,46,.06), 0 4px 16px rgba(15,42,46,.06)",
        toast: "0 8px 24px rgba(15,42,46,.16)",
      },
      spacing: { 18: "4.5rem", 24: "6rem" },
    },
  },
  plugins: [],
} satisfies Config;
