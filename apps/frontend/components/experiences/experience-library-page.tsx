'use client';

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import Loader2 from 'lucide-react/dist/esm/icons/loader-2';
import Plus from 'lucide-react/dist/esm/icons/plus';
import { Button } from '@/components/ui/button';
import { Dropdown } from '@/components/ui/dropdown';
import { Input } from '@/components/ui/input';
import { listExperiences, type ExperienceKind, type ExperienceRead } from '@/lib/api/experiences';
import { useTranslations } from '@/lib/i18n';
import { ExperienceList } from './experience-list';
import { TextImportDialog } from './text-import-dialog';

const experienceKinds: ExperienceKind[] = [
  'work',
  'internship',
  'project',
  'research',
  'campus',
  'volunteer',
  'other',
];

export function ExperienceLibraryPage() {
  const { t } = useTranslations();
  const [experiences, setExperiences] = useState<ExperienceRead[]>([]);
  const [selectedExperienceId, setSelectedExperienceId] = useState<number | null>(null);
  const [query, setQuery] = useState('');
  const [kind, setKind] = useState<ExperienceKind | 'all'>('all');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [importOpen, setImportOpen] = useState(false);
  const [mobilePane, setMobilePane] = useState<'list' | 'detail'>('list');
  const isMountedRef = useRef(false);
  const listRequestGenerationRef = useRef(0);

  useEffect(() => {
    isMountedRef.current = true;
    return () => {
      isMountedRef.current = false;
      listRequestGenerationRef.current += 1;
    };
  }, []);

  const loadExperiences = useCallback(async () => {
    const requestGeneration = ++listRequestGenerationRef.current;
    setLoading(true);
    setError(null);
    try {
      const response = await listExperiences();
      if (!isMountedRef.current || requestGeneration !== listRequestGenerationRef.current) return;
      setExperiences(response.items);
      setSelectedExperienceId((current) => current ?? response.items[0]?.experience_id ?? null);
    } catch (reason) {
      if (!isMountedRef.current || requestGeneration !== listRequestGenerationRef.current) return;
      setError(reason instanceof Error ? reason.message : t('experiences.error'));
    } finally {
      if (isMountedRef.current && requestGeneration === listRequestGenerationRef.current) {
        setLoading(false);
      }
    }
  }, [t]);

  useEffect(() => {
    void loadExperiences();
  }, [loadExperiences]);

  const filteredExperiences = useMemo(() => {
    const normalizedQuery = query.trim().toLocaleLowerCase();
    return experiences.filter((experience) => {
      const matchesKind = kind === 'all' || experience.kind === kind;
      if (!matchesKind) return false;
      if (!normalizedQuery) return true;
      return [
        experience.title,
        experience.organization,
        experience.role,
        experience.raw_input,
        ...experience.tags,
        ...experience.technologies,
      ]
        .filter((value): value is string => Boolean(value))
        .some((value) => value.toLocaleLowerCase().includes(normalizedQuery));
    });
  }, [experiences, kind, query]);

  const selectedExperience = filteredExperiences.find(
    (experience) => experience.experience_id === selectedExperienceId
  );

  useEffect(() => {
    if (selectedExperienceId !== null && !selectedExperience) {
      setSelectedExperienceId(filteredExperiences[0]?.experience_id ?? null);
    }
  }, [filteredExperiences, selectedExperience, selectedExperienceId]);

  const handleImported = (experience: ExperienceRead) => {
    listRequestGenerationRef.current += 1;
    setLoading(false);
    setError(null);
    setQuery('');
    setKind('all');
    setExperiences((current) => {
      const index = current.findIndex((item) => item.experience_id === experience.experience_id);
      if (index === -1) return [experience, ...current];
      return current.map((item) =>
        item.experience_id === experience.experience_id ? experience : item
      );
    });
    setSelectedExperienceId(experience.experience_id);
    setMobilePane('detail');
  };

  const handleSelectExperience = (experience: ExperienceRead) => {
    setSelectedExperienceId(experience.experience_id);
    setMobilePane('detail');
  };

  return (
    <main
      className="min-h-[100dvh] bg-background px-4 py-6 md:px-8"
      style={{
        backgroundImage:
          'linear-gradient(rgba(29, 78, 216, 0.1) 1px, transparent 1px), linear-gradient(90deg, rgba(29, 78, 216, 0.1) 1px, transparent 1px)',
        backgroundSize: '40px 40px',
      }}
    >
      <div className="mx-auto w-full max-w-[104rem] border border-black bg-background shadow-sw-lg">
        <header className="flex flex-col gap-4 border-b border-black p-4 md:flex-row md:items-end md:justify-between md:p-6">
          <div>
            <p className="font-mono text-xs uppercase tracking-widest text-primary">
              {t('experiences.kicker')}
            </p>
            <h1 className="mt-2 font-serif text-3xl font-bold tracking-tight md:text-4xl">
              {t('experiences.title')}
            </h1>
          </div>
          <div className="flex flex-wrap gap-2">
            <Button variant="outline" onClick={() => void loadExperiences()} disabled={loading}>
              {t('experiences.refresh')}
            </Button>
            <Button onClick={() => setImportOpen(true)}>
              <Plus className="h-4 w-4" />
              {t('experiences.import')}
            </Button>
          </div>
        </header>

        <div className="grid gap-0 border-b border-black p-4 md:grid-cols-[minmax(0,1fr)_14rem] md:p-6">
          <Input
            aria-label={t('experiences.search')}
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder={t('experiences.search')}
            className="md:border-r-0"
          />
          <Dropdown
            label={t('experiences.kind')}
            value={kind}
            onChange={(value) => setKind(value as ExperienceKind | 'all')}
            className="mt-3 md:mt-0"
            options={[
              { id: 'all', label: t('experiences.kind.all') },
              ...experienceKinds.map((value) => ({
                id: value,
                label: t(`experiences.kind.${value}`),
              })),
            ]}
          />
        </div>

        {loading && experiences.length === 0 ? (
          <div className="flex min-h-80 items-center justify-center gap-3 p-8 font-mono text-sm uppercase">
            <Loader2 className="h-5 w-5 animate-spin" /> {t('experiences.loading')}
          </div>
        ) : (
          <div className="grid min-h-[30rem] md:grid-cols-[minmax(18rem,0.85fr)_minmax(0,1.4fr)]">
            <section
              data-testid="experience-list-pane"
              className={`${mobilePane === 'list' ? 'block' : 'hidden'} border-b border-black p-4 md:block md:border-b-0 md:border-r md:p-6`}
            >
              {error && (
                <div className="mb-4 flex items-center justify-between gap-3 border border-destructive bg-red-50 p-3">
                  <p className="font-mono text-xs text-destructive">{t('experiences.error')}</p>
                  <Button size="sm" variant="outline" onClick={() => void loadExperiences()}>
                    {t('experiences.retry')}
                  </Button>
                </div>
              )}
              {experiences.length === 0 ? (
                <p className="border border-dashed border-black p-6 font-mono text-sm uppercase text-ink-soft">
                  {t('experiences.empty')}
                </p>
              ) : filteredExperiences.length === 0 ? (
                <p className="border border-dashed border-black p-6 font-mono text-sm uppercase text-ink-soft">
                  {t('experiences.noResults')}
                </p>
              ) : (
                <ExperienceList
                  experiences={filteredExperiences}
                  selectedExperienceId={selectedExperienceId}
                  onSelect={handleSelectExperience}
                />
              )}
            </section>
            <section
              data-testid="experience-detail-pane"
              className={`${mobilePane === 'detail' ? 'block' : 'hidden'} p-4 md:block md:p-6`}
            >
              {selectedExperience ? (
                <div className="space-y-6">
                  <Button
                    variant="outline"
                    size="sm"
                    className="md:hidden"
                    onClick={() => setMobilePane('list')}
                  >
                    {t('experiences.backToList')}
                  </Button>
                  <div className="border-b border-black pb-4">
                    <p className="font-mono text-xs uppercase tracking-widest text-primary">
                      {t(`experiences.kind.${selectedExperience.kind}`)}
                    </p>
                    <h2 className="mt-2 font-serif text-3xl font-bold leading-tight">
                      {selectedExperience.title}
                    </h2>
                  </div>
                  <div>
                    <h3 className="font-mono text-xs font-bold uppercase tracking-widest text-ink-soft">
                      {t('experiences.rawInput')}
                    </h3>
                    <p className="mt-2 whitespace-pre-wrap border-l-4 border-primary pl-4 text-sm leading-6">
                      {selectedExperience.raw_input}
                    </p>
                  </div>
                </div>
              ) : (
                <p className="font-mono text-sm uppercase text-ink-soft">
                  {t('experiences.empty')}
                </p>
              )}
            </section>
          </div>
        )}
      </div>
      <TextImportDialog
        open={importOpen}
        onOpenChange={setImportOpen}
        onImported={handleImported}
      />
    </main>
  );
}
