import { useQuery } from '@tanstack/react-query';
import { healthApi } from '@/api/endpoints';
import { queryKeys } from '@/api/queryKeys';

/** 健康详情（顶栏与健康页共用，10s 轮询）。 */
export function useHealthDetail(enabled = true) {
  return useQuery({
    queryKey: queryKeys.health,
    queryFn: () => healthApi.detail(),
    refetchInterval: 10_000,
    enabled,
  });
}

/** 系统信息（版本/能力）。 */
export function useSystemInfo() {
  return useQuery({
    queryKey: queryKeys.systemInfo,
    queryFn: () => healthApi.systemInfo(),
    staleTime: 60_000,
  });
}
