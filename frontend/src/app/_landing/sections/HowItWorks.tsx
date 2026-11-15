"use client";

import { Ear, PhoneCall, Sparkles, UserCheck } from "lucide-react";
import { SECTION_FLOATS, SectionFloats, Sticker } from "../ornaments";
import { BODY, CARD_SHADOW, CORAL, GREEN, INK, LILAC, ORANGE, SKY } from "../theme";
import { Reveal } from "../ui";

const STEPS = [
  {
    icon: PhoneCall,
    color: CORAL,
    title: "A child calls",
    body: "On any phone, even a basic one. A warm, calm voice answers.",
  },
  {
    icon: Ear,
    color: SKY,
    title: "Ayo listens",
    body: "Like a kind older sister. Short sentences, never rushing, never blaming. A child can talk about anything; they don’t have to be in crisis.",
  },
  {
    icon: Sparkles,
    color: LILAC,
    title: "Ayo understands",
    body: "In the background, Ayo recognises when something may be wrong, even when a child says it indirectly — “I don’t like going home”, “sometimes I don’t get dinner”.",
  },
  {
    icon: UserCheck,
    color: GREEN,
    title: "A human follows up",
    body: "Anything concerning goes to a trained person to review. In an emergency, Ayo tells the child the local emergency number.",
  },
];

export default function HowItWorks() {
  return (
    <div className="relative min-h-full">
      <SectionFloats {...SECTION_FLOATS.how} />
      <div className="relative z-10 mx-auto max-w-6xl px-5 py-16 sm:px-6 sm:py-24 lg:py-32">
        <Reveal>
          <Sticker bg={ORANGE} tilt={3} className="text-sm">
            how it works
          </Sticker>
          <h2
            className="mt-5 max-w-3xl text-balance text-3xl font-extrabold leading-tight tracking-tight sm:mt-6 sm:text-4xl"
            style={{ color: INK }}
          >
            Four steps, one phone call.
          </h2>
        </Reveal>
        <ol className="mt-8 grid list-none grid-cols-1 gap-4 p-0 sm:mt-12 sm:grid-cols-2 sm:gap-5 lg:mt-14 lg:grid-cols-4 lg:gap-6">
          {STEPS.map((step, i) => (
            <Reveal as="li" key={step.title} delay={i * 70} className="min-w-0 h-full">
              <div
                className="ayo-card flex h-full min-w-0 flex-col gap-4 rounded-3xl border border-[#1B2A4A]/10 bg-white p-5 sm:p-6"
                style={{ boxShadow: CARD_SHADOW }}
              >
                <div className="flex items-center gap-3">
                  <span
                    className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full text-base font-extrabold"
                    style={{ backgroundColor: step.color, color: INK }}
                    aria-hidden="true"
                  >
                    {i + 1}
                  </span>
                  <step.icon className="h-5 w-5 shrink-0" style={{ color: step.color }} aria-hidden="true" />
                </div>
                <h3 className="text-lg font-bold leading-snug" style={{ color: INK }}>
                  {step.title}
                </h3>
                <p className="text-base leading-relaxed" style={{ color: BODY }}>
                  {step.body}
                </p>
              </div>
            </Reveal>
          ))}
        </ol>
      </div>
    </div>
  );
}
