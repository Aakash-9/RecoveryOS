import path from "node:path";
import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // The repository root is one level up; without this Next walks upwards
  // looking for a lockfile and warns about the one in the home directory.
  outputFileTracingRoot: path.join(__dirname, ".."),
};

export default nextConfig;
