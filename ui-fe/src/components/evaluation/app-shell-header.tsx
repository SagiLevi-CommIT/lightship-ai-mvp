'use client';

import Link from 'next/link';
import { usePathname } from 'next/navigation';
import type { ReactNode } from 'react';
import SiteBrand from '@/components/evaluation/site-brand';

const navItems = [
  { href: '/', label: 'New Pipeline' },
  { href: '/history', label: 'History' },
];

type AppShellHeaderProps = {
  rightContent?: ReactNode;
};

export default function AppShellHeader({ rightContent }: AppShellHeaderProps) {
  const pathname = usePathname();

  return (
    <header className="flex items-center justify-between rounded-2xl border border-white/[0.06] bg-slate-950/60 px-5 py-3 backdrop-blur-sm">
      <div className="flex items-center gap-6">
        <SiteBrand />

        <div className="hidden h-6 w-px bg-white/10 sm:block" />

        <nav className="hidden items-center gap-1 sm:flex">
          {navItems.map((item) => {
            const isActive = pathname === item.href;

            return (
              <Link
                key={item.href}
                href={item.href}
                className={`rounded-lg px-3.5 py-1.5 text-sm font-medium transition ${
                  isActive
                    ? 'bg-white/10 text-white'
                    : 'text-slate-400 hover:bg-white/5 hover:text-slate-200'
                }`}
              >
                {item.label}
              </Link>
            );
          })}
        </nav>
      </div>

      {rightContent ? <div className="flex items-center justify-end">{rightContent}</div> : null}
    </header>
  );
}
