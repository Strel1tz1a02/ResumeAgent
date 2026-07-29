'use client';

import type { ExperienceRead } from '@/lib/api/experiences';
import { useTranslations } from '@/lib/i18n';

interface ExperienceListProps {
  experiences: ExperienceRead[];
  selectedExperienceId: number | null;
  onSelect: (experience: ExperienceRead) => void;
}

export function ExperienceList({
  experiences,
  selectedExperienceId,
  onSelect,
}: ExperienceListProps) {
  const { t } = useTranslations();

  return (
    <ul className="divide-y divide-black border border-black bg-background">
      {experiences.map((experience) => {
        const selected = experience.experience_id === selectedExperienceId;
        return (
          <li key={experience.experience_id}>
            <button
              type="button"
              onClick={() => onSelect(experience)}
              className={`flex w-full flex-col gap-2 px-4 py-4 text-left transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary focus-visible:ring-inset ${
                selected ? 'bg-primary text-white' : 'bg-background hover:bg-paper-tint'
              }`}
            >
              <span className="font-serif text-lg font-bold leading-tight">{experience.title}</span>
              <span className="font-mono text-xs uppercase tracking-wide">
                {t(`experiences.kind.${experience.kind}`)} ·{' '}
                {t(`experiences.status.${experience.status}`)} · {experience.completeness}%
              </span>
              {experience.organization && (
                <span className="text-sm">{experience.organization}</span>
              )}
            </button>
          </li>
        );
      })}
    </ul>
  );
}
