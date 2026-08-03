import { useState } from 'react';
import { Button } from '@/components/common/Button';
import { useCreateProvider, useUpdateProvider } from '@/hooks/useProviders';
import { useAuthStore } from '@/stores/authStore';
import { t } from '@/i18n';
import type { ProviderConfigRead } from '@/api/types';

export interface ProviderFormProps {
  /** 传入则编辑；否则新建。 */
  provider?: ProviderConfigRead | null;
  onDone?: () => void;
}

/** Provider 配置表单（密钥输入即遮蔽、测试连通）。 */
export function ProviderForm({ provider, onDone }: ProviderFormProps) {
  const [name, setName] = useState(provider?.name ?? '');
  const [kind, setKind] = useState(provider?.kind ?? 'openai_compatible');
  const [baseUrl, setBaseUrl] = useState(provider?.base_url ?? '');
  const [apiKey, setApiKey] = useState('');
  const [modelsText, setModelsText] = useState(provider?.models.join(', ') ?? '');
  const [enabled, setEnabled] = useState(provider?.enabled ?? true);

  const createProvider = useCreateProvider();
  const updateProvider = useUpdateProvider(provider?.id ?? '');
  const can = useAuthStore((state) => state.can);

  if (!can('admin')) {
    return <p className="text-sm text-ink-muted">需要 admin 权限</p>;
  }

  const submit = () => {
    const body = {
      name: name.trim(),
      kind,
      base_url: baseUrl.trim(),
      ...(apiKey.trim() ? { api_key: apiKey.trim() } : {}),
      models: modelsText.split(',').map((s) => s.trim()).filter(Boolean),
      enabled,
    };
    if (provider) {
      updateProvider.mutate(body, { onSuccess: () => onDone?.() });
    } else {
      createProvider.mutate(body, { onSuccess: () => onDone?.() });
    }
  };

  const inputClass =
    'h-9 w-full rounded-md border border-border bg-surface px-2 text-sm text-ink focus:border-brand focus:outline-none';

  return (
    <div className="space-y-3">
      <div className="grid grid-cols-2 gap-3">
        <label className="text-xs text-ink-muted">
          名称 *
          <input className={inputClass} value={name} onChange={(e) => setName(e.target.value)} />
        </label>
        <label className="text-xs text-ink-muted">
          类型
          <input className={inputClass} value={kind} onChange={(e) => setKind(e.target.value)} />
        </label>
      </div>
      <label className="block text-xs text-ink-muted">
        Base URL
        <input className={inputClass} value={baseUrl} onChange={(e) => setBaseUrl(e.target.value)} placeholder="https://api.openai.com/v1" />
      </label>
      <label className="block text-xs text-ink-muted">
        API Key{provider ? '（留空保持不变）' : ''}
        <input
          className={inputClass}
          type="password"
          value={apiKey}
          onChange={(e) => setApiKey(e.target.value)}
          placeholder="sk-…"
        />
      </label>
      <label className="block text-xs text-ink-muted">
        Models（逗号分隔）
        <input className={inputClass} value={modelsText} onChange={(e) => setModelsText(e.target.value)} placeholder="gpt-4o, gpt-4o-mini" />
      </label>
      <label className="flex items-center gap-2 text-xs text-ink-muted">
        <input type="checkbox" checked={enabled} onChange={(e) => setEnabled(e.target.checked)} />
        启用
      </label>

      {(createProvider.isError || updateProvider.isError) && (
        <p className="text-xs text-danger">
          {(createProvider.error ?? updateProvider.error) instanceof Error
            ? (createProvider.error ?? updateProvider.error)?.message
            : String(createProvider.error ?? updateProvider.error)}
        </p>
      )}

      <div className="flex justify-end gap-2">
        <Button variant="primary" loading={createProvider.isPending || updateProvider.isPending} onClick={submit}>
          {t('common.save')}
        </Button>
      </div>
    </div>
  );
}
