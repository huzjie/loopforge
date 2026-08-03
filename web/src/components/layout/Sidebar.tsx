import { NavLink } from 'react-router-dom';
import clsx from 'clsx';
import { useUiStore } from '@/stores/uiStore';
import { useApprovalSummary } from '@/hooks/useApprovals';
import { t } from '@/i18n';

interface NavItem {
  to: string;
  key: string;
  end?: boolean;
}

const NAV_ITEMS: NavItem[] = [
  { to: '/', key: 'nav.overview', end: true },
  { to: '/runs', key: 'nav.runs' },
  { to: '/tasks', key: 'nav.tasks' },
  { to: '/replay', key: 'nav.replay' },
  { to: '/costs', key: 'nav.costs' },
  { to: '/policies', key: 'nav.policies' },
  { to: '/approvals', key: 'nav.approvals' },
  { to: '/providers', key: 'nav.providers' },
  { to: '/projects', key: 'nav.projects' },
  { to: '/health', key: 'nav.health' },
  { to: '/settings', key: 'nav.settings' },
];

/** 导航侧栏（含待审批红点）。 */
export function Sidebar({ collapsed }: { collapsed: boolean }) {
  const toggleSidebar = useUiStore((state) => state.toggleSidebar);
  const { data: approvalSummary } = useApprovalSummary();
  const pending = approvalSummary?.pending ?? 0;

  return (
    <aside
      className={clsx(
        'fixed inset-y-0 left-0 z-40 flex flex-col border-r border-border bg-surface transition-all',
        collapsed ? 'w-14' : 'w-56',
      )}
    >
      <div className="flex h-14 items-center gap-2 border-b border-border px-3">
        <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-brand font-bold text-white">
          LF
        </div>
        {!collapsed && <span className="truncate text-sm font-bold text-ink">LoopForge</span>}
      </div>

      <nav className="flex-1 overflow-y-auto py-2">
        {NAV_ITEMS.map((item) => (
          <NavLink
            key={item.to}
            to={item.to}
            end={item.end}
            title={collapsed ? t(item.key) : undefined}
            className={({ isActive }) =>
              clsx(
                'mx-2 mb-0.5 flex items-center gap-2 rounded-md px-3 py-2 text-sm transition-colors',
                isActive ? 'bg-brand-soft font-medium text-brand' : 'text-ink-muted hover:bg-raised hover:text-ink',
                collapsed && 'justify-center px-0',
              )
            }
          >
            <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-current opacity-60" />
            {!collapsed && <span className="flex-1 truncate">{t(item.key)}</span>}
            {!collapsed && item.key === 'nav.approvals' && pending > 0 && (
              <span className="rounded-full bg-danger px-1.5 text-xs font-medium text-white">{pending}</span>
            )}
          </NavLink>
        ))}
      </nav>

      <button
        onClick={toggleSidebar}
        className="m-2 rounded-md border border-border px-3 py-1.5 text-xs text-ink-muted hover:bg-raised"
      >
        {collapsed ? '»' : '« 折叠'}
      </button>
    </aside>
  );
}
