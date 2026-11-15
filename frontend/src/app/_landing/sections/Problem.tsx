"use client";

import type { ReactNode } from "react";
import { SECTION_FLOATS, SectionFloats, Sticker } from "../ornaments";
import { BODY, INK, ORANGE } from "../theme";
import { CountUp, Reveal } from "../ui";

const sourceClass =
  "w-fit touch-manipulation text-xs underline decoration-[#CBA868] underline-offset-4 transition-colors duration-200 hover:text-[#1B2A4A] focus-visible:rounded-sm focus-visible:text-[#1B2A4A] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#1B2A4A] focus-visible:ring-offset-2";

const STATS: {
  render: () => ReactNode;
  label: string;
  sourceName: string;
  sourceUrl: string;
}[] = [
  {
    render: () => (
      <>
        <span className="block text-[clamp(1.125rem,4.5vw,1.5rem)] font-bold leading-tight opacity-60">
          Up to
        </span>
        <span className="tabular-nums">1 billion</span>
      </>
    ),
    label:
      "children aged 2–17 experienced physical, sexual or emotional violence or neglect in the past year",
    sourceName: "WHO, Violence against children",
    sourceUrl: "https://www.who.int/news-room/fact-sheets/detail/violence-against-children",
  },
  {
    render: () => (
      <>
        <CountUp to={6} /> in 10
      </>
    ),
    label: "Nigerian children experience physical, sexual or emotional violence before age 18",
    sourceName: "UNICEF Nigeria, VACS 2014",
    sourceUrl: "https://www.unicef.org/nigeria/reports/ending-violence-against-children-nigeria",
  },
  {
    render: () => (
      <>
        <span className="block text-[clamp(1.125rem,4.5vw,1.5rem)] font-bold leading-tight opacity-60">
          Fewer than
        </span>
        <span className="tabular-nums">
          <CountUp to={5} />%
        </span>
      </>
    ),
    label: "of Nigerian children who experienced violence ever received help",
    sourceName: "UNICEF Nigeria, VACS 2014",
    sourceUrl: "https://www.unicef.org/nigeria/reports/ending-violence-against-children-nigeria",
  },
  {
    render: () => <CountUp to={500000} />,
    label:
      "contacts to child helplines worldwide went unanswered in 2024, usually because there weren’t enough staff",
    sourceName: "Child Helpline International, 2024",
    sourceUrl:
      "https://childhelplineinternational.org/wp-content/uploads/2025/11/Global-Voices-of-Children-and-Young-People-2024-Factsheet_Final.pdf",
  },
];

export default function Problem() {
  return (
    <div className="relative min-h-full">
      <SectionFloats {...SECTION_FLOATS.problem} />
      <div className="relative z-10 mx-auto max-w-6xl px-6 py-24 sm:py-32">
      <Reveal>
        <Sticker bg={ORANGE} tilt={-3} className="text-sm">
          the problem
        </Sticker>
        <h2 className="mt-6 max-w-3xl text-3xl font-extrabold tracking-tight sm:text-4xl" style={{ color: INK }}>
          Most children who are hurt never tell anyone.
        </h2>
        <p className="mt-6 max-w-2xl text-lg leading-relaxed" style={{ color: BODY }}>
          Many are hurt by someone at home, so speaking up isn’t safe. Helplines are overstretched.
          Many children have no smartphone, no data, and no trusted adult. A child should only need
          a phone call.
        </p>
      </Reveal>
      <div className="mt-16 grid gap-x-8 gap-y-14 sm:grid-cols-2 lg:grid-cols-4">
        {STATS.map((stat, i) => (
          <Reveal key={stat.label} delay={i * 80} className="min-w-0">
            <div className="flex min-w-0 flex-col gap-3">
              <p
                className="text-[clamp(1.75rem,8vw,3rem)] font-extrabold leading-[1.15] tracking-tight [overflow-wrap:anywhere] sm:leading-none"
                style={{ color: ORANGE }}
              >
                {stat.render()}
              </p>
              <p className="text-base leading-relaxed" style={{ color: BODY }}>
                {stat.label}
              </p>
              <a
                href={stat.sourceUrl}
                target="_blank"
                rel="noopener noreferrer"
                className={sourceClass}
                style={{ color: "#8A93A3" }}
              >
                {stat.sourceName}
              </a>
            </div>
          </Reveal>
        ))}
      </div>
      <Reveal delay={120}>
        <p className="mt-16 max-w-2xl text-base leading-relaxed" style={{ color: BODY, opacity: 0.8 }}>
          Children are usually hurt by someone they know — of child sexual assault victims reported
          to US police, 93% knew the perpetrator. That is why speaking out at home is dangerous, and
          why a line a child can reach on their own matters.{" "}
          <a
            href="https://rainn.org/2025/06/16/statistics-perpetrators-of-sexual-violence/"
            target="_blank"
            rel="noopener noreferrer"
            className={sourceClass}
          >
            US BJS, via RAINN
          </a>
        </p>
      </Reveal>
      </div>
    </div>
  );
}
