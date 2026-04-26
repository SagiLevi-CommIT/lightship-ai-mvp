import { NextRequest, NextResponse } from 'next/server';

const ALLOWED_METHODS = new Set(['GET', 'HEAD', 'OPTIONS']);

function isPrivateHost(hostname: string): boolean {
  return (
    hostname === 'localhost' ||
    hostname === '127.0.0.1' ||
    hostname.startsWith('10.') ||
    hostname.startsWith('192.168.') ||
    /^172\.(1[6-9]|2\d|3[0-1])\./.test(hostname)
  );
}

function normalizeHost(rawHost: string | null): string {
  return (rawHost ?? '').trim().toLowerCase();
}

function configuredHosts(): Set<string> {
  const raw = process.env.ALLOWED_HOSTS ?? '';
  return new Set(
    raw
      .split(',')
      .map((value) => value.trim().toLowerCase())
      .filter(Boolean),
  );
}

export function middleware(request: NextRequest) {
  if (!ALLOWED_METHODS.has(request.method)) {
    return NextResponse.json({ error: 'Method not allowed' }, { status: 405 });
  }

  const pathname = request.nextUrl.pathname;
  if (pathname.includes('..') || pathname.includes('\\')) {
    return NextResponse.json({ error: 'Invalid path' }, { status: 400 });
  }

  const userAgent = request.headers.get('user-agent') ?? '';
  const hostHeader = normalizeHost(request.headers.get('host'));
  const hostname = hostHeader.split(':')[0];
  const allowedHosts = configuredHosts();

  const isHealthCheck = userAgent.startsWith('ELB-HealthChecker/');
  if (
    hostHeader &&
    !isHealthCheck &&
    allowedHosts.size > 0 &&
    !allowedHosts.has(hostHeader) &&
    !allowedHosts.has(hostname) &&
    !isPrivateHost(hostname)
  ) {
    return NextResponse.json({ error: 'Invalid host header' }, { status: 400 });
  }

  return NextResponse.next();
}

export const config = {
  matcher: ['/((?!_next/static|_next/image|favicon.ico).*)'],
};
