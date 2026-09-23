import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Standard Tauri + Vite setup: fixed port, don't clear the terminal, ignore Rust changes.
const host = process.env.TAURI_DEV_HOST;

export default defineConfig({
  plugins: [react()],
  clearScreen: false,
  server: {
    port: 1420,
    strictPort: true,
    host: host || false,
    hmr: host ? { protocol: "ws", host, port: 1421 } : undefined,
    watch: { ignored: ["**/src-tauri/**"] },
  },
  build: { target: ["es2021", "chrome100", "safari15"], sourcemap: false },
});
