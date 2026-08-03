import { create } from 'zustand';
import { persist } from 'zustand/middleware';
import type { LocaleCode } from '@/utils/format';

/**
 * 极简 i18n：`t()` + 语言切换 + 持久化。
 * 字典在 zh-CN.ts / en-US.ts 全量声明，这里只做查找与回退。
 */

export interface I18nDict {
  [key: string]: string;
}

let currentDict: I18nDict = {};
let currentLocale: LocaleCode = 'zh-CN';

interface I18nState {
  locale: LocaleCode;
  setLocale: (locale: LocaleCode) => void;
}

export const useI18nStore = create<I18nState>()(
  persist(
    (set) => ({
      locale: 'zh-CN',
      setLocale: (locale: LocaleCode) => set({ locale }),
    }),
    { name: 'loopforge.locale' },
  ),
);

export function loadDict(locale: LocaleCode, dict: I18nDict): void {
  currentLocale = locale;
  currentDict = dict;
}

/** 翻译查找；键不存在时回退到键本身（便于发现漏译）。 */
export function t(key: string): string {
  return currentDict[key] ?? key;
}

export function getLocale(): LocaleCode {
  return currentLocale;
}

export { currentLocale };
