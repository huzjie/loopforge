import { useCallback, useEffect, useState } from 'react';

export type Theme = 'light' | 'dark';

/**
 * 明暗主题切换与持久化（localStorage: loopforge.theme）。
 * 初始值在 index.html 的防闪烁脚本里已根据 localStorage 设置到 <html>。
 */
export function useTheme() {
  const [theme, setTheme] = useState<Theme>(() => {
    if (typeof document === 'undefined') return 'light';
    return document.documentElement.classList.contains('dark') ? 'dark' : 'light';
  });

  useEffect(() => {
    const root = document.documentElement;
    if (theme === 'dark') {
      root.classList.add('dark');
    } else {
      root.classList.remove('dark');
    }
    try {
      localStorage.setItem('loopforge.theme', theme);
    } catch {
      /* localStorage 不可用时忽略 */
    }
  }, [theme]);

  const toggleTheme = useCallback(() => {
    setTheme((prev) => (prev === 'dark' ? 'light' : 'dark'));
  }, []);

  return { theme, toggleTheme, setTheme };
}
