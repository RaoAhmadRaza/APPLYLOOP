/** @type {import('next').NextConfig} */
const nextConfig = {
  // Silences the "which lockfile is the workspace root" warning — the repo root
  // has its own unrelated package-lock.json (the ponytail plugin, not this app).
  outputFileTracingRoot: import.meta.dirname,
};

export default nextConfig;
