"use client";

import { ArrowDown, ChevronDown, Siren } from "lucide-react";
import VantaHero from "../VantaHero";
import { AyoOrb, Mascot, NameCursor, Sticker, scrollToId } from "../ornaments";
import { AMBER, CORAL, DEEP, GREEN, INK, LILAC } from "../theme";

const focusRing =
  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-white focus-visible:ring-offset-2 focus-visible:ring-offset-[#5CA6CA]/40";

export default function Hero() {
  return (
    <VantaHero>
      {/* Slim emergency bar */}
      <div className="flex items-start justify-center gap-2 bg-[#1B2A4A]/90 px-4 py-2.5 text-center text-xs leading-snug text-white/90 backdrop-blur-sm sm:items-center sm:text-sm sm:leading-normal">
        <Siren className="mt-0.5 h-3.5 w-3.5 shrink-0 text-[#F5B642] sm:mt-0" aria-hidden />
        <p className="min-w-0">
          Ayo is in development. If you are a child in danger right now, call{" "}
          <strong className="font-semibold text-white">112</strong> in Nigeria or{" "}
          <strong className="font-semibold text-white">911</strong> in the US.
        </p>
      </div>

      {/* Nav */}
      <header className="flex items-center justify-between gap-3 px-5 py-4 sm:px-10 sm:py-5">
        <a
          href="#hero"
          onClick={(e) => {
            e.preventDefault();
            scrollToId("hero");
          }}
          className={`ayo-rise press inline-flex min-h-11 items-center gap-2 rounded-full text-lg font-extrabold tracking-tight text-white drop-shadow-sm ${focusRing}`}
          aria-label="Ayo home"
        >
          <AyoOrb size="sm" className="drop-shadow-[0_4px_12px_rgba(27,42,74,0.35)]" />
          <span aria-hidden="true">Ayo</span>
        </a>
        <button
          type="button"
          onClick={() => scrollToId("talk-to-ayo")}
          className={`ayo-rise press inline-flex min-h-11 shrink-0 touch-manipulation select-none items-center rounded-full bg-white/85 px-4 py-2 text-sm font-semibold shadow-sm backdrop-blur-sm transition-colors duration-200 hover:bg-white ${focusRing}`}
          style={{ "--rise-delay": "500ms", color: INK } as React.CSSProperties}
        >
          Talk to Ayo Now
        </button>
      </header>

      {/* Drifting friends */}
      <NameCursor name="Amara" color={CORAL} drift="a" className="left-[12%] top-[30%] hidden md:block" />
      <NameCursor name="Tunde" color={GREEN} drift="b" className="right-[14%] top-[26%] hidden md:block" />
      <NameCursor name="Zainab" color={LILAC} drift="c" className="left-[18%] bottom-[22%] hidden md:block" />
      <NameCursor name="Chidi" color={AMBER} drift="a" className="right-[18%] bottom-[28%] hidden lg:block" />

      {/* Sticker words */}
      <Sticker bg={CORAL} tilt={-8} className="ayo-rise absolute left-[8%] top-[46%] hidden text-sm lg:inline-flex">
        heard
      </Sticker>
      <Sticker bg={GREEN} tilt={6} className="ayo-rise absolute right-[8%] top-[52%] hidden text-sm lg:inline-flex">
        safe
      </Sticker>
      <Sticker bg={LILAC} tilt={-5} className="ayo-rise absolute right-[26%] top-[18%] hidden text-sm lg:inline-flex">
        a friend
      </Sticker>

      {/* Wordmark */}
      <div className="flex flex-1 flex-col items-center justify-center px-5 pb-28 text-center sm:px-6 sm:pb-24">
        <Mascot />
        <div className="flex flex-col items-center gap-1 sm:flex-row sm:items-end sm:gap-5">
          <h1
            className="ayo-rise text-[clamp(4.25rem,16vw,13rem)] font-extrabold leading-none tracking-tighter text-white"
            style={{ textShadow: "0 6px 40px rgba(51,77,128,0.45)" }}
          >
            Ayo
          </h1>
          {/* TODO: confirm exact pronunciation line with Naomi (§11.1) */}
          <p
            className="ayo-rise pb-1 text-sm font-bold sm:pb-6 sm:text-lg"
            style={
              {
                "--rise-delay": "120ms",
                color: "#FFF6DF",
                textShadow: "0 2px 16px rgba(51,77,128,0.5)",
              } as React.CSSProperties
            }
          >
            Ayọ̀ · ah-yaw · “joy” in Yoruba
          </p>
        </div>

        <p
          className="ayo-rise mt-4 text-xl font-bold text-white sm:text-2xl"
          style={{ "--rise-delay": "200ms", textShadow: "0 2px 20px rgba(51,77,128,0.5)" } as React.CSSProperties}
        >
          A child’s best friend.
        </p>

        <p
          className="ayo-rise mt-4 max-w-xl text-base font-medium leading-relaxed text-white sm:mt-5 sm:text-lg"
          style={{ "--rise-delay": "300ms", textShadow: "0 2px 16px rgba(51,77,128,0.55)" } as React.CSSProperties}
        >
          A free phone line any child can call, to talk, to be heard, and to get help from a real
          person when something is wrong.
        </p>

        <div
          className="ayo-rise mt-8 flex w-full max-w-md flex-col items-stretch gap-3 sm:mt-9 sm:max-w-none sm:flex-row sm:items-center sm:justify-center"
          style={{ "--rise-delay": "420ms" } as React.CSSProperties}
        >
          <button
            type="button"
            onClick={() => scrollToId("talk-to-ayo")}
            className={`press inline-flex min-h-12 touch-manipulation select-none items-center justify-center rounded-full px-7 py-3 text-base font-extrabold shadow-lg transition-colors duration-200 hover:bg-[#FFC95E] ${focusRing}`}
            style={{ backgroundColor: AMBER, color: INK }}
          >
            Talk to Ayo Now
          </button>
          <button
            type="button"
            onClick={() => scrollToId("what-is-ayo")}
            className={`press inline-flex min-h-12 touch-manipulation select-none items-center justify-center gap-2 rounded-full bg-white/90 px-7 py-3 text-base font-bold shadow-md backdrop-blur-sm transition-colors duration-200 hover:bg-white ${focusRing}`}
            style={{ color: DEEP }}
          >
            How Ayo works <ArrowDown className="h-4 w-4" aria-hidden />
          </button>
        </div>
      </div>

      {/* Scroll cue */}
      <button
        type="button"
        aria-label="Next section — What Ayo is"
        onClick={() => scrollToId("what-is-ayo")}
        className={`ayo-rise absolute bottom-6 left-1/2 z-10 flex min-h-11 min-w-11 -translate-x-1/2 touch-manipulation items-center justify-center ${focusRing}`}
        style={{ "--rise-delay": "700ms" } as React.CSSProperties}
      >
        <ChevronDown className="ayo-float h-7 w-7 text-white drop-shadow-md" aria-hidden />
      </button>
    </VantaHero>
  );
}
