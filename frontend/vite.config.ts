import { defineConfig } from "vite"
import { devtools } from "@tanstack/devtools-vite"
import { tanstackStart } from "@tanstack/react-start/plugin/vite"
import viteReact from "@vitejs/plugin-react"
import tailwindcss from "@tailwindcss/vite"
import { nitro } from "nitro/vite"
import { VitePWA } from "vite-plugin-pwa"

const apiTarget = process.env.VITE_DASHBOARD_API_BASE_URL || "http://127.0.0.1:2024"

const config = defineConfig({
  server: {
    host: "127.0.0.1",
    port: 3000,
    proxy: {
      "/dashboard/api": {
        target: apiTarget,
        changeOrigin: true,
      },
    },
  },
  resolve: {
    tsconfigPaths: true,
  },
  plugins: [
    devtools(),
    nitro(),
    tailwindcss(),
    // 路由目录里和页面同放测试文件（跟 components/ 下的做法一致），它们不导出 Route。
    // 不忽略的话，生成路由树时会给每个测试文件刷一条「does not export a Route」，
    // 而那条警告的两种建议（加 "-" 前缀改名 / 配 routeFileIgnorePattern）里，改名会让
    // 路由目录下的测试文件命名与仓库其它地方分叉，所以走配置。
    tanstackStart({
      spa: { enabled: true },
      router: { routeFileIgnorePattern: "\\.(test|spec)\\.[jt]sx?$" },
    }),
    VitePWA({
      injectRegister: false,
      registerType: "prompt",
      outDir: ".output/public",
      devOptions: {
        enabled: true,
      },
      integration: {
        closeBundleOrder: "pre",
      },
      manifest: {
        id: "/",
        name: "XiheAgent",
        short_name: "XiheAgent",
        description: "Enterprise AI coding service.",
        start_url: "/",
        scope: "/",
        display: "standalone",
        theme_color: "#000000",
        background_color: "#ffffff",
        icons: [
          {
            src: "/favicon.png",
            sizes: "192x192",
            type: "image/png",
          },
          {
            src: "/logo512.png",
            sizes: "512x512",
            type: "image/png",
            purpose: "any maskable",
          },
        ],
      },
      workbox: {
        navigateFallback: "/_shell.html",
        navigateFallbackDenylist: [/^\/dashboard\/api\//, /^\/_serverFn\//],
        globPatterns: ["**/*.{js,css,png,svg,ico,webmanifest}"],
        additionalManifestEntries: [
          { url: "/_shell.html", revision: new Date().toISOString() },
        ],
      },
    }),
    viteReact(),
  ],
})

export default config
