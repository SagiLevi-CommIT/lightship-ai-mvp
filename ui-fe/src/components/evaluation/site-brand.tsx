'use client';

import Image from 'next/image';

export default function SiteBrand() {
  return (
    <div className="inline-flex items-center gap-2.5">
      <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-gradient-to-br from-cyan-500 to-blue-600 p-1 shadow-sm">
        <Image src="/lightship-logo.png" alt="Lightship logo" width={24} height={24} priority className="h-5 w-5 object-contain" />
      </div>
      <p className="font-[family:var(--font-ibm-plex-sans)] text-base font-semibold tracking-tight text-white">
        Lightship
      </p>
    </div>
  );
}
