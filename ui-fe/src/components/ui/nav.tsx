"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { Upload, PlayCircle, Clock, BarChart3 } from "lucide-react";
import { cn } from "@/lib/utils";

const links = [
  { href: "/", label: "Upload", icon: Upload },
  { href: "/run", label: "Processing", icon: PlayCircle },
  { href: "/history", label: "History", icon: Clock },
];

export function Nav() {
  const pathname = usePathname();

  return (
    <header className="border-b bg-white">
      <div className="mx-auto max-w-7xl flex items-center justify-between px-6 h-14">
        <Link href="/" className="flex items-center gap-2 font-semibold text-brand-800">
          <BarChart3 className="h-5 w-5" />
          Lightship MVP
        </Link>
        <nav className="flex gap-1">
          {links.map(({ href, label, icon: Icon }) => (
            <Link
              key={href}
              href={href}
              className={cn(
                "flex items-center gap-1.5 px-3 py-1.5 rounded-md text-sm transition-colors",
                pathname === href
                  ? "bg-brand-50 text-brand-700 font-medium"
                  : "text-gray-600 hover:text-gray-900 hover:bg-gray-50"
              )}
            >
              <Icon className="h-4 w-4" />
              {label}
            </Link>
          ))}
        </nav>
      </div>
    </header>
  );
}
