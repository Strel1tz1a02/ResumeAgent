'use client';

import { useEffect, useState } from 'react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import {
  createEvidence,
  deleteEvidence,
  patchEvidence,
  reorderEvidence,
  type ExperienceDetail,
} from '@/lib/api/experiences';
import { useTranslations } from '@/lib/i18n';

interface EvidenceListEditorProps {
  experience: ExperienceDetail;
  onMutated: (experience: ExperienceDetail) => void;
}

type EvidenceDraft = { action: string; result: string; metrics: string };

const toDraft = (item: ExperienceDetail['evidence_items'][number]): EvidenceDraft => ({
  action: item.action,
  result: item.result ?? '',
  metrics: item.metrics ?? '',
});

export function EvidenceListEditor({ experience, onMutated }: EvidenceListEditorProps) {
  const { t } = useTranslations();
  const [drafts, setDrafts] = useState<Record<number, EvidenceDraft>>({});
  const [newEvidence, setNewEvidence] = useState<EvidenceDraft>({
    action: '',
    result: '',
    metrics: '',
  });
  const [submitting, setSubmitting] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const archived = experience.status === 'archived';

  useEffect(() => {
    setDrafts(
      Object.fromEntries(experience.evidence_items.map((item) => [item.id, toDraft(item)]))
    );
  }, [experience.evidence_items]);

  const updateDraft = (id: number, key: keyof EvidenceDraft, value: string) => {
    setDrafts((current) => ({ ...current, [id]: { ...current[id], [key]: value } }));
  };

  const fail = (reason: unknown) =>
    setError(reason instanceof Error ? reason.message : t('experiences.evidence.error'));

  const saveNew = async () => {
    if (submitting || !newEvidence.action.trim()) return;
    setSubmitting('new');
    setError(null);
    try {
      const detail = await createEvidence(experience.experience_id, {
        action: newEvidence.action.trim(),
        result: newEvidence.result.trim() || null,
        metrics: newEvidence.metrics.trim() || null,
      });
      onMutated(detail);
      setNewEvidence({ action: '', result: '', metrics: '' });
    } catch (reason) {
      fail(reason);
    } finally {
      setSubmitting(null);
    }
  };

  const saveExisting = async (id: number) => {
    const draft = drafts[id];
    if (submitting || !draft?.action.trim()) return;
    setSubmitting(`save-${id}`);
    setError(null);
    try {
      onMutated(
        await patchEvidence(experience.experience_id, id, {
          action: draft.action.trim(),
          result: draft.result.trim() || null,
          metrics: draft.metrics.trim() || null,
        })
      );
    } catch (reason) {
      fail(reason);
    } finally {
      setSubmitting(null);
    }
  };

  const remove = async (id: number) => {
    if (submitting) return;
    setSubmitting(`delete-${id}`);
    setError(null);
    try {
      onMutated(await deleteEvidence(experience.experience_id, id));
    } catch (reason) {
      fail(reason);
    } finally {
      setSubmitting(null);
    }
  };

  const move = async (index: number, direction: -1 | 1) => {
    const nextIndex = index + direction;
    if (submitting || nextIndex < 0 || nextIndex >= experience.evidence_items.length) return;
    const ids = experience.evidence_items.map((item) => item.id);
    [ids[index], ids[nextIndex]] = [ids[nextIndex], ids[index]];
    setSubmitting(`move-${experience.evidence_items[index].id}`);
    setError(null);
    try {
      onMutated(await reorderEvidence(experience.experience_id, ids));
    } catch (reason) {
      fail(reason);
    } finally {
      setSubmitting(null);
    }
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
          disabled={archived || Boolean(submitting)}
        />
      </div>
      <div>
        <Label htmlFor={`${id}-result`}>{t('experiences.evidence.result')}</Label>
        <Input
          id={`${id}-result`}
          aria-label={`${t('experiences.evidence.result')} ${id}`}
          value={draft.result}
          onChange={(event) => change('result', event.target.value)}
          disabled={archived || Boolean(submitting)}
        />
      </div>
      <div>
        <Label htmlFor={`${id}-metrics`}>{t('experiences.evidence.metrics')}</Label>
        <Input
          id={`${id}-metrics`}
          aria-label={`${t('experiences.evidence.metrics')} ${id}`}
          value={draft.metrics}
          onChange={(event) => change('metrics', event.target.value)}
          disabled={archived || Boolean(submitting)}
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
                <Button
                  size="sm"
                  onClick={() => void saveExisting(item.id)}
                  disabled={Boolean(submitting)}
                >
                  {t('experiences.evidence.save')}
                </Button>
                <Button
                  size="sm"
                  variant="outline"
                  onClick={() => void move(index, -1)}
                  disabled={Boolean(submitting) || index === 0}
                >
                  {t('experiences.evidence.moveUp')}
                </Button>
                <Button
                  size="sm"
                  variant="outline"
                  onClick={() => void move(index, 1)}
                  disabled={Boolean(submitting) || index === experience.evidence_items.length - 1}
                >
                  {t('experiences.evidence.moveDown')}
                </Button>
                <Button
                  size="sm"
                  variant="destructive"
                  onClick={() => void remove(item.id)}
                  disabled={Boolean(submitting)}
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
            disabled={Boolean(submitting) || !newEvidence.action.trim()}
          >
            {t('experiences.evidence.add')}
          </Button>
        </div>
      )}
      {error && <p className="font-mono text-xs text-destructive">{error}</p>}
    </section>
  );
}
