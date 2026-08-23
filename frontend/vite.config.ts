import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

export default defineConfig({
  plugins: [react()],
  server: {
    host: true,
    port: 5163,
    // Docker Desktop's bind mount does not forward the host filesystem's change events
    // into the container, so chokidar's inotify watch never fires and no HMR update is
    // ever pushed. A full refresh still serves new code, which is what makes it easy to
    // miss: it reads as HMR being slow rather than absent.
    //
    // ponytail: polling costs a little idle CPU, which is the going rate for editing on
    // one OS and serving from another. Drop it when the dev server runs on the host.
    watch: { usePolling: true, interval: 300 },
    // ponytail: proxy instead of CORS on the backend — one origin, nothing to configure.
    // Outside docker, set VITE_PROXY_TARGET=http://localhost:8001.
    proxy: {
      "/api": {
        target: process.env.VITE_PROXY_TARGET ?? "http://backend:8000",
        rewrite: (p) => p.replace(/^\/api/, ""),
      },
    },
  },
  test: { environment: "jsdom", globals: true },
});
