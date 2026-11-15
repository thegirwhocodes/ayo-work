"use client";

import { useEffect, useRef, useState } from "react";
import { AMBER, CORAL, GREEN, LILAC, ORANGE, SKY } from "./theme";

/* Ayo trust orb — clear PNG with soft glow; sizes via className. */
export function AyoOrb({
  className = "",
  size = "md",
  decorative = true,
}: {
  className?: string;
  size?: "sm" | "md" | "lg";
  /** When false, exposes an accessible name for logo use. */
  decorative?: boolean;
}) {
  const dim =
    size === "sm"
      ? "h-8 w-8 sm:h-9 sm:w-9"
      : size === "lg"
        ? "h-28 w-28 sm:h-36 sm:w-36"
        : "h-20 w-20 sm:h-24 sm:w-24";
  // 512 is enough at display sizes ≤144px; avoids shipping the 1.8MB clear asset in the hot path.
  const src = "/brand/ayo-trust-orb-512.png";
  const intrinsic = 512;

  return (
    <img
      src={src}
      alt={decorative ? "" : "Ayo"}
      width={intrinsic}
      height={intrinsic}
      decoding="async"
      draggable={false}
      className={`ayo-orb select-none ${dim} ${className}`}
      aria-hidden={decorative || undefined}
    />
  );
}

/** Hero / CTA floating orb — soft drop-in, then a gentle bob. */
export function Mascot({ className = "" }: { className?: string }) {
  return (
    <div className={`ayo-mascot ${className}`} aria-hidden>
      <div className="ayo-mascot-bob">
        <AyoOrb size="md" className="drop-shadow-[0_12px_28px_rgba(51,77,128,0.35)]" />
      </div>
    </div>
  );
}

export function Sticker({
  children,
  bg,
  tilt = -4,
  className = "",
}: {
  children: React.ReactNode;
  bg: string;
  tilt?: number;
  className?: string;
}) {
  return (
    <span
      className={`ayo-sticker ${className}`}
      style={{ backgroundColor: bg, color: "#fff", "--sticker-tilt": `${tilt}deg` } as React.CSSProperties}
    >
      {children}
    </span>
  );
}

export function NameCursor({
  name,
  color,
  drift,
  className = "",
}: {
  name: string;
  color: string;
  drift: "a" | "b" | "c";
  className?: string;
}) {
  return (
    <div className={`ayo-cursor ayo-cursor-${drift} ${className}`} aria-hidden>
      <svg width="22" height="22" viewBox="0 0 24 24">
        <path
          d="M5.5 3.2v17.6c0 .45.54.67.85.35l4.86-4.86a.5.5 0 0 1 .35-.15h6.87c.45 0 .67-.54.35-.85L6.35 2.85a.5.5 0 0 0-.85.35Z"
          fill={color}
          stroke="#fff"
          strokeWidth="1.5"
        />
      </svg>
      <span
        className="ml-4 inline-block rounded-full px-2.5 py-1 text-xs font-bold text-white shadow-md"
        style={{ backgroundColor: color }}
      >
        {name}
      </span>
    </div>
  );
}

type FloatSticker = {
  text: string;
  bg: string;
  tilt: number;
  /** Absolute position + visibility classes */
  at: string;
};

type FloatCursor = {
  name: string;
  color: string;
  drift: "a" | "b" | "c";
  at: string;
};

