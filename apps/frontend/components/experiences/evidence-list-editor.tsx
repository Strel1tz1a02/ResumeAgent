'use client';

import { useEffect, useRef, useState } from 'react';
import { Button } from '@/components/ui/button';
import { Label } from '@/components/ui/label';
import { AutoResizeTextarea } from './auto-resize-textarea';
import { FieldAiEntry } from './ai-chat/field-ai-entry';
import { useExperienceAiChat } from './ai-chat/use-experience-ai-chat';
import type { ExperienceDetail, ExperienceGlobalSave } from '@/lib/api/experiences';
import { useTranslations } from '@/lib/i18n';
import {
  useCreateEvidenceMutation,
  useDeleteEvidenceMutation,
  usePatchEvidenceMutation,
  useReorderEvidenceMutation,
} from '@/lib/queries/experiences/mutations';

interface EvidenceListEditorProps {
  experience: ExperienceDetail;
  onDirtyChange: (dirty: boolean) => void;
  resetSignal: number;
  globalSaving: boolean;
  onGlobalDraftChange: (
    value: Pick<
      ExperienceGlobalSave,
      'evidence_items' | 'new_evidence' | 'expected_collection_revision'
    >,
    valid: boolean
  ) => void;
}

type EvidenceDraft = { action: string; result: string; metrics: string };
type EvidenceEditorState = {
  drafts: Record<number, EvidenceDraft>;
  baseline: Record<number, EvidenceDraft>;
};

const toDraft = (item: ExperienceDetail['evidence_items'][number]): EvidenceDraft => ({
  action: item.action,
  result: item.result ?? '',
  metrics: item.metrics ?? '',
});

const sameDraft = (left: EvidenceDraft, right: EvidenceDraft) =>
  left.action === right.action && left.result === right.result && left.metrics === right.metrics;

const evidenceChatScope = { field: 'evidence' } as const;

