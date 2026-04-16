/** @type {import('next').NextConfig} */
const nextConfig = {
  output: "standalone",
  reactStrictMode: true,
  async rewrites() {
    const apiBase = process.env.NEXT_PUBLIC_API_BASE || "";
    if (apiBase) {
      return [
        { source: "/health", destination: `${apiBase}/health` },
        { source: "/jobs", destination: `${apiBase}/jobs` },
        { source: "/presign-upload", destination: `${apiBase}/presign-upload` },
        { source: "/process-video", destination: `${apiBase}/process-video` },
        { source: "/status/:path*", destination: `${apiBase}/status/:path*` },
        { source: "/results/:path*", destination: `${apiBase}/results/:path*` },
        { source: "/download/:path*", destination: `${apiBase}/download/:path*` },
        { source: "/cleanup/:path*", destination: `${apiBase}/cleanup/:path*` },
      ];
    }
    return [];
  },
};

export default nextConfig;
