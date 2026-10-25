import type { Metadata } from "next";
import { Epilogue, Instrument_Sans, JetBrains_Mono, Karla } from "next/font/google";
import { Nav } from "@/components/Nav";
import { SiteChrome } from "@/components/SiteChrome";
import "./globals.css";

const sans = Instrument_Sans({ subsets: ["latin"], variable: "--font-sans", display: "swap" });
const mono = JetBrains_Mono({ subsets: ["latin"], variable: "--font-mono", display: "swap" });
// The marketing pages (scoped under .parchment) are set in these two faces.
const display = Epilogue({ subsets: ["latin"], variable: "--font-display", display: "swap" });
const bodyAlt = Karla({ subsets: ["latin"], variable: "--font-body-alt", display: "swap" });

export const metadata: Metadata = {
  title: "Forkward",
  description: "Forkward forwards every upstream fix to your fork. It resolves merge conflicts with deterministic rules and a team of AI agents, and opens a pull request for you to approve.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={`${sans.variable} ${mono.variable} ${display.variable} ${bodyAlt.variable}`}>
      <head>
        {/* Material Symbols power the small glyphs on the marketing pages. display=block keeps the
            ligature names ("history", "bolt") invisible until the font arrives. */}
        <link rel="preconnect" href="https://fonts.googleapis.com" />
        <link rel="preconnect" href="https://fonts.gstatic.com" crossOrigin="anonymous" />
        <link
          rel="stylesheet"
          href="https://fonts.googleapis.com/css2?family=Material+Symbols+Outlined:opsz,wght,FILL,GRAD@20..48,100..700,0..1,-50..200&display=block"
        />
      </head>
      <body>
        <a href="#main" className="sr-only focus:not-sr-only focus:fixed focus:left-4 focus:top-4 focus:z-50 focus:rounded-btn focus:bg-raised focus:px-3 focus:py-2">Skip to content</a>
        <SiteChrome><Nav /></SiteChrome>
        <main id="main">{children}</main>
      </body>
    </html>
  );
}
