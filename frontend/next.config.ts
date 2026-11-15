import type { NextConfig } from "next";

const oneYear = "public, max-age=31536000, immutable";
const hourSWR = "public, max-age=3600, s-maxage=86400, stale-while-revalidate=604800";

const nextConfig: NextConfig = {
  poweredByHeader: false,
  compress: true,
  images: {
    formats: ["image/avif", "image/webp"],
  },
  async headers() {
    return [
      {
        source: "/brand/:path*",
        headers: [
          { key: "Cache-Control", value: oneYear },
          { key: "CDN-Cache-Control", value: oneYear },
        ],
      },
      {
        source: "/noise.png",
        headers: [
          { key: "Cache-Control", value: oneYear },
          { key: "CDN-Cache-Control", value: oneYear },
        ],
      },
      {
        // Next hashed build assets
        source: "/_next/static/:path*",
        headers: [
          { key: "Cache-Control", value: oneYear },
          { key: "CDN-Cache-Control", value: oneYear },
        ],
      },
      {
        source: "/_next/image",
        headers: [
          { key: "Cache-Control", value: oneYear },
        ],
      },
      {
        // Pre-rendered landing HTML — cache hard at the edge, revalidate in background
        source: "/",
        headers: [
          { key: "Cache-Control", value: hourSWR },
          { key: "CDN-Cache-Control", value: hourSWR },
          { key: "X-Content-Type-Options", value: "nosniff" },
        ],
      },
    ];
  },
};

export default nextConfig;
