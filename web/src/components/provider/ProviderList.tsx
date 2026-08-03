import { Badge } from '@/components/common/Badge';
import { Button } from '@/components/common/Button';
import { useProviders, useTestProvider } from '@/hooks/useProviders';
import { formatRelativeTime } from '@/utils/format';
import { t } from '@/i18n';
import type { ProviderConfigRead } from '@/api/types';

export interface ProviderListProps {
  onEdit?: (provider: ProviderConfigRead) => void;
}

/** Provider 列表与健康状态。 */
export function ProviderList({ onEdit }: ProviderListProps) {
  const { data, isLoading, error } = useProviders();
  const testProvider = useTestProvider();

  if (isLoading && !data) {
    return <div className="space-y-3">{Array.from({ length: 3 }).map((_, i) => (
      <div key={i} className="h-16 animate-pulse rounded-md bg-raised" />
    ))}</div>;
  }

  if (error) {
    return <p className="text-sm text-danger">{error instanceof Error ? error.message : String(error)}</p>;
  }

  const providers = data?.items ?? [];

  return (
    <div className="space-y-2">
      {providers.map((provider) => (
        <div key={provider.id} className="flex items-center gap-3 rounded-md border border-border p-3">
          <Badge tone={provider.enabled ? 'ok' : 'muted'}>{provider.enabled ? '启用' : '停用'}</Badge>
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-2">
              <span className="text-sm font-medium text-ink">{provider.name}</span>
              <span className="text-xs text-ink-faint">{provider.kind}</span>
            </div>
            <p className="truncate text-xs text-ink-muted">
              {provider.base_url || '（未配置 base_url）'} · {provider.models.length} models ·{' '}
              {provider.has_api_key ? '已配置密钥' : '未配置密钥'}
            </p>
            <p className="text-xs text-ink-faint">更新于 {formatRelativeTime(provider.updated_at)}</p>
          </div>
          <Button
            size="sm"
            variant="ghost"
            loading={testProvider.isPending}
            onClick={() => testProvider.mutate({ id: provider.id })}
          >
            测试
          </Button>
          {onEdit && (
            <Button size="sm" variant="secondary" onClick={() => onEdit(provider)}>
              {t('common.edit')}
            </Button>
          )}
        </div>
      ))}
      {providers.length === 0 && <p className="py-8 text-center text-sm text-ink-muted">{t('common.empty')}</p>}
    </div>
  );
}
