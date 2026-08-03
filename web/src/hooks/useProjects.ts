import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { projectApi } from '@/api/endpoints';
import { queryKeys } from '@/api/queryKeys';

/** 项目列表。 */
export function useProjects(params?: Record<string, unknown>) {
  return useQuery({
    queryKey: queryKeys.projects.list(params),
    queryFn: () => projectApi.list(params),
  });
}

/** 项目详情。 */
export function useProjectDetail(id: string | undefined) {
  return useQuery({
    queryKey: queryKeys.projects.detail(id ?? ''),
    queryFn: () => projectApi.get(id as string),
    enabled: Boolean(id),
  });
}

/** 创建项目。 */
export function useCreateProject() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: { slug: string; name: string; description?: string; settings?: Record<string, unknown> }) =>
      projectApi.create(body),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.projects.all });
    },
  });
}
