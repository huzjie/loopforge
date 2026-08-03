import { Link } from 'react-router-dom';
import { t } from '@/i18n';

/** 404。 */
export default function NotFoundPage() {
  return (
    <div className="flex flex-col items-center justify-center gap-3 py-24">
      <p className="text-5xl font-bold text-ink-faint">404</p>
      <p className="text-sm text-ink-muted">{t('notFound.title')}</p>
      <Link to="/" className="text-sm text-brand hover:underline">
        {t('notFound.back')}
      </Link>
    </div>
  );
}
