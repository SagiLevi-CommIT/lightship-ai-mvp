import type { Metadata } from "next";
import "./globals.css";
import { Nav } from "@/components/ui/nav";

export const metadata: Metadata = {
  title: "Lightship — Dashcam Video Analysis",
  description: "AI-powered dashcam analysis for autonomous vehicle safety",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <body className="min-h-screen flex flex-col">
        <Nav />
        <main className="flex-1">{children}</main>
      </body>
    </html>
  );
}
