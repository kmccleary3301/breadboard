import { defineConfig } from "vitest/config"

export default defineConfig({
  test: {
    environment: "node",
    include: [
      "tests/**/*.{test,spec}.ts",
      "tests/**/*.{test,spec}.tsx",
      "src/**/__tests__/**/*.{test,spec}.ts",
      "src/**/__tests__/**/*.{test,spec}.tsx",
      "src/**/*.{test,spec}.ts",
      "src/**/*.{test,spec}.tsx",
    ],
    pool: "threads",
  },
})
