import { fileURLToPath, URL } from 'node:url';
import { defineConfig } from 'vitest/config';

// vitest 配置：jsdom 环境 + Testing Library setup。
// 测试文件统一放在 src/**/*.test.{ts,tsx}，用 vi.mock 打桩，不依赖后端。
// 注意：不引入 @vitejs/plugin-react —— vitest 自带 esbuild JSX 转换，
// 避免 vite/vitest 双份 Plugin 类型冲突。
export default defineConfig({
  esbuild: {
    jsx: 'automatic',
  },
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./src/test/setup.ts'],
    css: false,
    include: ['src/**/*.test.{ts,tsx}'],
    reporters: ['default'],
  },
});
