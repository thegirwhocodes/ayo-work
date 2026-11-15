import type { Metadata } from "next";
import { Manrope } from "next/font/google";
import Landing from "./_landing/Landing";

const manrope = Manrope({
  subsets: ["latin"],
  weight: ["400", "500", "600", "700", "800"],
});

export const metadata: Metadata = {
  title: "Ayo — A child’s best friend.",
  description:
    "A free phone line any child can call, to talk, to be heard, and to get help from a real person when something is wrong. An initiative of Education for Equality.",
};

export default function Page() {
  return (
    <main className={manrope.className}>
      <Landing />
    </main>
  );
}
