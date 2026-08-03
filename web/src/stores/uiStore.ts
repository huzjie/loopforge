import { create } from 'zustand';

/**
 * 全局 UI 状态：侧栏折叠、筛选器、回放播放状态。
 * 不涉及服务端数据（那些走 TanStack Query）。
 */
interface UiState {
  sidebarCollapsed: boolean;
  toggleSidebar: () => void;

  // 事件筛选
  eventTypeFilter: string[];
  setEventTypeFilter: (types: string[]) => void;

  // 回放
  replayPlaying: boolean;
  replaySpeed: number;
  setReplayPlaying: (playing: boolean) => void;
  setReplaySpeed: (speed: number) => void;
}

export const useUiStore = create<UiState>((set) => ({
  sidebarCollapsed: false,
  toggleSidebar: () => set((state) => ({ sidebarCollapsed: !state.sidebarCollapsed })),

  eventTypeFilter: [],
  setEventTypeFilter: (eventTypeFilter: string[]) => set({ eventTypeFilter }),

  replayPlaying: false,
  replaySpeed: 1,
  setReplayPlaying: (replayPlaying: boolean) => set({ replayPlaying }),
  setReplaySpeed: (replaySpeed: number) => set({ replaySpeed }),
}));
