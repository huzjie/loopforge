import { create } from 'zustand';
import { persist } from 'zustand/middleware';
import type { Principal } from '@/api/types';

/**
 * 认证状态：API Key 与当前主体。
 * - apiKey 持久化到 localStorage（key: loopforge.auth），刷新不丢。
 * - 权限判定用 `can(role)`，viewer < operator < admin。
 */
interface AuthState {
  apiKey: string | null;
  principal: Principal | null;
  setApiKey: (key: string | null) => void;
  setPrincipal: (principal: Principal | null) => void;
  clear: () => void;
  can: (required: string) => boolean;
}

const ROLE_RANK: Record<string, number> = { viewer: 1, operator: 2, admin: 3 };

export const useAuthStore = create<AuthState>()(
  persist(
    (set, get) => ({
      apiKey: null,
      principal: null,
      setApiKey: (apiKey: string | null) => set({ apiKey }),
      setPrincipal: (principal: Principal | null) => set({ principal }),
      clear: () => set({ apiKey: null, principal: null }),
      can: (required: string) => {
        const role = get().principal?.role ?? 'viewer';
        return (ROLE_RANK[role] ?? 0) >= (ROLE_RANK[required] ?? 1);
      },
    }),
    { name: 'loopforge.auth', partialize: (s) => ({ apiKey: s.apiKey, principal: s.principal }) },
  ),
);