export function EvidenceListEditor({
  experience,
  onDirtyChange,
  resetSignal,
  globalSaving,
  onGlobalDraftChange,
}: EvidenceListEditorProps) {
  const { t } = useTranslations();
  const chat = useExperienceAiChat();
  const [editorState, setEditorState] = useState<EvidenceEditorState>({
    drafts: {},
    baseline: {},
  });
  const { drafts, baseline } = editorState;
  const [newEvidence, setNewEvidence] = useState<EvidenceDraft>({
    action: '',
    result: '',
    metrics: '',
  });
  const createMutation = useCreateEvidenceMutation(experience.experience_id);
  const patchMutation = usePatchEvidenceMutation(experience.experience_id);
  const deleteMutation = useDeleteEvidenceMutation(experience.experience_id);
  const reorderMutation = useReorderEvidenceMutation(experience.experience_id);
  const loadedExperienceIdRef = useRef(experience.experience_id);
  const loadedResetSignalRef = useRef(resetSignal);
  const archived = experience.status === 'archived';
  const collectionRevision =
    (experience.field_states ?? []).find(
      (state) => state.key === 'evidence_new' && state.ref_id === null
    )?.revision ?? 0;
  const evidenceRevision = (evidenceId: number) =>
    (experience.field_states ?? []).find(
      (state) => state.key === 'action' && state.ref_id === evidenceId
    )?.revision ?? 0;
  const submitting =
    createMutation.isPending ||
    patchMutation.isPending ||
    deleteMutation.isPending ||
    reorderMutation.isPending;
  const error =
    createMutation.error ?? patchMutation.error ?? deleteMutation.error ?? reorderMutation.error;

  useEffect(() => {
    const serverBaseline = Object.fromEntries(
      experience.evidence_items.map((item) => [item.id, toDraft(item)])
    );
    const fullReset =
      loadedExperienceIdRef.current !== experience.experience_id ||
      loadedResetSignalRef.current !== resetSignal;
    setEditorState((current) => {
      if (fullReset) return { drafts: serverBaseline, baseline: serverBaseline };
      const next: Record<number, EvidenceDraft> = {};
      const appliedScope = chat.lastBusinessEvent?.data.scope as
        | {
            field?: string;
            evidence_id?: number | null;
          }
        | undefined;
      for (const item of experience.evidence_items) {
        const serverDraft = serverBaseline[item.id];
        const localDraft = current.drafts[item.id];
        const previousBaseline = current.baseline[item.id];
        if (!localDraft || !previousBaseline) {
          next[item.id] = serverDraft;
          continue;
        }
        const merged = { ...localDraft };
        for (const key of ['action', 'result', 'metrics'] as const) {
          if (
            localDraft[key] === previousBaseline[key] ||
            (appliedScope?.evidence_id === item.id &&
              (appliedScope.field === 'evidence' || appliedScope.field === key))
          ) {
            merged[key] = serverDraft[key];
          }
        }
        next[item.id] = merged;
      }
      return { drafts: next, baseline: serverBaseline };
    });
    if (fullReset) {
      setNewEvidence({ action: '', result: '', metrics: '' });
    } else if (
      chat.lastBusinessEvent?.data.created === true &&
      (chat.lastBusinessEvent.data.scope as { field?: string } | undefined)?.field === 'evidence'
    ) {
      setNewEvidence({ action: '', result: '', metrics: '' });
    }
    loadedExperienceIdRef.current = experience.experience_id;
    loadedResetSignalRef.current = resetSignal;
  }, [chat.lastBusinessEvent, experience.experience_id, experience.evidence_items, resetSignal]);

  const dirty =
    newEvidence.action !== '' ||
    newEvidence.result !== '' ||
    newEvidence.metrics !== '' ||
    experience.evidence_items.some((item) => {
      const draft = drafts[item.id];
      const baselineDraft = baseline[item.id] ?? toDraft(item);
      return Boolean(draft && !sameDraft(draft, baselineDraft));
    });

  useEffect(() => {
    onDirtyChange(dirty);
  }, [dirty, onDirtyChange]);

  useEffect(() => {
    const hasNewEvidence = Boolean(newEvidence.action || newEvidence.result || newEvidence.metrics);
    onGlobalDraftChange(
      {
        evidence_items: experience.evidence_items.map((item) => {
          const draft = drafts[item.id] ?? toDraft(item);
          return {
            evidence_id: item.id,
            action: draft.action.trim(),
            result: draft.result.trim() || null,
            metrics: draft.metrics.trim() || null,
            expected_revision:
              (experience.field_states ?? []).find(
                (state) => state.key === 'action' && state.ref_id === item.id
              )?.revision ?? 0,
          };
        }),
        new_evidence: hasNewEvidence
          ? {
              action: newEvidence.action.trim(),
              result: newEvidence.result.trim() || null,
              metrics: newEvidence.metrics.trim() || null,
            }
          : null,
        expected_collection_revision:
          (experience.field_states ?? []).find(
            (state) => state.key === 'evidence_new' && state.ref_id === null
          )?.revision ?? 0,
      },
      !hasNewEvidence || Boolean(newEvidence.action.trim())
    );
  }, [
    drafts,
    experience.evidence_items,
    experience.field_states,
    newEvidence,
    onGlobalDraftChange,
  ]);

  const updateDraft = (id: number, key: keyof EvidenceDraft, value: string) => {
    setEditorState((current) => ({
      ...current,
      drafts: {
        ...current.drafts,
        [id]: { ...current.drafts[id], [key]: value },
      },
    }));
  };

  const saveNew = () => {
    if (submitting || !newEvidence.action.trim()) return;
    createMutation.mutate(
      {
        action: newEvidence.action.trim(),
        result: newEvidence.result.trim() || null,
        metrics: newEvidence.metrics.trim() || null,
        expected_collection_revision: collectionRevision,
      },
      { onSuccess: () => setNewEvidence({ action: '', result: '', metrics: '' }) }
    );
  };

  const saveExisting = (id: number) => {
    const draft = drafts[id];
    if (submitting || !draft?.action.trim()) return;
    patchMutation.mutate({
      evidenceId: id,
      payload: {
        action: draft.action.trim(),
        result: draft.result.trim() || null,
        metrics: draft.metrics.trim() || null,
        expected_revision: evidenceRevision(id),
      },
    });
  };

  const remove = (id: number) => {
    if (submitting) return;
    deleteMutation.mutate({
      evidenceId: id,
      expectedRevision: evidenceRevision(id),
      expectedCollectionRevision: collectionRevision,
    });
  };

  const move = (index: number, direction: -1 | 1) => {
    const nextIndex = index + direction;
    if (submitting || nextIndex < 0 || nextIndex >= experience.evidence_items.length) return;
    const ids = experience.evidence_items.map((item) => item.id);
    [ids[index], ids[nextIndex]] = [ids[nextIndex], ids[index]];
    reorderMutation.mutate({
      evidenceIds: ids,
      expectedCollectionRevision: collectionRevision,
    });
  };

  const fields = (
    draft: EvidenceDraft,
    change: (key: keyof EvidenceDraft, value: string) => void,
    id: string,
    evidenceId: number | null,
    dirty: boolean,
    onSave?: () => void
  ) => (
    <div className="space-y-4">
      {(['action', 'result', 'metrics'] as const).map((key) => {
        const itemScope = { field: 'evidence', evidence_id: evidenceId };
        return (
          <FieldAiEntry
            key={key}
            scope={evidenceChatScope}
            showAiStart={false}
            state={(experience.field_states ?? []).find(
              (state) =>
                state.key === (evidenceId === null ? 'evidence_new' : key) &&
                state.ref_id === evidenceId
            )}
            dirty={dirty}
            onSave={onSave}
            saveDisabled={submitting || globalSaving || chat.isScopeLocked(itemScope)}
          >
            <Label htmlFor={`${id}-${key}`}>{t(`experiences.evidence.${key}`)}</Label>
            <AutoResizeTextarea
              id={`${id}-${key}`}
              aria-label={`${t(`experiences.evidence.${key}`)} ${id}`}
              value={draft[key]}
              onChange={(event) => change(key, event.target.value)}
              disabled={archived || submitting || globalSaving || chat.isScopeLocked(itemScope)}
              minRows={key === 'action' ? 4 : key === 'result' ? 3 : 2}
            />
          </FieldAiEntry>
        );
      })}
    </div>
  );

  const collectionState = (experience.field_states ?? []).find(
    (state) => state.key === 'evidence_new' && state.ref_id === null
  );

  return (
    <FieldAiEntry scope={evidenceChatScope} state={collectionState}>
      <section className="space-y-4" aria-label={t('experiences.evidence.title')}>
        <h3 className="font-mono text-xs font-bold uppercase tracking-widest text-ink-soft">
          {t('experiences.evidence.title')}
        </h3>
        {experience.evidence_items.map((item, index) => {
          const draft = drafts[item.id] ?? toDraft(item);
          return (
            <article
              key={item.id}
              className="border border-black p-4"
              aria-label={`${t('experiences.evidence.card')} ${index + 1}`}
            >
              {fields(
                draft,
                (key, value) => updateDraft(item.id, key, value),
                String(item.id),
                item.id,
                !sameDraft(draft, baseline[item.id] ?? toDraft(item)),
                () => saveExisting(item.id)
              )}
              {!archived && (
                <div className="mt-3 flex flex-wrap gap-2">
                  <Button
                    size="sm"
                    variant="outline"
                    onClick={() => void move(index, -1)}
                    disabled={globalSaving || submitting || index === 0}
                  >
                    {t('experiences.evidence.moveUp')}
                  </Button>
                  <Button
                    size="sm"
                    variant="outline"
                    onClick={() => void move(index, 1)}
                    disabled={
                      globalSaving || submitting || index === experience.evidence_items.length - 1
                    }
                  >
                    {t('experiences.evidence.moveDown')}
                  </Button>
                  <Button
                    size="sm"
                    variant="destructive"
                    onClick={() => void remove(item.id)}
                    disabled={globalSaving || submitting}
                  >
                    {t('experiences.evidence.delete')}
                  </Button>
                </div>
              )}
            </article>
          );
        })}
        {!archived && (
          <div className="border border-dashed border-black p-4">
            {fields(
              newEvidence,
              (key, value) => setNewEvidence((current) => ({ ...current, [key]: value })),
              'new',
              null,
              Boolean(newEvidence.action || newEvidence.result || newEvidence.metrics)
            )}
            <Button
              className="mt-3"
              size="sm"
              onClick={() => void saveNew()}
              disabled={globalSaving || submitting || !newEvidence.action.trim()}
            >
              {t('experiences.evidence.add')}
            </Button>
          </div>
        )}
        {error && (
          <p className="font-mono text-xs text-destructive">
            {error instanceof Error ? error.message : t('experiences.evidence.error')}
          </p>
        )}
      </section>
    </FieldAiEntry>
  );
}
