import { defineConfig, type PluginOption } from "vite";
import react from "@vitejs/plugin-react";
import fs from "node:fs";
import path from "node:path";
import zlib from "node:zlib";

// The static artifact bundle is ~49 MB on the eager startup path (papers-index.arrow alone is
// 31 MB) and Vite's dev server applies no compression. That is invisible on localhost — the
// whole bundle transfers in ~0.1 s — but it dominates over an SSH tunnel or LAN, where it is
// the difference between a few seconds and minutes. Arrow and JSON compress ~2.5x
// (49 MB -> 19.8 MB measured), so gzip them.
//
// Compressed bodies are cached in memory keyed by path + mtime + size: papers-index costs a
// few hundred ms to gzip, and re-paying that on every reload would waste CPU that the data
// pipeline is usually competing for. The cache invalidates automatically when s11 re-emits.
function compressArtifacts(): PluginOption {
  const cache = new Map<string, { key: string; body: Buffer }>();
  const publicDir = path.resolve(process.cwd(), "public");

  return {
    name: "compress-artifacts",
    configureServer(server) {
      server.middlewares.use((req, res, next) => {
        const url = (req.url || "").split("?")[0];
        if (!url.startsWith("/data/")) return next();
        if (!/\bgzip\b/.test(String(req.headers["accept-encoding"] || ""))) return next();

        const file = path.resolve(publicDir, "." + decodeURIComponent(url));
        // Refuse anything that escapes public/ (path traversal).
        if (!file.startsWith(publicDir + path.sep)) return next();

        let st: fs.Stats;
        try {
          st = fs.statSync(file);
          if (!st.isFile()) return next();
        } catch {
          return next();
        }

        const key = `${st.mtimeMs}:${st.size}`;
        const send = (body: Buffer) => {
          res.setHeader(
            "Content-Type",
            url.endsWith(".json") ? "application/json" : "application/octet-stream",
          );
          res.setHeader("Content-Encoding", "gzip");
          res.setHeader("Content-Length", String(body.byteLength));
          res.setHeader("Vary", "Accept-Encoding");
          res.end(body);
        };

        const hit = cache.get(file);
        if (hit && hit.key === key) return send(hit.body);

        zlib.gzip(fs.readFileSync(file), { level: 6 }, (err, body) => {
          if (err) return next();
          cache.set(file, { key, body });
          send(body);
        });
      });
    },
  };
}

export default defineConfig({
  // Project pages are served from https://<user>.github.io/research-atlas/, so asset URLs
  // must be prefixed. Without this every /assets/... request 404s on Pages while working
  // perfectly on localhost — the failure only appears once deployed.
  base: process.env.VITE_BASE ?? "/research-atlas/",
  plugins: [react(), compressArtifacts()],
  server: { port: 5173, host: true },
  // `vite preview` serves the production build; the same artifacts need compressing there.
  preview: { port: 4173, host: true },
});
