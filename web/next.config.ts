import type { NextConfig } from "next";

// Static export: the app is hosted as plain files on AWS Amplify Hosting.
// All data fetching happens client-side against NEXT_PUBLIC_API_URL.
const nextConfig: NextConfig = {
  output: "export",
  // Emit /finding/index.html (not finding.html) so any static host, including
  // Amplify Hosting, serves /finding/?scan=..&id=.. without rewrite rules.
  trailingSlash: true,
  images: { unoptimized: true },
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
