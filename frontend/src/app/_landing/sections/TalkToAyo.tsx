"use client";

import { PhoneCall, TriangleAlert } from "lucide-react";
import { Mascot, SECTION_FLOATS, SectionFloats, Sticker } from "../ornaments";
import { BODY, CARD_SHADOW, GOLD, GREEN, INK, ORANGE } from "../theme";
import { Reveal } from "../ui";

export default function TalkToAyo() {
  return (
    <div className="relative min-h-full">
      <SectionFloats {...SECTION_FLOATS.talk} />
      <div className="relative z-10 mx-auto max-w-3xl px-5 py-16 text-center sm:px-6 sm:py-24 lg:py-32">
        <Reveal>
          <div className="flex justify-center">
            <Mascot />
          </div>
          <div className="mt-2 flex justify-center">
            <Sticker bg={GREEN} tilt={-3} className="text-sm">
              say hello
            </Sticker>
          </div>
          <h2 className="mt-6 text-3xl font-extrabold tracking-tight sm:text-4xl" style={{ color: INK }}>
            Talk to Ayo Now
          </h2>
          <p className="mx-auto mt-5 max-w-xl text-lg leading-relaxed" style={{ color: BODY }}>
            Ayo is answering calls on a demo line at HackPrinceton this weekend — find the E4E table
            to hear it live. A public number for children launches once our safeguarding rules have
            been reviewed by child-protection professionals.
          </p>
          <div
            className="mx-auto mt-8 flex max-w-md items-start gap-3 rounded-2xl bg-white px-5 py-4 text-left sm:inline-flex sm:max-w-none sm:items-center sm:rounded-full sm:px-6 sm:py-3"
            style={{ boxShadow: CARD_SHADOW }}
          >
            <PhoneCall className="mt-0.5 h-5 w-5 shrink-0 sm:mt-0" style={{ color: GREEN }} aria-hidden />
            <span className="min-w-0 text-sm font-bold leading-snug" style={{ color: INK }}>
              Demo line (HackPrinceton) — ask the team
            </span>
          </div>
        </Reveal>

        <Reveal delay={120}>
          <div
            role="note"
            aria-labelledby="ayo-status-heading"
            className="mt-14 flex flex-col gap-3 rounded-3xl border-2 bg-white p-6 text-left sm:flex-row sm:items-start sm:gap-4"
            style={{ borderColor: GOLD }}
          >
            <TriangleAlert className="h-5 w-5 shrink-0 sm:mt-0.5" style={{ color: ORANGE }} aria-hidden />
            <div className="min-w-0">
              <h3 id="ayo-status-heading" className="text-base font-bold" style={{ color: INK }}>
                Where Ayo is today
              </h3>
              <p className="mt-2 text-sm leading-relaxed" style={{ color: BODY }}>
                Ayo is a working prototype built at HackPrinceton (Nov 13–15, 2026). It is not yet a
                live service for children. Live phone calls, in-call location sharing, concern review,
                and human case review work today; escalation rules are a demo policy that needs review
                by child-protection professionals before real use.
              </p>
            </div>
          </div>
        </Reveal>
      </div>
    </div>
  );
}
