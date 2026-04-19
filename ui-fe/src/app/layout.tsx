import type { Metadata } from 'next';
import type { ReactNode } from 'react';
import { FlowProvider } from '@/components/evaluation/flow-provider';
import { IBM_Plex_Mono, IBM_Plex_Sans } from 'next/font/google';
import './globals.css';

const ibmPlexMono = IBM_Plex_Mono({
  subsets: ['latin'],
  variable: '--font-ibm-plex-mono',
  weight: ['300', '400', '500', '600'],
});

const ibmPlexSans = IBM_Plex_Sans({
  subsets: ['latin'],
  variable: '--font-ibm-plex-sans',
  weight: ['300', '400', '600', '700'],
});

export const metadata: Metadata = {
  title: 'LightShip Evaluation Report',
  description: 'Evaluation report UI rendered with Next.js.',
  icons: {
    icon: '/lightship-logo.png',
    shortcut: '/lightship-logo.png',
    apple: '/lightship-logo.png',
  },
};

export default function RootLayout({
  children,
}: Readonly<{
  children: ReactNode;
}>) {
  return (
    <html lang="en">
      <body className={`${ibmPlexMono.variable} ${ibmPlexSans.variable} antialiased`}>
        <FlowProvider>{children}</FlowProvider>
      </body>
    </html>
  );
}
