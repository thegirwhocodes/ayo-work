"use client";

import { GraduationCap, Phone, Sparkles } from "lucide-react";
import { SECTION_FLOATS, SectionFloats, Sticker } from "../ornaments";
import { BODY, INK, LILAC } from "../theme";
import { Reveal } from "../ui";

const POINTS = [
  {
    icon: GraduationCap,
    body: (
      <>
        <strong style={{ color: INK }}>Education for Equality (E4E)</strong> is a US 501(c)(3)
        nonprofit, registered with Nigeria’s CAC. Founder Naomi Ivie (Wesleyan University,
        Economics &amp; CS) grew up in Lagos.
      </>
    ),
  },
  {
    icon: Phone,
    body: (
      <>
        <strong style={{ color: INK }}>Sabi</strong>, E4E’s AI voice tutor, already teaches
        foundational maths and literacy to Nigerian children over ordinary phone calls. Ayo
        runs on the same proven telephony stack: phone line → audio bridge → real-time voice
        model.
      </>
    ),
  },
  {
    icon: Sparkles,
    body: (
      <>
        Built at <strong style={{ color: INK }}>HackPrinceton Fall ’26</strong>.
      </>
    ),
  },
];

export default function WhyUs() {
  return (
    <div className="relative min-h-full">
      <SectionFloats {...SECTION_FLOATS.why} />
      <div className="relative z-10 mx-auto max-w-3xl px-6 py-24 sm:py-32">
        <Reveal>
          <Sticker bg={LILAC} tilt={4} className="text-sm">
            why us
          </Sticker>
          <h2 className="mt-6 text-3xl font-extrabold tracking-tight sm:text-4xl" style={{ color: INK }}>
            Built by people who grew up where it matters.
          </h2>
        </Reveal>
        <div className="mt-14 flex flex-col gap-6">
          {POINTS.map((point, i) => (
            <Reveal key={i} delay={60 + i * 60}>
              <div className="flex gap-4">
                <span
                  className="flex h-11 w-11 shrink-0 items-center justify-center rounded-2xl"
                  style={{ backgroundColor: `${LILAC}26` }}
                >
                  <point.icon className="h-5 w-5" style={{ color: LILAC }} aria-hidden />
                </span>
                <p className="min-w-0 pt-2 text-[15px] leading-relaxed" style={{ color: BODY }}>
                  {point.body}
                </p>
              </div>
            </Reveal>
          ))}
        </div>
      </div>
    </div>
  );
}
