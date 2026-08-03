import { useQuery } from '@tanstack/react-query';
import { eventApi } from '@/api/endpoints';
import { queryKeys } from '@/api/queryKeys';

/** 事件分页查询与筛选。 */
export function useEvents(params?: Record<string, unknown>) {
  return useQuery({
    queryKey: queryKeys.events.list(params),
    queryFn: () => eventApi.list(params),
    placeholderData: (prev) => prev,
  });
}

/** 事件类型字典（62 种及其 payload schema）。 */
export function useEventTypes() {
  return useQuery({
    queryKey: queryKeys.events.types,
    queryFn: () => eventApi.types(),
    staleTime: 60_000,
  });
}
