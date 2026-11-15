"use client";

import { useEffect, useRef, useState, type ReactNode } from "react";

declare global {
  interface Window {
    THREE?: unknown;
    VANTA?: {
      CLOUDS2?: (options: Record<string, unknown>) => { destroy: () => void };
    };
  }
}

const THREE_SRC = "https://cdn.jsdelivr.net/npm/three@0.134.0/build/three.min.js";
const VANTA_SRC = "https://cdn.jsdelivr.net/npm/vanta@0.5.24/dist/vanta.clouds2.min.js";

function loadScript(src: string) {
  return new Promise<void>((resolve, reject) => {
    const existing = document.querySelector<HTMLScriptElement>(`script[src="${src}"]`);
    if (existing) {
      if (existing.dataset.loaded) resolve();
      else {
        existing.addEventListener("load", () => resolve());
        existing.addEventListener("error", () => reject(new Error(`Failed to load ${src}`)));
      }
      return;
    }
    const script = document.createElement("script");
    script.src = src;
    script.async = true;
    script.onload = () => {
      script.dataset.loaded = "1";
      resolve();
    };
    script.onerror = () => reject(new Error(`Failed to load ${src}`));
    document.head.appendChild(script);
  });
}

export default function VantaHero({ children }: { children: ReactNode }) {
  const rootRef = useRef<HTMLDivElement | null>(null);
  const bgRef = useRef<HTMLDivElement | null>(null);
  const effectRef = useRef<{ destroy: () => void } | undefined>(undefined);
  const [ready, setReady] = useState(false);

  useEffect(() => {
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;

    let cancelled = false;
    const panel = rootRef.current?.closest<HTMLElement>("[data-billboard]");

    const stop = () => {
      effectRef.current?.destroy();
      effectRef.current = undefined;
      setReady(false);
    };

    const start = async () => {
      if (cancelled || effectRef.current || !bgRef.current) return;
      try {
        await loadScript(THREE_SRC);
        await loadScript(VANTA_SRC);
        if (cancelled || !bgRef.current || !window.VANTA?.CLOUDS2) return;
        // Only boot WebGL while this panel is the active deck slide.
        if (panel && panel.dataset.active !== "true") return;
        effectRef.current = window.VANTA.CLOUDS2({
          el: bgRef.current,
          mouseControls: true,
          touchControls: false,
          gyroControls: false,
          minHeight: 200.0,
          minWidth: 200.0,
          scale: 1.0,
          texturePath: "/noise.png",
          backgroundColor: 0xffffff,
          skyColor: 0x7ec4ea,
          cloudColor: 0xe8f4fc,
          lightColor: 0xffe6b3,
          speed: 0.55,
        });
        setReady(true);
      } catch {
        // CDN unavailable — gradient fallback stays up.
      }
    };

    const sync = () => {
      const active = !panel || panel.dataset.active === "true";
      if (active) void start();
      else stop();
    };

    sync();

    const mo = panel
      ? new MutationObserver(sync)
      : null;
    if (panel && mo) mo.observe(panel, { attributes: true, attributeFilter: ["data-active"] });

    return () => {
      cancelled = true;
      mo?.disconnect();
      stop();
    };
  }, []);

  return (
    <div ref={rootRef} className="relative flex h-full min-h-[100dvh] flex-col overflow-hidden">
      <div
        aria-hidden
        className="absolute inset-0 bg-[radial-gradient(ellipse_at_50%_-20%,#bfe3f7_0%,#7ec4ea_50%,#a8d6f0_80%,#fdfbf7_100%)]"
      />
      <div ref={bgRef} aria-hidden className="ayo-vanta absolute inset-0" data-ready={ready} />
      <div
        aria-hidden
        className="pointer-events-none absolute inset-x-0 bottom-0 h-32 bg-gradient-to-b from-transparent to-[#fdfbf7]/80"
      />
      <div className="relative z-10 flex h-full min-h-[100dvh] flex-col">{children}</div>
    </div>
  );
}
