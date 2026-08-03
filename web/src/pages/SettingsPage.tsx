import { useEffect, useState } from 'react';
import { Card } from '@/components/common/Card';
import { Button } from '@/components/common/Button';
import { Badge } from '@/components/common/Badge';
import { useAuthStore } from '@/stores/authStore';
import { useTheme } from '@/hooks/useTheme';
import { useI18nStore } from '@/i18n';
import { loadDict, t } from '@/i18n';
import { zhCN } from '@/i18n/zh-CN';
import { enUS } from '@/i18n/en-US';
import { useToast } from '@/components/common/Toast';
import type { LocaleCode } from '@/utils/format';

/** 设置：API Key、主题、语言。 */
export default function SettingsPage() {
  const apiKey = useAuthStore((state) => state.apiKey);
  const setApiKey = useAuthStore((state) => state.setApiKey);
  const principal = useAuthStore((state) => state.principal);
  const clearAuth = useAuthStore((state) => state.clear);
  const { theme, toggleTheme } = useTheme();
  const locale = useI18nStore((state) => state.locale);
  const setLocale = useI18nStore((state) => state.setLocale);
  const toast = useToast();
  const [input, setInput] = useState(apiKey ?? '');
  const [showKey, setShowKey] = useState(false);

  // 初始同步字典（刷新后恢复语言）
  useEffect(() => {
    loadDict(locale, locale === 'zh-CN' ? zhCN : enUS);
  }, [locale]);

  const saveKey = () => {
    setApiKey(input.trim() || null);
    toast.push('API Key 已保存', 'success');
  };

  const switchLocale = (next: LocaleCode) => {
    setLocale(next);
    loadDict(next, next === 'zh-CN' ? zhCN : enUS);
  };

  return (
    <div className="space-y-4">
      <h1 className="text-lg font-semibold text-ink">{t('settings.title')}</h1>

      <Card title={t('settings.apiKey')}>
        {principal && (
          <div className="mb-3 flex items-center gap-2 text-sm">
            <Badge tone="info">{principal.role}</Badge>
            <span className="text-ink">{principal.username ?? principal.subject_id}</span>
          </div>
        )}
        <div className="flex gap-2">
          <input
            type={showKey ? 'text' : 'password'}
            className="h-9 flex-1 rounded-md border border-border bg-surface px-2 font-mono text-sm text-ink focus:border-brand focus:outline-none"
            placeholder={t('settings.apiKeyPlaceholder')}
            value={input}
            onChange={(event) => setInput(event.target.value)}
          />
          <Button variant="secondary" onClick={() => setShowKey((prev) => !prev)}>
            {showKey ? '隐藏' : '显示'}
          </Button>
          <Button variant="primary" onClick={saveKey}>
            {t('settings.saveKey')}
          </Button>
          {apiKey && (
            <Button variant="ghost" onClick={() => { clearAuth(); setInput(''); toast.push('已清除', 'info'); }}>
              清除
            </Button>
          )}
        </div>
      </Card>

      <Card title={t('settings.theme')}>
        <Button variant="secondary" onClick={toggleTheme}>
          {theme === 'dark' ? '切换到亮色 ☀️' : '切换到暗色 🌙'}
        </Button>
      </Card>

      <Card title={t('settings.language')}>
        <div className="flex gap-2">
          <Button
            variant={locale === 'zh-CN' ? 'primary' : 'secondary'}
            onClick={() => switchLocale('zh-CN')}
          >
            简体中文
          </Button>
          <Button
            variant={locale === 'en-US' ? 'primary' : 'secondary'}
            onClick={() => switchLocale('en-US')}
          >
            English
          </Button>
        </div>
      </Card>
    </div>
  );
}
