import type { NextConfig } from 'next';

const nextConfig: NextConfig = {
  reactStrictMode: true,
  // Standalone output so the Docker runtime image stays small and doesn't
  // need the full node_modules tree.
  output: 'standalone',
  // When deployed behind the same ALB that also routes /api paths to the
  // Lambda backend, we keep NEXT_PUBLIC_API_BASE empty and rely on the ALB
  // to do the path-based routing.  When running locally we set
  // NEXT_PUBLIC_API_BASE to the FastAPI URL (uvicorn).
  async rewrites() {
    const apiBase = process.env.NEXT_PUBLIC_API_BASE || '';
    if (!apiBase) return [];
    return [
      { source: '/health', destination: `${apiBase}/health` },
      { source: '/jobs', destination: `${apiBase}/jobs` },
      { source: '/presign-upload', destination: `${apiBase}/presign-upload` },
      { source: '/process-video', destination: `${apiBase}/process-video` },
      { source: '/process-image', destination: `${apiBase}/process-image` },
      { source: '/process-s3-video', destination: `${apiBase}/process-s3-video` },
      { source: '/process-s3-prefix', destination: `${apiBase}/process-s3-prefix` },
      { source: '/status/:path*', destination: `${apiBase}/status/:path*` },
      { source: '/results/:path*', destination: `${apiBase}/results/:path*` },
      { source: '/frames/:path*', destination: `${apiBase}/frames/:path*` },
      { source: '/video-class/:path*', destination: `${apiBase}/video-class/:path*` },
      { source: '/download/:path*', destination: `${apiBase}/download/:path*` },
      { source: '/cleanup/:path*', destination: `${apiBase}/cleanup/:path*` },
      { source: '/client-configs/:path*', destination: `${apiBase}/client-configs/:path*` },
      { source: '/batch/:path*', destination: `${apiBase}/batch/:path*` },
    ];
  },
};

export default nextConfig;
