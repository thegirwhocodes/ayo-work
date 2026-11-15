"use client";

import { SectionDeck } from "./Billboard";
import BuiltSafe from "./sections/BuiltSafe";
import Features from "./sections/Features";
import Footer from "./sections/Footer";
import Hero from "./sections/Hero";
import HowItWorks from "./sections/HowItWorks";
import Problem from "./sections/Problem";
import TalkToAyo from "./sections/TalkToAyo";
import WhatAyoIs from "./sections/WhatAyoIs";
import WhyUs from "./sections/WhyUs";
import { BODY } from "./theme";

export default function Landing() {
  return (
    <div id="top" style={{ color: BODY }}>
      <SectionDeck
        panels={[
          { id: "hero", label: "Ayo — home", rounded: false, node: <Hero /> },
          {
            id: "what-is-ayo",
            label: "What Ayo is",
            className: "bg-[#EAF4FB]",
            node: <WhatAyoIs />,
          },
          {
            id: "how-it-works",
            label: "How it works",
            className: "bg-[#FDF1E4]",
            node: <HowItWorks />,
          },
          {
            id: "features",
            label: "What Ayo does",
            className: "bg-[#FBEFF4]",
            node: <Features />,
          },
          {
            id: "built-safe",
            label: "Built safe",
            className: "bg-[#FDFBF7]",
            node: <BuiltSafe />,
          },
          {
            id: "problem",
            label: "The problem",
            className: "bg-[#FDFBF7]",
            node: <Problem />,
          },
          {
            id: "why-us",
            label: "Why us",
            className: "bg-[#F3EFFA]",
            node: <WhyUs />,
          },
          {
            id: "talk-to-ayo",
            label: "Talk to Ayo Now",
            className: "bg-[#EAF4FB]",
            node: (
              <>
                <TalkToAyo />
                <Footer />
              </>
            ),
          },
        ]}
      />
    </div>
  );
}