/** Decorative stickers — only mount while the parent deck panel is active. */
export function SectionFloats({
  stickers,
  cursors = [],
}: {
  stickers: readonly FloatSticker[];
  cursors?: readonly FloatCursor[];
}) {
  const rootRef = useRef<HTMLDivElement | null>(null);
  const [active, setActive] = useState(false);

  useEffect(() => {
    const panel = rootRef.current?.closest<HTMLElement>("[data-billboard]");
    if (!panel) {
      setActive(true);
      return;
    }
    const sync = () => setActive(panel.dataset.active === "true");
    sync();
    const mo = new MutationObserver(sync);
    mo.observe(panel, { attributes: true, attributeFilter: ["data-active"] });
    return () => mo.disconnect();
  }, []);

  return (
    <div ref={rootRef} className="pointer-events-none absolute inset-0 z-0 overflow-hidden" aria-hidden>
      {active
        ? cursors.map((c) => (
            <NameCursor
              key={`${c.name}-${c.at}`}
              name={c.name}
              color={c.color}
              drift={c.drift}
              className={`absolute ${c.at}`}
            />
          ))
        : null}
      {active
        ? stickers.map((s) => (
            <Sticker
              key={`${s.text}-${s.at}`}
              bg={s.bg}
              tilt={s.tilt}
              className={`absolute text-xs sm:text-sm ${s.at}`}
            >
              {s.text}
            </Sticker>
          ))
        : null}
    </div>
  );
}

/**
 * Sparse corner-only packs (lg+). Kept out of the content column so they
 * never sit under body copy like the old mid-page floats did.
 */
export const SECTION_FLOATS = {
  what: {
    stickers: [
      { text: "listens", bg: CORAL, tilt: -7, at: "left-4 top-8 hidden lg:inline-flex" },
      { text: "joy", bg: AMBER, tilt: 8, at: "right-4 bottom-10 hidden lg:inline-flex" },
    ],
    cursors: [
      { name: "Kemi", color: SKY, drift: "b" as const, at: "right-8 top-24 hidden xl:block" },
    ],
  },
  how: {
    stickers: [
      { text: "call", bg: CORAL, tilt: -6, at: "right-4 top-8 hidden lg:inline-flex" },
      { text: "a human", bg: GREEN, tilt: 6, at: "left-4 bottom-10 hidden lg:inline-flex" },
    ],
    cursors: [
      { name: "Sade", color: ORANGE, drift: "a" as const, at: "left-8 top-20 hidden xl:block" },
    ],
  },
  features: {
    stickers: [
      { text: "24/7", bg: AMBER, tilt: 7, at: "left-4 top-8 hidden lg:inline-flex" },
      { text: "to a human", bg: ORANGE, tilt: -8, at: "right-4 bottom-10 hidden lg:inline-flex" },
    ],
    cursors: [
      { name: "Amina", color: LILAC, drift: "c" as const, at: "right-8 top-28 hidden xl:block" },
    ],
  },
  safe: {
    stickers: [
      { text: "honest", bg: SKY, tilt: -4, at: "right-4 top-6 hidden lg:inline-flex" },
      { text: "careful", bg: GREEN, tilt: 5, at: "left-4 bottom-8 hidden lg:inline-flex" },
    ],
    cursors: [] as const,
  },
  problem: {
    // Dropped mid-copy floats ("not alone", "be heard") — they sat under the text.
    stickers: [
      { text: "unheard", bg: ORANGE, tilt: -6, at: "right-4 top-8 hidden lg:inline-flex" },
    ],
    cursors: [] as const,
  },
  why: {
    stickers: [
      { text: "Lagos", bg: LILAC, tilt: -5, at: "right-4 top-8 hidden lg:inline-flex" },
      { text: "home", bg: AMBER, tilt: 4, at: "left-4 bottom-10 hidden lg:inline-flex" },
    ],
    cursors: [
      { name: "Naomi", color: LILAC, drift: "b" as const, at: "right-8 bottom-28 hidden xl:block" },
    ],
  },
  talk: {
    stickers: [
      { text: "hello", bg: GREEN, tilt: -6, at: "left-4 top-10 hidden lg:inline-flex" },
      { text: "joy", bg: LILAC, tilt: 9, at: "right-4 top-16 hidden lg:inline-flex" },
    ],
    cursors: [] as const,
  },
} as const;

/** Jump the section deck to a panel id — zoom transition, not page scroll. */
export function scrollToId(id: string) {
  if (id === "top" || id === "hero") {
    window.dispatchEvent(new CustomEvent("ayo:goto", { detail: { id: "hero" } }));
    return;
  }
  window.dispatchEvent(new CustomEvent("ayo:goto", { detail: { id } }));
}
