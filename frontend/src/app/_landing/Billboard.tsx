"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";

/**
 * Full-viewport section deck — no document scroll.
 * Wheel / swipe / keys: current section zooms toward you and dissolves;
 * the next section fades in underneath. Like driving through a billboard.
 */

type GoOpts = { instant?: boolean };

type DeckApi = {
  goTo: (index: number, opts?: GoOpts) => void;
  goToId: (id: string, opts?: GoOpts) => void;
  index: number;
  count: number;
};

const DeckContext = createContext<DeckApi | null>(null);

export function useDeck() {
  return useContext(DeckContext);
}

export function goToSection(id: string, opts?: GoOpts) {
  window.dispatchEvent(new CustomEvent("ayo:goto", { detail: { id, ...opts } }));
}

const EASE = "cubic-bezier(0.23, 1, 0.32, 1)";
const OUT_MS = 720;
const IN_MS = 560;

type Panel = {
  id?: string;
  className?: string;
  rounded?: boolean;
  label: string;
  node: ReactNode;
};

export function SectionDeck({ panels }: { panels: Panel[] }) {
  const [index, setIndex] = useState(0);
  const [busy, setBusy] = useState(false);
  const indexRef = useRef(0);
  const busyRef = useRef(false);
  const reduceRef = useRef(false);
  const panelRefs = useRef<(HTMLElement | null)[]>([]);
  const touchY = useRef<number | null>(null);

  useEffect(() => {
    indexRef.current = index;
  }, [index]);

  useEffect(() => {
    busyRef.current = busy;
  }, [busy]);

  useEffect(() => {
    const mq = window.matchMedia("(prefers-reduced-motion: reduce)");
    reduceRef.current = mq.matches;
    const onChange = () => {
      reduceRef.current = mq.matches;
    };
    mq.addEventListener("change", onChange);
    // Lock the document — this deck owns navigation.
    const prevHtml = document.documentElement.style.overflow;
    const prevBody = document.body.style.overflow;
    document.documentElement.style.overflow = "hidden";
    document.body.style.overflow = "hidden";
    return () => {
      mq.removeEventListener("change", onChange);
      document.documentElement.style.overflow = prevHtml;
      document.body.style.overflow = prevBody;
    };
  }, []);

  const applyIdleState = useCallback((active: number) => {
    panelRefs.current.forEach((el, i) => {
      if (!el) return;
      const inner = el.querySelector<HTMLElement>("[data-billboard-inner]");
      const on = i === active;
      el.dataset.active = on ? "true" : "false";
      el.style.zIndex = on ? "2" : "1";
      el.style.pointerEvents = on ? "auto" : "none";
      el.setAttribute("aria-hidden", on ? "false" : "true");
      if (on) el.removeAttribute("inert");
      else el.setAttribute("inert", "");
      if (inner) {
        inner.style.transition = "none";
        inner.style.transform = on ? "scale(1)" : "scale(1)";
        inner.style.opacity = on ? "1" : "0";
        inner.style.filter = "none";
      }
    });
  }, []);

  useEffect(() => {
    applyIdleState(0);
  }, [applyIdleState, panels.length]);

  const animateTo = useCallback(
    (next: number, opts?: GoOpts) => {
      if (next === indexRef.current) return;
      if (next < 0 || next >= panels.length) return;
      if (busyRef.current && !opts?.instant) return;

      const from = indexRef.current;
      const outgoing = panelRefs.current[from];
      const incoming = panelRefs.current[next];
      if (!outgoing || !incoming) return;

      const outInner = outgoing.querySelector<HTMLElement>("[data-billboard-inner]");
      const inInner = incoming.querySelector<HTMLElement>("[data-billboard-inner]");
      if (!outInner || !inInner) return;

      const forward = next > from;
      const reduce = reduceRef.current || opts?.instant;

      busyRef.current = true;
      setBusy(true);

      // Stack: incoming under outgoing while we zoom through.
      outgoing.style.zIndex = "3";
      incoming.style.zIndex = "2";
      incoming.style.pointerEvents = "none";
      outgoing.style.pointerEvents = "none";
      incoming.dataset.active = "true";
      incoming.setAttribute("aria-hidden", "false");
      incoming.removeAttribute("inert");

      if (reduce) {
        outInner.style.transition = "opacity 220ms ease";
        inInner.style.transition = "opacity 220ms ease";
        outInner.style.opacity = "0";
        inInner.style.opacity = "1";
        inInner.style.transform = "scale(1)";
        outInner.style.transform = "scale(1)";
        window.setTimeout(() => {
          applyIdleState(next);
          indexRef.current = next;
          setIndex(next);
          busyRef.current = false;
          setBusy(false);
        }, 230);
        return;
      }

      // Prepare incoming: invisible, slightly large (we'll settle into it).
      inInner.style.transition = "none";
      inInner.style.opacity = "0";
      inInner.style.transform = "scale(1.08)";
      inInner.style.filter = "none";
      void inInner.offsetWidth;

      // Outgoing zooms toward the viewer and dissolves.
      const zoom = forward ? 1.55 : 0.72;
      // Scale + opacity only — CSS blur during transitions is a major GPU cost.
      outInner.style.transition = `transform ${OUT_MS}ms ${EASE}, opacity ${OUT_MS}ms ${EASE}`;
      outInner.style.transform = `scale(${zoom})`;
      outInner.style.opacity = "0";
      outInner.style.filter = "none";

      // Incoming fades in as we pass through the billboard.
      window.setTimeout(() => {
        inInner.style.transition = `transform ${IN_MS}ms ${EASE}, opacity ${IN_MS}ms ${EASE}`;
        inInner.style.opacity = "1";
        inInner.style.transform = "scale(1)";
      }, forward ? 180 : 80);

      window.setTimeout(() => {
        applyIdleState(next);
        indexRef.current = next;
        setIndex(next);
        busyRef.current = false;
        setBusy(false);
      }, OUT_MS + 40);
    },
    [applyIdleState, panels.length],
  );

  const goTo = useCallback(
    (i: number, opts?: GoOpts) => animateTo(i, opts),
    [animateTo],
  );

  const goToId = useCallback(
    (id: string, opts?: GoOpts) => {
      const i = panels.findIndex((p) => p.id === id);
      if (i >= 0) animateTo(i, opts);
    },
    [animateTo, panels],
  );

  useEffect(() => {
    const onGoto = (e: Event) => {
      const detail = (e as CustomEvent<{ id?: string; index?: number; instant?: boolean }>).detail;
      if (typeof detail?.index === "number") goTo(detail.index, { instant: detail.instant });
      else if (detail?.id) goToId(detail.id, { instant: detail.instant });
    };
    window.addEventListener("ayo:goto", onGoto);
    return () => window.removeEventListener("ayo:goto", onGoto);
  }, [goTo, goToId]);

  const step = useCallback(
    (dir: 1 | -1) => {
      animateTo(indexRef.current + dir);
    },
    [animateTo],
  );

  // Wheel → section change (never document scroll).
  useEffect(() => {
    let wheelLock = 0;
    const onWheel = (e: WheelEvent) => {
      e.preventDefault();
      if (busyRef.current) return;
      const now = performance.now();
      if (now - wheelLock < 900) return;

      const active = panelRefs.current[indexRef.current];
      const scroller = active?.querySelector<HTMLElement>("[data-billboard-scroll]");
      if (scroller && scroller.scrollHeight > scroller.clientHeight + 4) {
        const atTop = scroller.scrollTop <= 1;
        const atBottom = scroller.scrollTop + scroller.clientHeight >= scroller.scrollHeight - 1;
        if ((e.deltaY < 0 && !atTop) || (e.deltaY > 0 && !atBottom)) {
          scroller.scrollTop += e.deltaY;
          return;
        }
      }

      if (Math.abs(e.deltaY) < 8) return;
      wheelLock = now;
      step(e.deltaY > 0 ? 1 : -1);
    };
    window.addEventListener("wheel", onWheel, { passive: false });
    return () => window.removeEventListener("wheel", onWheel);
  }, [step]);

  // Touch swipe.
  useEffect(() => {
    const onStart = (e: TouchEvent) => {
      touchY.current = e.touches[0]?.clientY ?? null;
    };
    const onEnd = (e: TouchEvent) => {
      if (touchY.current == null || busyRef.current) return;
      const y = e.changedTouches[0]?.clientY;
      if (y == null) return;
      const dy = touchY.current - y;
      touchY.current = null;
      if (Math.abs(dy) < 56) return;

      const active = panelRefs.current[indexRef.current];
      const scroller = active?.querySelector<HTMLElement>("[data-billboard-scroll]");
      if (scroller && scroller.scrollHeight > scroller.clientHeight + 4) {
        const atTop = scroller.scrollTop <= 1;
        const atBottom = scroller.scrollTop + scroller.clientHeight >= scroller.scrollHeight - 1;
        if ((dy < 0 && !atTop) || (dy > 0 && !atBottom)) return;
      }
      step(dy > 0 ? 1 : -1);
    };
    window.addEventListener("touchstart", onStart, { passive: true });
    window.addEventListener("touchend", onEnd, { passive: true });
    return () => {
      window.removeEventListener("touchstart", onStart);
      window.removeEventListener("touchend", onEnd);
    };
  }, [step]);

  // Keyboard.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.defaultPrevented) return;
      const tag = (e.target as HTMLElement | null)?.tagName;
      if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return;
      if (["ArrowDown", "PageDown", " ", "Spacebar"].includes(e.key)) {
        e.preventDefault();
        step(1);
      } else if (["ArrowUp", "PageUp"].includes(e.key)) {
        e.preventDefault();
        step(-1);
      } else if (e.key === "Home") {
        e.preventDefault();
        goTo(0);
      } else if (e.key === "End") {
        e.preventDefault();
        goTo(panels.length - 1);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [goTo, panels.length, step]);

  const api: DeckApi = { goTo, goToId, index, count: panels.length };

  return (
    <DeckContext.Provider value={api}>
      <div
        className="ayo-deck fixed inset-0 overflow-hidden"
        style={{ height: "100dvh", backgroundColor: "#FDFBF7" }}
        role="region"
        aria-roledescription="carousel"
        aria-label="Ayo story"
      >
        {panels.map((panel, i) => (
          <section
            key={panel.id ?? panel.label}
            id={panel.id}
            ref={(el) => {
              panelRefs.current[i] = el;
            }}
            data-billboard
            data-active={i === 0 ? "true" : "false"}
            aria-label={panel.label}
            className={`ayo-panel absolute inset-0 overflow-hidden ${panel.className ?? ""}`}
            style={{ zIndex: i === 0 ? 2 : 1, pointerEvents: i === 0 ? "auto" : "none" }}
          >
            <div
              data-billboard-inner
              className="flex h-full min-h-0 origin-center flex-col"
              style={{ opacity: i === 0 ? 1 : 0 }}
            >
              <div
                data-billboard-scroll
                className="min-h-0 flex-1 overflow-y-auto overscroll-y-contain"
              >
                {panel.node}
              </div>
            </div>
          </section>
        ))}

        {/* Progress dots */}
        <nav
          className="pointer-events-auto absolute right-3 top-1/2 z-20 hidden -translate-y-1/2 flex-col gap-2 sm:right-5 sm:flex"
          aria-label="Sections"
        >
          {panels.map((panel, i) => (
            <button
              key={panel.label}
              type="button"
              aria-label={panel.label}
              aria-current={i === index ? "true" : undefined}
              disabled={busy}
              onClick={() => goTo(i)}
              className="h-2.5 w-2.5 rounded-full border border-white/80 shadow-sm transition-[transform,background-color] duration-200 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#5CA6CA] focus-visible:ring-offset-2"
              style={{
                backgroundColor: i === index ? "#1B2A4A" : "rgba(255,255,255,0.55)",
                transform: i === index ? "scale(1.25)" : "scale(1)",
              }}
            />
          ))}
        </nav>

        <p className="sr-only" aria-live="polite">
          {panels[index]?.label}
        </p>
      </div>
    </DeckContext.Provider>
  );
}

/** @deprecated — deck owns panels now; kept so old imports fail loudly if misused */
export function Billboard(_props: {
  children: ReactNode;
  className?: string;
  id?: string;
  rounded?: boolean;
}) {
  throw new Error("Use SectionDeck panels instead of <Billboard>");
}

export function BillboardManager() {
  return null;
}
