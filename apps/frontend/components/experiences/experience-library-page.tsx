'use client';

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import Loader2 from 'lucide-react/dist/esm/icons/loader-2';
import Plus from 'lucide-react/dist/esm/icons/plus';
import { Button } from '@/components/ui/button';
import { ConfirmDialog } from '@/components/ui/confirm-dialog';
import { Dropdown } from '@/components/ui/dropdown';
import { Input } from '@/components/ui/input';
import {
  archiveExperience,
  fetchExperience,
  markExperienceReady,
  restoreExperience,
  type ExperienceDetail,
  type ExperienceKind,
  type ExperienceRead,
  type ExperienceReadyConflictError,
  listExperiences,
} from '@/lib/api/experiences';
import { useTranslations } from '@/lib/i18n';
import { CompletenessPanel } from './completeness-panel';
import { EvidenceListEditor } from './evidence-list-editor';
import { ExperienceEditor } from './experience-editor';
import { ExperienceList } from './experience-list';
import { PermanentDeleteDialog } from './permanent-delete-dialog';
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
type LibraryView = 'active' | 'archived';

export function ExperienceLibraryPage() {
  const { t } = useTranslations();
  const [experiences, setExperiences] = useState<ExperienceRead[]>([]);
  const [selectedExperienceId, setSelectedExperienceId] = useState<number | null>(null);
  const [selectedDetail, setSelectedDetail] = useState<ExperienceDetail | null>(null);
  const [query, setQuery] = useState('');
  const [kind, setKind] = useState<ExperienceKind | 'all'>('all');
  const [view, setView] = useState<LibraryView>('active');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [importOpen, setImportOpen] = useState(false);
  const [mobilePane, setMobilePane] = useState<'list' | 'detail'>('list');
  const [draftDirty, setDraftDirty] = useState(false);
  const [discardDialogOpen, setDiscardDialogOpen] = useState(false);
  const [resetSignal, setResetSignal] = useState(0);
  const [readyError, setReadyError] = useState<string | null>(null);
  const [readySubmitting, setReadySubmitting] = useState(false);
  const [permanentExperienceId, setPermanentExperienceId] = useState<number | null>(null);
  const isMountedRef = useRef(false);
  const listRequestGenerationRef = useRef(0);
  const detailRequestGenerationRef = useRef(0);
  const pendingDiscardActionRef = useRef<(() => void) | null>(null);

  useEffect(() => {
    isMountedRef.current = true;
    return () => {
      isMountedRef.current = false;
      listRequestGenerationRef.current += 1;
      detailRequestGenerationRef.current += 1;
    };
  }, []);

  const loadExperiences = useCallback(
    async (targetView = view) => {
      const requestGeneration = ++listRequestGenerationRef.current;
      setLoading(true);
      setError(null);
      try {
        const response = await listExperiences({ status: targetView });
        if (!isMountedRef.current || requestGeneration !== listRequestGenerationRef.current) return;
        setExperiences(response.items);
        setSelectedExperienceId((current) =>
          response.items.some((item) => item.experience_id === current)
            ? current
            : (response.items[0]?.experience_id ?? null)
        );
      } catch (reason) {
        if (!isMountedRef.current || requestGeneration !== listRequestGenerationRef.current) return;
        setError(reason instanceof Error ? reason.message : t('experiences.error'));
      } finally {
        if (isMountedRef.current && requestGeneration === listRequestGenerationRef.current)
          setLoading(false);
      }
    },
    [t, view]
  );

  useEffect(() => {
    void loadExperiences(view);
  }, [loadExperiences, view]);

  useEffect(() => {
    if (selectedExperienceId === null) {
      setSelectedDetail(null);
      return;
    }
    const requestGeneration = ++detailRequestGenerationRef.current;
    void fetchExperience(selectedExperienceId)
      .then((detail) => {
        if (
          !isMountedRef.current ||
          requestGeneration !== detailRequestGenerationRef.current ||
          detail.experience_id !== selectedExperienceId
        )
          return;
        setSelectedDetail(detail);
      })
      .catch(() => {
        // The list item remains usable if a detail refresh races or fails.
      });
  }, [selectedExperienceId]);

  const filteredExperiences = useMemo(() => {
    const normalizedQuery = query.trim().toLocaleLowerCase();
    return experiences.filter((experience) => {
      if (kind !== 'all' && experience.kind !== kind) return false;
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
  const displayExperience =
    selectedDetail?.experience_id === selectedExperienceId ? selectedDetail : selectedExperience;

  useEffect(() => {
    if (selectedExperienceId !== null && !selectedExperience) {
      setSelectedExperienceId(filteredExperiences[0]?.experience_id ?? null);
    }
  }, [filteredExperiences, selectedExperience, selectedExperienceId]);

  useEffect(() => {
    const handleBeforeUnload = (event: BeforeUnloadEvent) => {
      if (!draftDirty) return;
      event.preventDefault();
      event.returnValue = '';
    };
    if (!draftDirty) return;
    window.addEventListener('beforeunload', handleBeforeUnload);
    return () => window.removeEventListener('beforeunload', handleBeforeUnload);
  }, [draftDirty]);

  useEffect(() => {
    const handleInAppNavigation = (event: MouseEvent) => {
      if (
        !draftDirty ||
        event.defaultPrevented ||
        event.button !== 0 ||
        event.metaKey ||
        event.ctrlKey ||
        event.shiftKey ||
        event.altKey
      )
        return;
      const target = event.target as Element | null;
      const link = target?.closest('a[href]') as HTMLAnchorElement | null;
      if (!link || link.target || link.href === window.location.href) return;
      event.preventDefault();
      pendingDiscardActionRef.current = () => window.location.assign(link.href);
      setDiscardDialogOpen(true);
    };
    document.addEventListener('click', handleInAppNavigation, true);
    return () => document.removeEventListener('click', handleInAppNavigation, true);
  }, [draftDirty]);

  const replaceDetail = useCallback((detail: ExperienceDetail) => {
    setSelectedDetail(detail);
    setExperiences((current) =>
      current.map((item) => (item.experience_id === detail.experience_id ? detail : item))
    );
    setReadyError(null);
  }, []);

  const discardThen = (action: () => void) => {
    if (!draftDirty) {
      action();
      return;
    }
    pendingDiscardActionRef.current = action;
    setDiscardDialogOpen(true);
  };

  const handleDiscard = () => {
    setDraftDirty(false);
    setResetSignal((current) => current + 1);
    const action = pendingDiscardActionRef.current;
    pendingDiscardActionRef.current = null;
    setDiscardDialogOpen(false);
    action?.();
  };

  const handleImported = (experience: ExperienceDetail) => {
    listRequestGenerationRef.current += 1;
    setLoading(false);
    setError(null);
    setQuery('');
    setKind('all');
    setView('active');
    setExperiences((current) => {
      const index = current.findIndex((item) => item.experience_id === experience.experience_id);
      return index === -1
        ? [experience, ...current]
        : current.map((item) =>
            item.experience_id === experience.experience_id ? experience : item
          );
    });
    setSelectedExperienceId(experience.experience_id);
    setSelectedDetail(experience);
    setMobilePane('detail');
  };

  const handleSelectExperience = (experience: ExperienceRead) => {
    discardThen(() => {
      setSelectedExperienceId(experience.experience_id);
      setMobilePane('detail');
    });
  };

  const switchView = (nextView: LibraryView) => {
    if (nextView === view) return;
    discardThen(() => {
      setView(nextView);
      setSelectedExperienceId(null);
      setSelectedDetail(null);
      setMobilePane('list');
    });
  };

  const handleReady = async () => {
    if (!selectedDetail || readySubmitting) return;
    setReadySubmitting(true);
    setReadyError(null);
    try {
      replaceDetail(await markExperienceReady(selectedDetail.experience_id));
    } catch (reason) {
      const conflict = reason as ExperienceReadyConflictError;
      if (conflict?.conflict) {
        setSelectedDetail((current) =>
          current
            ? {
                ...current,
                completeness: conflict.conflict.completeness,
                missing_dimensions: conflict.conflict.missing_dimensions,
              }
            : current
        );
        setReadyError(
          t('experiences.ready.conflict', {
            score: conflict.conflict.completeness,
            missing: conflict.conflict.missing_dimensions.join(', '),
          })
        );
      } else {
        setReadyError(reason instanceof Error ? reason.message : t('experiences.ready.error'));
      }
    } finally {
      setReadySubmitting(false);
    }
  };

  const archive = async () => {
    if (!selectedDetail) return;
    try {
      const archived = await archiveExperience(selectedDetail.experience_id);
      setExperiences((current) =>
        current.filter((item) => item.experience_id !== archived.experience_id)
      );
      setSelectedExperienceId(null);
      setSelectedDetail(null);
      setMobilePane('list');
    } catch (reason) {
      setReadyError(reason instanceof Error ? reason.message : t('experiences.lifecycle.error'));
    }
  };

  const restore = async () => {
    if (!selectedDetail) return;
    try {
      const restored = await restoreExperience(selectedDetail.experience_id);
      setExperiences((current) =>
        current.filter((item) => item.experience_id !== restored.experience_id)
      );
      setSelectedExperienceId(null);
      setSelectedDetail(null);
      setMobilePane('list');
    } catch (reason) {
      setReadyError(reason instanceof Error ? reason.message : t('experiences.lifecycle.error'));
    }
  };

  const handleDeletedPermanently = (experienceId: number) => {
    setExperiences((current) => current.filter((item) => item.experience_id !== experienceId));
    setSelectedExperienceId(null);
    setSelectedDetail(null);
    setMobilePane('list');
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
        <div
          className="flex border-b border-black p-4 md:p-6"
          role="tablist"
          aria-label={t('experiences.views')}
        >
          <Button
            variant={view === 'active' ? 'default' : 'outline'}
            onClick={() => switchView('active')}
          >
            {t('experiences.active')}
          </Button>
          <Button
            className="ml-2"
            variant={view === 'archived' ? 'default' : 'outline'}
            onClick={() => switchView('archived')}
          >
            {t('experiences.archive')}
          </Button>
        </div>
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
              {displayExperience ? (
                <div className="space-y-6">
                  <Button
                    variant="outline"
                    size="sm"
                    className="md:hidden"
                    onClick={() => discardThen(() => setMobilePane('list'))}
                  >
                    {t('experiences.backToList')}
                  </Button>
                  <div className="border-b border-black pb-4">
                    <p className="font-mono text-xs uppercase tracking-widest text-primary">
                      {t(`experiences.kind.${displayExperience.kind}`)}
                    </p>
                    <h2 className="mt-2 font-serif text-3xl font-bold leading-tight">
                      {displayExperience.title}
                    </h2>
                  </div>
                  {selectedDetail?.experience_id === selectedExperienceId ? (
                    <>
                      <div>
                        <h3 className="font-mono text-xs font-bold uppercase tracking-widest text-ink-soft">
                          {t('experiences.rawInput')}
                        </h3>
                        <p className="mt-2 whitespace-pre-wrap border-l-4 border-primary pl-4 text-sm leading-6">
                          {selectedDetail.raw_input}
                        </p>
                      </div>
                      <ExperienceEditor
                        experience={selectedDetail}
                        onSaved={replaceDetail}
                        onDirtyChange={setDraftDirty}
                        resetSignal={resetSignal}
                      />
                      <EvidenceListEditor experience={selectedDetail} onMutated={replaceDetail} />
                      <CompletenessPanel
                        experience={selectedDetail}
                        onMarkReady={() => void handleReady()}
                        submitting={readySubmitting}
                        error={readyError}
                      />
                      {view === 'active' ? (
                        <Button variant="destructive" onClick={() => void archive()}>
                          {t('experiences.lifecycle.archive')}
                        </Button>
                      ) : (
                        <div className="flex flex-wrap gap-2">
                          <Button onClick={() => void restore()}>
                            {t('experiences.lifecycle.restore')}
                          </Button>
                          <Button
                            variant="destructive"
                            onClick={() => setPermanentExperienceId(selectedDetail.experience_id)}
                          >
                            {t('experiences.lifecycle.permanent')}
                          </Button>
                        </div>
                      )}
                    </>
                  ) : (
                    <div>
                      <h3 className="font-mono text-xs font-bold uppercase tracking-widest text-ink-soft">
                        {t('experiences.rawInput')}
                      </h3>
                      <p className="mt-2 whitespace-pre-wrap border-l-4 border-primary pl-4 text-sm leading-6">
                        {displayExperience.raw_input}
                      </p>
                    </div>
                  )}
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
      <ConfirmDialog
        open={discardDialogOpen}
        onOpenChange={(open) => {
          setDiscardDialogOpen(open);
          if (!open) pendingDiscardActionRef.current = null;
        }}
        title={t('experiences.unsaved.title')}
        description={t('experiences.unsaved.description')}
        confirmLabel={t('experiences.unsaved.discard')}
        cancelLabel={t('common.cancel')}
        variant="warning"
        closeOnConfirm={false}
        onConfirm={handleDiscard}
      />
      <PermanentDeleteDialog
        experienceId={permanentExperienceId}
        open={permanentExperienceId !== null}
        onOpenChange={(open) => {
          if (!open) setPermanentExperienceId(null);
        }}
        onDeleted={handleDeletedPermanently}
      />
    </main>
  );
}
