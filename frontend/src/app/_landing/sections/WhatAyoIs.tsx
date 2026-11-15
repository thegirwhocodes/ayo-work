"use client";

import { SECTION_FLOATS, SectionFloats, Sticker } from "../ornaments";
import { BODY, INK, SKY } from "../theme";
import { Reveal } from "../ui";

export default function WhatAyoIs() {
  return (
    <div className="relative min-h-full">
      <SectionFloats {...SECTION_FLOATS.what} />
      <div className="relative z-10 mx-auto max-w-3xl px-5 py-16 sm:px-6 sm:py-24 lg:py-32">
        <Reveal>
          <Sticker bg={SKY} tilt={-3} className="text-sm">
            what Ayo is
          </Sticker>
        </Reveal>
        <Reveal delay={80}>
          <p className="mt-6 text-2xl font-bold leading-snug sm:mt-8 sm:text-3xl" style={{ color: INK }}>
            Ayo is a free phone line that children can call. A warm voice answers straight away, any
            time. It listens without judging, never rushes, and never asks “why”.
          </p>
        </Reveal>
        <Reveal delay={160}>
          <p className="mt-5 text-lg leading-relaxed sm:mt-6" style={{ color: BODY }}>
            When a child shares that someone is hurting them, Ayo makes sure it isn’t lost: their
            words go to a trained person whose job is to keep children safe. Ayo works on any phone,
            with no app, no internet, and no smartphone needed.
          </p>
        </Reveal>
        <Reveal delay={240}>
          <p className="mt-5 text-base leading-relaxed sm:mt-6" style={{ color: BODY }}>
            Ayo is the “little sister” of <strong>Sabi</strong>, E4E’s AI voice tutor that teaches
            Nigerian children maths and reading over ordinary phone calls. Ayo uses the same phone
            technology, pointed at listening instead of teaching. Ayo is honest with children that
            it’s an AI.
          </p>
        </Reveal>
      </div>
    </div>
  );
}
