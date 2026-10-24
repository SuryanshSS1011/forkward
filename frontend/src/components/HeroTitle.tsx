"use client";
import { useEffect, useRef } from "react";

// Hides the hero title once it scrolls up to the header, and flags <html> so the header logo can take over.
export function HeroTitle({ children }: { children: React.ReactNode }) {
  const ref = useRef<HTMLHeadingElement>(null);
  useEffect(() => {
    const root = document.documentElement;
    const update = () => {
      const h = ref.current;
      const bar = document.querySelector("header");
      if (!h || !bar) return;
      if (h.getBoundingClientRect().bottom <= bar.getBoundingClientRect().bottom) root.setAttribute("data-hero-docked", "");
      else root.removeAttribute("data-hero-docked");
    };
    update();
    window.addEventListener("scroll", update, { passive: true });
    window.addEventListener("resize", update);
    return () => {
      window.removeEventListener("scroll", update);
      window.removeEventListener("resize", update);
      root.removeAttribute("data-hero-docked");
    };
  }, []);
  return (
    <h1 ref={ref} className="hero-title text-[clamp(56px,10vw,96px)] font-semibold leading-[0.95] tracking-[-0.04em]">
      {children}
    </h1>
  );
}
