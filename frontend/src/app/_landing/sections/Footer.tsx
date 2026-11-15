"use client";

import { AyoOrb } from "../ornaments";
import { INK } from "../theme";

const telClass =
  "inline-flex min-h-11 items-center touch-manipulation rounded-sm font-extrabold tabular-nums tracking-tight text-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-white focus-visible:ring-offset-2 focus-visible:ring-offset-[#1B2A4A]";

const HELP = [
  {
    region: "Nigeria",
    lines: [
      { n: "112", href: "tel:112", note: "Emergency", size: "text-3xl sm:text-4xl" },
      {
        n: "0800 800 8001",
        href: "tel:08008008001",
        note: "Cece Yara child helpline · free, 24/7",
        size: "text-xl sm:text-2xl",
      },
    ],
  },
  {
    region: "US",
    lines: [
      { n: "911", href: "tel:911", note: "Emergency", size: "text-3xl sm:text-4xl" },
      {
        n: "1-800-422-4453",
        href: "tel:+18004224453",
        note: "Childhelp · call or text",
        size: "text-xl sm:text-2xl",
      },
      { n: "988", href: "tel:988", note: "Suicide & Crisis Lifeline", size: "text-xl sm:text-2xl" },
    ],
  },
];

export default function Footer() {
  return (
    <footer
      className="relative px-6 pt-10"
      style={{
        backgroundColor: INK,
        paddingBottom: "max(4rem, calc(2.5rem + env(safe-area-inset-bottom, 0px)))",
      }}
    >
      <div className="mx-auto flex max-w-6xl flex-col gap-12">
        <div className="flex flex-col gap-10 lg:flex-row lg:items-start lg:justify-between">
          <div className="min-w-0 max-w-3xl">
            <h2 className="text-lg font-bold text-white sm:text-xl">If you need help now</h2>
            <div className="mt-6 grid gap-8 sm:grid-cols-2 sm:gap-10">
              {HELP.map((place) => (
                <div key={place.region} className="min-w-0">
                  <p className="text-sm font-bold text-white">{place.region}</p>
                  <ul className="mt-3 flex flex-col gap-4">
                    {place.lines.map((line) => (
                      <li key={line.n} className="flex flex-col">
                        <a href={line.href} className={`${telClass} ${line.size}`}>
                          {line.n}
                        </a>
                        <p className="text-sm leading-relaxed text-white/70">{line.note}</p>
                      </li>
                    ))}
                  </ul>
                </div>
              ))}
            </div>
          </div>
          <a
            href="https://github.com/thegirwhocodes/safe-line"
            target="_blank"
            rel="noopener noreferrer"
            className="press inline-flex min-h-11 w-fit shrink-0 items-center touch-manipulation rounded-sm text-sm text-white/70 underline decoration-white/30 underline-offset-4 transition-colors duration-200 hover:text-white hover:decoration-white focus-visible:text-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-white focus-visible:ring-offset-2 focus-visible:ring-offset-[#1B2A4A]"
          >
            GitHub →
          </a>
        </div>
        <div className="flex flex-col gap-4 border-t border-white/15 pt-8 sm:flex-row sm:items-center sm:justify-between">
          <div className="flex items-center gap-3">
            <AyoOrb size="sm" className="opacity-90" />
            <div className="flex flex-col gap-1 text-sm text-white/55">
              <p>Ayo is an initiative of Education for Equality.</p>
              <p>Prototype. Not a substitute for emergency services.</p>
            </div>
          </div>
        </div>
      </div>
    </footer>
  );
}
