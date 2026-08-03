import { useNavigate } from 'react-router-dom';
import clsx from 'clsx';
import { useTheme } from '@/hooks/useTheme';
import { useHealthDetail } from '@/hooks/useHealth';
import { useI18nStore } from '@/i18n';
import { loadDict, t } from '@/i18n';
import { zhCN } from '@/i18n/zh-CN';
import { enUS } from '@/i18n/en-US';
import { useUiStore } from '@/stores/uiStore';
import type { LocaleCode } from '@/utils/format';

/** 顶栏：健康指示灯、主题切换、语言切换。 */
export function TopBar() {
  const navigate = useNavigate();
  const { theme, toggleTheme } = useTheme();
  const locale = useI18nStore((state) => state.locale);
  const setLocale = useI18nStore((state) => state.setLocale);
  const sidebarCollapsed = useUiStore((state) => state.sidebarCollapsed);
  const toggleSidebar = useUiStore((state) => state.toggleSidebar);

  // 语言切换时同步字典
  const switchLocale = (next: LocaleCode) => {
    setLocale(next);
    loadDict(next, next === 'zh-CN' ? zhCN : enUS);
  };

  const { data: health } = useHealthDetail();

  const healthTone =
    health?.status === 'healthy' ? 'bg-ok' : health?.status === 'degraded' ? 'bg-warn' : 'bg-danger';

  return (
    <header
      className={clsx(
        'sticky top-0 z-30 flex h-14 items-center gap-3 border-b border-border bg-surface/90 px-4 backdrop-blur transition-all',
        sidebarCollapsed ? 'lg:pl-16' : 'lg:pl-56',
      )}
    >
      <button
        onClick={toggleSidebar}
        className="rounded-md p-1.5 text-ink-muted hover:bg-raised lg:hidden"
        aria-label="menu"
      >
        ☰
      </button>

      <div className="flex items-center gap-2">
        <span className={clsx('h-2.5 w-2.5 rounded-full', healthTone)} title={`${t('topbar.health')}: ${health?.status ?? 'unknown'}`} />
        <span className="text-xs text-ink-muted">{health?.status ?? '…'}</span>
      </div>

      <div className="flex-1" />

      <button
        onClick={() => navigate('/approvals')}
        className="rounded-md px-2 py-1 text-xs text-ink-muted hover:bg-raised hover:text-ink"
      >
        {t('topbar.pendingApprovals')}
      </button>

      <button
        onClick={() => switchLocale(locale === 'zh-CN' ? 'en-US' : 'zh-CN')}
        className="rounded-md px-2 py-1 text-xs text-ink-muted hover:bg-raised hover:text-ink"
        title={t('topbar.lang')}
      >
        {locale === 'zh-CN' ? 'EN' : '中文'}
      </button>

      <button
        onClick={toggleTheme}
        className="rounded-md px-2 py-1 text-sm text-ink-muted hover:bg-raised hover:text-ink"
        title={t('topbar.theme')}
      >
        {theme === 'dark' ? '☀️' : '🌙'}
      </button>
    </header>
  );
}
