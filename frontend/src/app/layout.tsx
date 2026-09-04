import type { Metadata } from "next";
import "./globals.css";
import { Nav } from "@/components/nav";

export const metadata: Metadata = {
  title: "RecoveryOS",
  description:
    "Agentic revenue-recovery decision engine. Synthetic data, simulated outcomes.",
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body>
        <div className="flex min-h-screen">
          <Nav />
          <main className="min-w-0 flex-1 px-6 py-5 lg:px-8">{children}</main>
        </div>
      </body>
    </html>
  );
}
