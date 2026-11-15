import type { Metadata, Viewport } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Ayo — A child’s best friend.",
  description:
    "A free phone line any child can call, to talk, to be heard, and to get help from a real person when something is wrong. An initiative of Education for Equality.",
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  viewportFit: "cover",
  themeColor: "#7EC4EA",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className="bg-[#FDFBF7] text-[#3D4A5C] antialiased selection:bg-[#5CA6CA]/30">
        {children}
      </body>
    </html>
  );
}
