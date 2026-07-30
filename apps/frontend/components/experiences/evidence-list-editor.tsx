'use client';

import { useEffect, useRef, useState } from 'react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import type { ExperienceDetail } from '@/lib/api/experiences';
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

export function EvidenceListEditor({
  experience,
  onDirtyChange,
  resetSignal,
}: EvidenceListEditorProps) {
  const { t } = useTranslations();
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
      for (const item of experience.evidence_items) {
        const serverDraft = serverBaseline[item.id];
        const localDraft = current.drafts[item.id];
        const previousBaseline = current.baseline[item.id];
        next[item.id] =
          localDraft && previousBaseline && !sameDraft(localDraft, previousBaseline)
            ? localDraft
            : serverDraft;
      }
      return { drafts: next, baseline: serverBaseline };
    });
    if (fullReset) {
      setNewEvidence({ action: '', result: '', metrics: '' });
    }
    loadedExperienceIdRef.current = experience.experience_id;
    loadedResetSignalRef.current = resetSignal;
  }, [experience.experience_id, experience.evidence_items, resetSignal]);

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
      },
    });
  };

  const remove = (id: number) => {
    if (submitting) return;
    deleteMutation.mutate(id);
  };

  const move = (index: number, direction: -1 | 1) => {
    const nextIndex = index + direction;
    if (submitting || nextIndex < 0 || nextIndex >= experience.evidence_items.length) return;
    const ids = experience.evidence_items.map((item) => item.id);
    [ids[index], ids[nextIndex]] = [ids[nextIndex], ids[index]];
    reorderMutation.mutate(ids);
  };

  const fields = (
    draft: EvidenceDraft,
    change: (key: keyof EvidenceDraft, value: string) => void,
    id: string
  ) => (
    <div className="grid gap-3 md:grid-cols-3">
      <div>
        <Label htmlFor={`${id}-action`}>{t('experiences.evidence.action')}</Label>
        <Input
          id={`${id}-action`}
          aria-label={`${t('experiences.evidence.action')} ${id}`}
          value={draft.action}
          onChange={(event) => change('action', event.target.value)}
          disabled={archived || submitting}
        />
      </div>
      <div>
        <Label htmlFor={`${id}-result`}>{t('experiences.evidence.result')}</Label>
        <Input
          id={`${id}-result`}
          aria-label={`${t('experiences.evidence.result')} ${id}`}
          value={draft.result}
          onChange={(event) => change('result', event.target.value)}
          disabled={archived || submitting}
        />
      </div>
      <div>
        <Label htmlFor={`${id}-metrics`}>{t('experiences.evidence.metrics')}</Label>
        <Input
          id={`${id}-metrics`}
          aria-label={`${t('experiences.evidence.metrics')} ${id}`}
          value={draft.metrics}
          onChange={(event) => change('metrics', event.target.value)}
          disabled={archived || submitting}
        />
      </div>
    </div>
  );

  return (
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
            {fields(draft, (key, value) => updateDraft(item.id, key, value), String(item.id))}
            {!archived && (
              <div className="mt-3 flex flex-wrap gap-2">
                <Button size="sm" onClick={() => void saveExisting(item.id)} disabled={submitting}>
                  {t('experiences.evidence.save')}
                </Button>
                <Button
                  size="sm"
                  variant="outline"
                  onClick={() => void move(index, -1)}
                  disabled={submitting || index === 0}
                >
                  {t('experiences.evidence.moveUp')}
                </Button>
                <Button
                  size="sm"
                  variant="outline"
                  onClick={() => void move(index, 1)}
                  disabled={submitting || index === experience.evidence_items.length - 1}
                >
                  {t('experiences.evidence.moveDown')}
                </Button>
                <Button
                  size="sm"
                  variant="destructive"
                  onClick={() => void remove(item.id)}
                  disabled={submitting}
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
            'new'
          )}
          <Button
            className="mt-3"
            size="sm"
            onClick={() => void saveNew()}
            disabled={submitting || !newEvidence.action.trim()}
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
  );
}
