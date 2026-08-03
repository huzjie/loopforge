import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { providerApi } from '@/api/endpoints';
import { queryKeys } from '@/api/queryKeys';

/** Provider 配置列表。 */
export function useProviders() {
  return useQuery({
    queryKey: queryKeys.providers.list,
    queryFn: () => providerApi.list(),
  });
}

/** 角色模型绑定列表。 */
export function useRoles() {
  return useQuery({
    queryKey: queryKeys.providers.roles,
    queryFn: () => providerApi.roles(),
  });
}

/** 连通性测试。 */
export function useTestProvider() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, body }: { id: string; body?: Record<string, unknown> }) => providerApi.test(id, body),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.providers.list });
    },
  });
}

/** 新增 Provider。 */
export function useCreateProvider() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: Record<string, unknown>) => providerApi.create(body),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.providers.list });
    },
  });
}

/** 更新 Provider。 */
export function useUpdateProvider(id: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: Record<string, unknown>) => providerApi.update(id, body),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.providers.list });
    },
  });
}
