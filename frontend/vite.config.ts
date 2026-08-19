import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    // `import.meta.dirname` rather than `__dirname`: Vite's native config
    // loader does not provide the CommonJS globals.
    alias: { "@": new URL("./src", import.meta.url).pathname },
  },
  server: {
    port: 5173,
    // Bind IPv4 explicitly. Vite's default listens on ::1 only, so anything
    // that resolves "localhost" to 127.0.0.1 -- curl, some browsers, most
    // health checks -- gets ECONNREFUSED against a server that is running.
    host: "127.0.0.1",
    // The backend serves images straight off local disk, so the UI is same
    // origin in dev and needs no CORS handling of its own.
    proxy: {
      "/api": {
        // Overridable so a second backend can be run alongside a long job
        // rather than restarting the one that is midway through a chapter.
        target: process.env.CTT_API ?? "http://127.0.0.1:8000",
        changeOrigin: true,
      },
    },
  },
});
