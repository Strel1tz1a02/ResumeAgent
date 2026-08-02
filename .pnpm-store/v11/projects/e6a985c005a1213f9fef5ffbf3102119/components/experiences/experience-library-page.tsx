'use client';

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import Loader2 from 'lucide-react/dist/esm/icons/loader-2';
import Plus from 'lucide-react/dist/esm/icons/plus';
import ArrowLeft from 'lucide-react/dist/esm/icons/arrow-left';
import Link from 'next/link';
import { Button } from '@/components/ui/button';
import { ConfirmDialog } from '@/components/ui/confirm-dialog';
import { Dropdown } from '@/components/ui/dropdown';
import { Input } from '@/components/ui/input';
import type {
  ExperienceDetail,
  ExperienceKind,
  ExperienceGlobalSave,
  ExperienceRead,
  ExperienceReadyConflictError,
} from '@/lib/api/experiences';
import { useTranslations } from '@/lib/i18n';
import { experienceKeys } from '@/lib/queries/experiences/keys';
import {
  useArchiveExperienceMutation,
  useCreateExperienceMutation,
  useExperienceCreationPending,
  useMarkExperienceReadyMutation,
  useRestoreExperienceMutation,
  useSaveExperienceMutation,
  experienceMutationKeys,
} from '@/lib/queries/experiences/mutations';
import { ExperienceQueryProvider } from '@/lib/queries/experiences/provider';
import { useExperienceDetail, useExperienceList } from '@/lib/queries/experiences/queries';
import { CompletenessPanel } from './completeness-panel';
import { EvidenceListEditor } from './evidence-list-editor';
import { ExperienceEditor } from './experience-editor';
import { ExperienceList } from './experience-list';
import { PermanentDeleteDialog } from './permanent-delete-dialog';
import { TextImportDialog } from './text-import-dialog';
import { ExperienceAiChatProvider } from './ai-chat/use-experience-ai-chat';
import { ExperienceChatPanel } from './ai-chat/experience-chat-panel';

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

function filterExperiences(
  experiences: ExperienceRead[],
  query: string,
  kind: ExperienceKind | 'all'
): ExperienceRead[] {
  const normalizedQuery = query.trim().toLocaleLowerCase();
  return experiences.filter((experience) => {
    if (kind !== 'all' && experience.kind !== kind) return false;
    if (!normalizedQuery) return true;
    return [
      experience.title,
      experience.organization,
      experience.role,
      experience.background,
      ...experience.tags,
      ...experience.technologies,
    ]
      .filter((value): value is string => Boolean(value))
      .some((value) => value.toLocaleLowerCase().includes(normalizedQuery));
  });
}

