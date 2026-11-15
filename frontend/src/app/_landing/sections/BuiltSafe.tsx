"use client";

import { Ear, EyeOff, Lock, Quote, ShieldCheck, UserCheck } from "lucide-react";
import { SECTION_FLOATS, SectionFloats, Sticker } from "../ornaments";
import { DEEP, SKY } from "../theme";
import { Reveal } from "../ui";

const SAFETY = [
  {
    icon: UserCheck,
    title: "A person decides, not the AI",
    body: "Ayo’s AI only understands the conversation. Separate fixed rules, plus a trained human reviewer, decide what happens. The AI can’t close a case or decide a child is fine.",
  },
  {
    icon: ShieldCheck,
    title: "Nothing slips through",
    body: "If Ayo isn’t sure, it sends the conversation to a human rather than ignoring it. “Not sure” never means “nothing happened”.",
  },
  {
    icon: Quote,
    title: "The child’s words, not guesses",
    body: "Every concern shown to a reviewer quotes what the child actually said.",
  },
  {
    icon: Ear,
    title: "Honest with children",
    body: "Ayo never promises total secrecy. It tells children what they share goes to someone whose job is to keep them safe — and never to the person hurting them.",
  },
  {
    icon: EyeOff,
    title: "Minimal data",
    body: "Names, phone numbers and emails are removed from transcripts before they’re saved. Location stays in the secure case-review system, with the trained reviewer, not in alert messages.",
  },
  {
    icon: Lock,
    title: "Never promises rescue",
    body: "Ayo won’t say “help is on the way” unless that’s true. In danger it gives the child the real local emergency number.",
  },
];

export default function BuiltSafe() {
  return (
    <div className="relative min-h-full px-4 py-12 sm:px-6 sm:py-16 lg:py-20">
      <SectionFloats {...SECTION_FLOATS.safe} />
      <div
        style={{ backgroundColor: DEEP }}
        className="relative z-10 mx-auto max-w-6xl rounded-[2rem] px-5 py-12 sm:rounded-[2.5rem] sm:px-10 sm:py-16 lg:px-14 lg:py-20"
      >
        <Reveal>
          <div className="flex items-center gap-3">
            <ShieldCheck className="h-7 w-7 shrink-0 text-white" aria-hidden="true" />
            <Sticker bg={SKY} tilt={3} className="text-sm">
              built safe
            </Sticker>
          </div>
          <h2 className="mt-5 max-w-3xl text-balance text-3xl font-extrabold leading-tight tracking-tight text-white sm:mt-6 sm:text-4xl">
            AI listens. Humans decide.
          </h2>
          <p className="mt-3 max-w-2xl text-lg leading-relaxed text-white/90 sm:mt-4">
            Trust is the whole product. These are the rules Ayo is built on.
          </p>
        </Reveal>
        <ul className="mt-8 grid list-none grid-cols-1 gap-4 p-0 sm:mt-10 sm:grid-cols-2 sm:gap-5 lg:mt-12 lg:grid-cols-3">
          {SAFETY.map((s, i) => (
            <Reveal as="li" key={s.title} delay={(i % 3) * 70} className="min-w-0 h-full">
              <div className="ayo-card flex h-full min-w-0 flex-col gap-3 rounded-3xl bg-white/[0.08] p-5 ring-1 ring-white/25 sm:gap-4 sm:p-6">
                <s.icon className="h-6 w-6" style={{ color: "#9FD0E8" }} aria-hidden="true" />
                <h3 className="text-lg font-bold leading-snug text-white">{s.title}</h3>
                <p className="text-base leading-relaxed text-white/90">{s.body}</p>
              </div>
            </Reveal>
          ))}
        </ul>
      </div>
    </div>
  );
}
