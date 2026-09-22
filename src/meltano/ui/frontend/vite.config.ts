import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The build output is served by the Python package, so it lands in
// `src/meltano/ui/static`. `emptyOutDir` is off deliberately: that directory
// also holds `MISSING.html` and `.gitkeep`, which are tracked in git.
export default defineConfig({
  plugins: [react()],
  build: {
    outDir: "../static",
    emptyOutDir: false,
    assetsDir: "assets",
  },
  server: {
    // `npm run dev` proxies the API to a `meltano ui` server started
    // separately, so the SPA and the API stay same-origin in development too.
    proxy: {
      "/api": {
        target: "http://127.0.0.1:5001",
        changeOrigin: false,
      },
    },
  },
});
