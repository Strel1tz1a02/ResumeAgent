'use client';

import { Button } from '@/components/ui/button';
import type { ExperienceDetail } from '@/lib/api/experiences';
import { useTranslations } from '@/lib/i18n';

interface CompletenessPanelProps {
  experience: ExperienceDetail;
  onMarkReady: () => void;
  submitting?: boolean;
  error?: string | null;
}

export function CompletenessPanel({
  experience,
  onMarkReady,
  submitting = false,
  error,
}: CompletenessPanelProps) {
  const { t } = useTranslations();
  const isArchived = experience.status === 'archived';

  return (
    <section className="border border-black p-4" aria-label={t('experiences.completeness.title')}>
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h3 className="font-mono text-xs font-bold uppercase tracking-widest text-ink-soft">
            {t('experiences.completeness.title')}
          </h3>
          <p className="mt-1 font-serif text-2xl font-bold">
            {t('experiences.completeness.score', { score: experience.completeness })}
          </p>
        </div>
        <Button
          size="sm"
          onClick={onMarkReady}
          disabled={submitting || isArchived || experience.status === 'ready'}
        >
          {submitting ? t('experiences.ready.submitting') : t('experiences.ready.action')}
        </Button>
      </div>
      {experience.missing_dimensions.length > 0 && (
        <div className="mt-4">
          <p className="font-mono text-xs font-bold uppercase tracking-widest text-ink-soft">
            {t('experiences.completeness.missing')}
          </p>
          <ul className="mt-2 list-disc space-y-1 pl-5 text-sm">
            {experience.missing_dimensions.map((dimension) => (
              <li key={dimension}>{t(`experiences.completeness.dimension.${dimension}`)}</li>
            ))}
          </ul>
        </div>
      )}
      {experience.suggested_questions.length > 0 && (
        <div className="mt-4">
          <p className="font-mono text-xs font-bold uppercase tracking-widest text-ink-soft">
            {t('experiences.completeness.guidance')}
          </p>
          <ul className="mt-2 space-y-1 text-sm">
            {experience.suggested_questions.map((question) => (
              <li key={question}>• {question}</li>
            ))}
          </ul>
        </div>
      )}
      {error && <p className="mt-4 font-mono text-xs text-destructive">{error}</p>}
    </section>
  );
}