function ExperienceLibraryContent() {
  const { t } = useTranslations();
  const queryClient = useQueryClient();
  const [selectedExperienceId, setSelectedExperienceId] = useState<number | null>(null);
  const [query, setQuery] = useState('');
  const [kind, setKind] = useState<ExperienceKind | 'all'>('all');
  const [view, setView] = useState<LibraryView>('active');
  const [importOpen, setImportOpen] = useState(false);
  const [mobilePane, setMobilePane] = useState<'list' | 'detail'>('list');
  const [metadataDirty, setMetadataDirty] = useState(false);
  const [evidenceDirty, setEvidenceDirty] = useState(false);
  const [metadataDraftValid, setMetadataDraftValid] = useState(false);
  const [evidenceDraftValid, setEvidenceDraftValid] = useState(false);
  const [discardDialogOpen, setDiscardDialogOpen] = useState(false);
  const [resetSignal, setResetSignal] = useState(0);
  const [readyError, setReadyError] = useState<{ experienceId: number; message: string } | null>(
    null
  );
  const [permanentExperienceId, setPermanentExperienceId] = useState<number | null>(null);
  const pendingDiscardActionRef = useRef<(() => void) | null>(null);
  const selectedExperienceIdRef = useRef<number | null>(null);
  const dirtyRef = useRef(false);
  const hasUnsavedChanges = metadataDirty || evidenceDirty;
  const listQuery = useExperienceList(view);
  const experiences = useMemo(() => listQuery.data?.items ?? [], [listQuery.data?.items]);
  const detailQuery = useExperienceDetail(selectedExperienceId);
  const selectedDetail = detailQuery.data ?? null;
  const mutationExperienceId = selectedDetail?.experience_id ?? selectedExperienceId ?? 0;
  const createMutation = useCreateExperienceMutation();
  const creationPending = useExperienceCreationPending();
  const readyMutation = useMarkExperienceReadyMutation(mutationExperienceId);
  const archiveMutation = useArchiveExperienceMutation(mutationExperienceId);
  const restoreMutation = useRestoreExperienceMutation(mutationExperienceId);
  const globalSaveMutation = useSaveExperienceMutation(mutationExperienceId);
  const metadataSaveRef = useRef<ExperienceGlobalSave['experience'] | null>(null);
  const evidenceSaveRef = useRef<Pick<
    ExperienceGlobalSave,
    'evidence_items' | 'new_evidence' | 'expected_collection_revision'
  > | null>(null);
  const metadataValidRef = useRef(false);
  const evidenceValidRef = useRef(false);

  const captureMetadataDraft = useCallback(
    (value: ExperienceGlobalSave['experience'], valid: boolean) => {
      metadataSaveRef.current = value;
      metadataValidRef.current = valid;
      setMetadataDraftValid(valid);
    },
    []
  );
  const captureEvidenceDraft = useCallback(
    (
      value: Pick<
        ExperienceGlobalSave,
        'evidence_items' | 'new_evidence' | 'expected_collection_revision'
      >,
      valid: boolean
    ) => {
      evidenceSaveRef.current = value;
      evidenceValidRef.current = valid;
      setEvidenceDraftValid(valid);
    },
    []
  );
  const saveAll = useCallback(() => {
    const experience = metadataSaveRef.current;
    const evidence = evidenceSaveRef.current;
    if (
      !experience ||
      !evidence ||
      !metadataValidRef.current ||
      !evidenceValidRef.current ||
      globalSaveMutation.isPending
    )
      return;
    globalSaveMutation.mutate({ experience, ...evidence });
  }, [globalSaveMutation]);

  useEffect(() => {
    selectedExperienceIdRef.current = selectedExperienceId;
  }, [selectedExperienceId]);

  useEffect(() => {
    dirtyRef.current = hasUnsavedChanges;
  }, [hasUnsavedChanges]);

  useEffect(() => {
    if (!listQuery.data) return;
    const currentId = selectedExperienceIdRef.current;
    if (listQuery.data.items.some((item) => item.experience_id === currentId)) return;
    const replacementId = listQuery.data.items[0]?.experience_id ?? null;
    const selectReplacement = () => {
      selectedExperienceIdRef.current = replacementId;
      setSelectedExperienceId(replacementId);
    };
    if (currentId !== null && dirtyRef.current) {
      pendingDiscardActionRef.current = selectReplacement;
      setDiscardDialogOpen(true);
    } else {
      selectReplacement();
    }
  }, [listQuery.data]);

  const filteredExperiences = useMemo(
    () => filterExperiences(experiences, query, kind),
    [experiences, kind, query]
  );

  const selectedExperience = filteredExperiences.find(
    (experience) => experience.experience_id === selectedExperienceId
  );
  const displayExperience =
    selectedDetail?.experience_id === selectedExperienceId ? selectedDetail : selectedExperience;

  useEffect(() => {
    const handleBeforeUnload = (event: BeforeUnloadEvent) => {
      if (!hasUnsavedChanges) return;
      event.preventDefault();
      event.returnValue = '';
    };
    if (!hasUnsavedChanges) return;
    window.addEventListener('beforeunload', handleBeforeUnload);
    return () => window.removeEventListener('beforeunload', handleBeforeUnload);
  }, [hasUnsavedChanges]);

  useEffect(() => {
    const handleInAppNavigation = (event: MouseEvent) => {
      if (
        !hasUnsavedChanges ||
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
  }, [hasUnsavedChanges]);

  const selectExperience = useCallback((experienceId: number | null) => {
    selectedExperienceIdRef.current = experienceId;
    setSelectedExperienceId(experienceId);
    setReadyError(null);
  }, []);

  const discardThen = (action: () => void) => {
    if (!hasUnsavedChanges) {
      action();
      return;
    }
    pendingDiscardActionRef.current = action;
    setDiscardDialogOpen(true);
  };

  const handleDiscard = () => {
    setMetadataDirty(false);
    setEvidenceDirty(false);
    setResetSignal((current) => current + 1);
    const action = pendingDiscardActionRef.current;
    pendingDiscardActionRef.current = null;
    setDiscardDialogOpen(false);
    action?.();
  };

  const applyFilters = (nextQuery: string, nextKind: ExperienceKind | 'all') => {
    const nextFiltered = filterExperiences(experiences, nextQuery, nextKind);
    const selectedStillVisible = nextFiltered.some(
      (experience) => experience.experience_id === selectedExperienceIdRef.current
    );
    const apply = () => {
      setQuery(nextQuery);
      setKind(nextKind);
      if (!selectedStillVisible) {
        selectExperience(nextFiltered[0]?.experience_id ?? null);
      }
    };
    if (!selectedStillVisible && hasUnsavedChanges) {
      pendingDiscardActionRef.current = apply;
      setDiscardDialogOpen(true);
      return;
    }
    apply();
  };

  const handleImported = (experience: ExperienceDetail) => {
    setQuery('');
    setKind('all');
    setView('active');
    selectExperience(experience.experience_id);
    setMobilePane('detail');
  };

  const handleCreate = async () => {
    if (
      creationPending ||
      queryClient.isMutating({ mutationKey: experienceMutationKeys.creation(), exact: true }) > 0
    )
      return;
    try {
      handleImported(await createMutation.mutateAsync({}));
    } catch {
      // 下方由 mutation 展示本地化错误信息。
    }
  };

  const handleSelectExperience = (experience: ExperienceRead) => {
    discardThen(() => {
      selectExperience(experience.experience_id);
      setMobilePane('detail');
    });
  };

  const switchView = (nextView: LibraryView, moveFocus = false) => {
    if (nextView === view) return;
    discardThen(() => {
      setView(nextView);
      selectExperience(null);
      setMobilePane('list');
      if (moveFocus) document.getElementById(`experience-view-${nextView}-tab`)?.focus();
    });
  };

  const handleViewKeyDown = (event: React.KeyboardEvent<HTMLButtonElement>) => {
    const nextView =
      event.key === 'ArrowRight' || event.key === 'End'
        ? 'archived'
        : event.key === 'ArrowLeft' || event.key === 'Home'
          ? 'active'
          : null;
    if (!nextView) return;
    event.preventDefault();
    switchView(nextView, true);
  };

  const handleReady = async () => {
    if (!selectedDetail) return;
    const targetId = selectedDetail.experience_id;
    if (
      readyMutation.isPending ||
      queryClient.isMutating({
        mutationKey: experienceMutationKeys.item(targetId, 'ready'),
        exact: true,
      }) > 0
    )
      return;
    setReadyError(null);
    try {
      await readyMutation.mutateAsync();
    } catch (reason) {
      if (selectedExperienceIdRef.current !== targetId) return;
      const conflict = reason as ExperienceReadyConflictError;
      if (conflict?.conflict) {
        queryClient.setQueryData<ExperienceDetail>(experienceKeys.detail(targetId), (current) =>
          current
            ? {
                ...current,
                completeness: conflict.conflict.completeness,
                missing_dimensions: conflict.conflict.missing_dimensions,
              }
            : current
        );
        setReadyError({
          experienceId: targetId,
          message: t('experiences.ready.conflict', {
            score: conflict.conflict.completeness,
            missing: conflict.conflict.missing_dimensions.join(', '),
          }),
        });
      } else {
        setReadyError({
          experienceId: targetId,
          message: reason instanceof Error ? reason.message : t('experiences.ready.error'),
        });
      }
    }
  };

  const archive = async () => {
    if (!selectedDetail) return;
    const targetId = selectedDetail.experience_id;
    if (
      archiveMutation.isPending ||
      queryClient.isMutating({
        mutationKey: experienceMutationKeys.item(targetId, 'archive'),
        exact: true,
      }) > 0
    )
      return;
    try {
      await archiveMutation.mutateAsync();
      if (selectedExperienceIdRef.current === targetId) {
        selectExperience(null);
        setMobilePane('list');
      }
    } catch (reason) {
      if (selectedExperienceIdRef.current === targetId) {
        setReadyError({
          experienceId: targetId,
          message: reason instanceof Error ? reason.message : t('experiences.lifecycle.error'),
        });
      }
    }
  };

  const restore = async () => {
    if (!selectedDetail) return;
    const targetId = selectedDetail.experience_id;
    if (
      restoreMutation.isPending ||
      queryClient.isMutating({
        mutationKey: experienceMutationKeys.item(targetId, 'restore'),
        exact: true,
      }) > 0
    )
      return;
    try {
      await restoreMutation.mutateAsync();
      if (selectedExperienceIdRef.current === targetId) {
        selectExperience(null);
        setMobilePane('list');
      }
    } catch (reason) {
      if (selectedExperienceIdRef.current === targetId) {
        setReadyError({
          experienceId: targetId,
          message: reason instanceof Error ? reason.message : t('experiences.lifecycle.error'),
        });
      }
    }
  };

  const handleDeletedPermanently = (experienceId: number) => {
    if (selectedExperienceIdRef.current === experienceId) {
      selectExperience(null);
      setMobilePane('list');
    }
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
            <Link
              href="/dashboard"
              className="mb-3 inline-flex items-center gap-1 font-mono text-xs uppercase text-ink-soft hover:text-primary"
            >
              <ArrowLeft className="h-3.5 w-3.5" />
              {t('experiences.backToDashboard')}
            </Link>
            <p className="font-mono text-xs uppercase tracking-widest text-primary">
              {t('experiences.kicker')}
            </p>
            <h1 className="mt-2 font-serif text-3xl font-bold tracking-tight md:text-4xl">
              {t('experiences.title')}
            </h1>
          </div>
          <div className="flex flex-wrap gap-2">
            <Button
              variant="outline"
              onClick={() => {
                void listQuery.refetch();
                if (selectedExperienceId !== null) void detailQuery.refetch();
              }}
              disabled={listQuery.isFetching}
            >
              {t('experiences.refresh')}
            </Button>
            <Button
              variant="outline"
              onClick={() => discardThen(() => void handleCreate())}
              disabled={creationPending}
            >
              <Plus className="h-4 w-4" />
              {createMutation.isPending
                ? t('experiences.creating')
                : t('experiences.createFromTemplate')}
            </Button>
            <Button onClick={() => setImportOpen(true)} disabled={creationPending}>
              <Plus className="h-4 w-4" />
              {t('experiences.import.button')}
            </Button>
          </div>
        </header>
        <div
          className="flex border-b border-black p-4 md:p-6"
          role="tablist"
          aria-label={t('experiences.views')}
        >
          <Button
            id="experience-view-active-tab"
            role="tab"
            aria-selected={view === 'active'}
            aria-controls="experience-view-active-panel"
            tabIndex={view === 'active' ? 0 : -1}
            variant={view === 'active' ? 'default' : 'outline'}
            onClick={() => switchView('active')}
            onKeyDown={handleViewKeyDown}
          >
            {t('experiences.active')}
          </Button>
          <Button
            id="experience-view-archived-tab"
            role="tab"
            aria-selected={view === 'archived'}
            aria-controls="experience-view-archived-panel"
            tabIndex={view === 'archived' ? 0 : -1}
            className="ml-2"
            variant={view === 'archived' ? 'default' : 'outline'}
            onClick={() => switchView('archived')}
            onKeyDown={handleViewKeyDown}
          >
            {t('experiences.archive')}
          </Button>
        </div>
        <div className="grid gap-0 border-b border-black p-4 md:grid-cols-[minmax(0,1fr)_14rem] md:p-6">
          <Input
            aria-label={t('experiences.search')}
            value={query}
            onChange={(event) => applyFilters(event.target.value, kind)}
            placeholder={t('experiences.search')}
            className="md:border-r-0"
          />
          <Dropdown
            label={t('experiences.kind')}
            value={kind}
            onChange={(value) => applyFilters(query, value as ExperienceKind | 'all')}
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
        {listQuery.isPending && experiences.length === 0 ? (
          <div className="flex min-h-80 items-center justify-center gap-3 p-8 font-mono text-sm uppercase">
            <Loader2 className="h-5 w-5 animate-spin" /> {t('experiences.loading')}
          </div>
        ) : (
          <div
            id={`experience-view-${view}-panel`}
            role="tabpanel"
            aria-labelledby={`experience-view-${view}-tab`}
            className="grid min-h-[30rem] md:grid-cols-[minmax(18rem,0.85fr)_minmax(0,1.4fr)]"
          >
            <section
              data-testid="experience-list-pane"
              className={`${mobilePane === 'list' ? 'block' : 'hidden'} border-b border-black p-4 md:block md:border-b-0 md:border-r md:p-6`}
            >
              {(listQuery.error || createMutation.error) && (
                <div className="mb-4 flex items-center justify-between gap-3 border border-destructive bg-red-50 p-3">
                  <p className="font-mono text-xs text-destructive">{t('experiences.error')}</p>
                  <Button size="sm" variant="outline" onClick={() => void listQuery.refetch()}>
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
                    <ExperienceAiChatProvider experienceId={selectedDetail.experience_id}>
                      <ExperienceEditor
                        key={`metadata-${selectedDetail.experience_id}`}
                        experience={selectedDetail}
                        onDirtyChange={setMetadataDirty}
                        resetSignal={resetSignal}
                        globalDirty={hasUnsavedChanges && metadataDraftValid && evidenceDraftValid}
                        globalSaving={globalSaveMutation.isPending}
                        globalError={globalSaveMutation.error}
                        onGlobalSave={saveAll}
                        onGlobalDraftChange={captureMetadataDraft}
                      />
                      <EvidenceListEditor
                        key={`evidence-${selectedDetail.experience_id}`}
                        experience={selectedDetail}
                        onDirtyChange={setEvidenceDirty}
                        resetSignal={resetSignal}
                        globalSaving={globalSaveMutation.isPending}
                        onGlobalDraftChange={captureEvidenceDraft}
                      />
                      <ExperienceChatPanel />
                      <CompletenessPanel
                        experience={selectedDetail}
                        onMarkReady={() => void handleReady()}
                        submitting={readyMutation.isPending}
                        error={
                          readyError?.experienceId === selectedDetail.experience_id
                            ? readyError.message
                            : null
                        }
                      />
                      {view === 'active' ? (
                        <Button
                          variant="destructive"
                          onClick={() => void archive()}
                          disabled={archiveMutation.isPending}
                        >
                          {t('experiences.lifecycle.archive')}
                        </Button>
                      ) : (
                        <div className="flex flex-wrap gap-2">
                          <Button
                            onClick={() => void restore()}
                            disabled={restoreMutation.isPending}
                          >
                            {t('experiences.lifecycle.restore')}
                          </Button>
                          <Button
                            variant="destructive"
                            onClick={() => setPermanentExperienceId(selectedDetail.experience_id)}
                            disabled={restoreMutation.isPending}
                          >
                            {t('experiences.lifecycle.permanent')}
                          </Button>
                        </div>
                      )}
                    </ExperienceAiChatProvider>
                  ) : (
                    <div>
                      <h3 className="font-mono text-xs font-bold uppercase tracking-widest text-ink-soft">
                        {t('experiences.editor.background')}
                      </h3>
                      <p className="mt-2 whitespace-pre-wrap border-l-4 border-primary pl-4 text-sm leading-6">
                        {displayExperience.background || '—'}
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

export function ExperienceLibraryPage() {
  return (
    <ExperienceQueryProvider>
      <ExperienceLibraryContent />
    </ExperienceQueryProvider>
  );
}
