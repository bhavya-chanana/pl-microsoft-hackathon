import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  output: "standalone",
  cacheComponents: true,
  partialPrefetching: true,
  poweredByHeader: false,
  devIndicators: false,
  async redirects() {
    return [
      { source: "/multiverse", destination: "/explore/multiverse", permanent: false },
      { source: "/broadcast", destination: "/explore/broadcast", permanent: false },
      { source: "/lab", destination: "/explore/lab", permanent: false },
      { source: "/tactics", destination: "/coach?tab=tactics", permanent: false },
    ];
  },
  turbopack: {
    rules: {
      "*.css": {
        loaders: ["@tailwindcss/turbopack"],
        as: "*.css",
      },
    },
  },
};

export default nextConfig;
