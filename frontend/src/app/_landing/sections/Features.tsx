"use client";

import { Clock, HeartHandshake, MapPin, MessageCircle, Phone, UserCheck } from "lucide-react";
import { SECTION_FLOATS, SectionFloats, Sticker } from "../ornaments";
import { AMBER, BODY, CARD_SHADOW, CORAL, GREEN, INK, LILAC, ORANGE, SKY } from "../theme";
import { Reveal } from "../ui";

const FEATURES = [
  {
    icon: Clock,
    color: AMBER,
    title: "Answers instantly, 24/7",
    body: "No queue, no hold music. A warm voice picks up straight away, day or night.",
  },
  {
    icon: Phone,
    color: SKY,
    title: "Works on any phone",
    body: "An ordinary voice call: no smartphone, app, or data. This matters where most children don’t have smartphones.",
  },
  {
    icon: HeartHandshake,
    color: CORAL,
    title: "Listens like a person, not a form",
    body: "Trauma-informed: believes the child, never asks “why” or for graphic details, lets the child set the pace, and is honest that it’s an AI.",
  },
  {
    icon: MessageCircle,
    color: LILAC,
    title: "Hears what isn’t said outright",
    body: "Recognises many kinds of harm, not just physical — emotional abuse, neglect, threats, bullying, and “I’m scared but I don’t know how to say it”.",
  },
  {
    icon: MapPin,
    color: GREEN,
    title: "Knows where to send help",
    body: "Asks gently where the child is. If it’s safe, it can send a one-tap link so the child’s phone shares its location.",
  },
  {
    icon: UserCheck,
    color: ORANGE,
    title: "Gets it to a human",
    body: "Concerns become review items for a trained reviewer, with the child’s own words as evidence and a clear reason why.",
  },
];

export default function Features() {
  return (
    <div className="relative min-h-full">
      <SectionFloats {...SECTION_FLOATS.features} />
      <div className="relative z-10 mx-auto max-w-6xl px-5 py-16 sm:px-6 sm:py-24 lg:py-32">
        <Reveal>
          <Sticker bg={CORAL} tilt={-4} className="text-sm">
            what Ayo does
          </Sticker>
          <h2
            className="mt-5 max-w-3xl text-balance text-3xl font-extrabold leading-tight tracking-tight sm:mt-6 sm:text-4xl"
            style={{ color: INK }}
          >
            Built for the child who has no one else to tell.
          </h2>
        </Reveal>
        <ul className="mt-8 grid list-none grid-cols-1 gap-4 p-0 sm:mt-12 sm:grid-cols-2 sm:gap-5 lg:mt-14 lg:grid-cols-3 lg:gap-6">
          {FEATURES.map((f, i) => (
            <Reveal as="li" key={f.title} delay={(i % 3) * 70} className="min-w-0 h-full">
              <div
                className="ayo-card flex h-full min-w-0 flex-col gap-3 rounded-3xl border border-[#1B2A4A]/10 bg-white p-5 sm:gap-4 sm:p-6"
                style={{ boxShadow: CARD_SHADOW }}
              >
                <span
                  className="flex h-11 w-11 items-center justify-center rounded-2xl"
                  style={{ backgroundColor: `${f.color}26` }}
                  aria-hidden="true"
                >
                  <f.icon className="h-6 w-6" style={{ color: f.color }} aria-hidden="true" />
                </span>
                <h3 className="text-lg font-bold leading-snug" style={{ color: INK }}>
                  {f.title}
                </h3>
                <p className="text-base leading-relaxed" style={{ color: BODY }}>
                  {f.body}
                </p>
              </div>
            </Reveal>
          ))}
        </ul>
      </div>
    </div>
  );
}
