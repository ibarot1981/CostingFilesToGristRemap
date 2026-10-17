import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    host: "127.0.0.1",
    port: 44571,
    strictPort: true,
    proxy: { "/api": { target: "http://127.0.0.1:44570", changeOrigin: true } },
  },
  build: { outDir: "dist-workbook-baseline-qa", emptyOutDir: false },
});
