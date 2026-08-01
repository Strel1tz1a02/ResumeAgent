'use client';

import { useEffect, useMemo, useRef, useState, type KeyboardEvent } from 'react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Textarea } from '@/components/ui/textarea';
import { FieldAiEntry } from './ai-chat/field-ai-entry';
import { useExperienceAiChat } from './ai-chat/use-experience-ai-chat';
import type {
  ExperienceDetail,
  ExperienceFieldState,
  ExperienceKind,
  ExperienceUpdate,
} from '@/lib/api/experiences';
import { useTranslations } from '@/lib/i18n';
import { usePatchExperienceMutation } from '@/lib/queries/experiences/mutations';

const kinds: ExperienceKind[] = [
  'work',
  'internship',
  'project',
  'research',
  'campus',
  'volunteer',
  'other',
];

interface ExperienceDraft {
  kind: ExperienceKind;
  title: string;
  organization: string;
  role: string;
  location: string;
  start_date: string;
  end_date: string;
  is_current: boolean;
  background: string;
  technologies: string;
  tags: string;
  notes: string;
}

type DraftKey = keyof ExperienceDraft;

const units: Record<DraftKey, DraftKey[]> = {
  kind: ['kind', 'title'],
  title: ['kind', 'title'],
  organization: ['organization'],
  role: ['role'],
  location: ['location'],
  start_date: ['start_date', 'end_date', 'is_current'],
  end_date: ['start_date', 'end_date', 'is_current'],
  is_current: ['start_date', 'end_date', 'is_current'],
  background: ['background'],
  technologies: ['technologies'],
  tags: ['tags'],
  notes: ['notes'],
};

interface ExperienceEditorProps {
  experience: ExperienceDetail;
  onDirtyChange: (dirty: boolean) => void;
  resetSignal: number;
  globalDirty: boolean;
  globalSaving: boolean;
  globalError: unknown;
  onGlobalSave: () => void;
  onGlobalDraftChange: (value: ExperienceUpdate, valid: boolean) => void;
}

function labels(value: string): string[] {
  const seen = new Set<string>();
  return value
    .split(',')
    .map((item) => item.trim())
    .filter((item) => {
      const key = item.toLocaleLowerCase();
      if (!key || seen.has(key)) return false;
      seen.add(key);
      return true;
    });
}

function draftFromExperience(experience: ExperienceDetail): ExperienceDraft {
  return {
    kind: experience.kind,
    title: experience.title,
    organization: experience.organization ?? '',
    role: experience.role ?? '',
    location: experience.location ?? '',
    start_date: experience.start_date ?? '',
    end_date: experience.end_date ?? '',
    is_current: experience.is_current,
    background: experience.background ?? '',
    technologies: experience.technologies.join(', '),
    tags: experience.tags.join(', '),
    notes: experience.notes ?? '',
  };
}

function normalizedValue(key: DraftKey, draft: ExperienceDraft): unknown {
  if (key === 'technologies' || key === 'tags') return labels(draft[key]);
  if (key === 'end_date' && draft.is_current) return null;
  if (key === 'kind' || key === 'is_current') return draft[key];
  const value = draft[key].trim();
  return key === 'title' ? value : value || null;
}

function sameField(key: DraftKey, left: ExperienceDraft, right: ExperienceDraft): boolean {
  return JSON.stringify(normalizedValue(key, left)) === JSON.stringify(normalizedValue(key, right));
}

function payloadFor(
  draft: ExperienceDraft,
  keys: DraftKey[],
  experience: ExperienceDetail
): ExperienceUpdate {
  const payload: Record<string, unknown> = {};
  const revisions: Record<string, number> = {};
  for (const key of keys) {
    payload[key] = normalizedValue(key, draft);
    const state = (experience.field_states ?? []).find(
      (item) => item.key === key && item.ref_id === null
    );
    if (state) revisions[key] = state.revision;
  }
  payload.expected_field_revisions = revisions;
  return payload as ExperienceUpdate;
}

export function ExperienceEditor({
  experience,
  onDirtyChange,
  resetSignal,
  globalDirty,
  globalSaving,
  globalError,
  onGlobalSave,
  onGlobalDraftChange,
}: ExperienceEditorProps) {
  const { t } = useTranslations();
  const chat = useExperienceAiChat();
  const [draft, setDraft] = useState(() => draftFromExperience(experience));
  const [baseline, setBaseline] = useState(() => draftFromExperience(experience));
  const patchMutation = usePatchExperienceMutation(experience.experience_id);
  const loadedExperienceIdRef = useRef(experience.experience_id);
  const loadedResetSignalRef = useRef(resetSignal);
  const archived = experience.status === 'archived';
  const saving = patchMutation.isPending;
  const dirtyKeys = useMemo(
    () => (Object.keys(draft) as DraftKey[]).filter((key) => !sameField(key, draft, baseline)),
    [baseline, draft]
  );
  const dirty = dirtyKeys.length > 0;

  useEffect(() => {
    const server = draftFromExperience(experience);
    const fullReset =
      loadedExperienceIdRef.current !== experience.experience_id ||
      loadedResetSignalRef.current !== resetSignal;
    if (fullReset) {
      setDraft(server);
      setBaseline(server);
      patchMutation.reset();
    } else {
      const appliedTarget = chat.lastBusinessEvent?.data.target as
        | { key?: string; ref_id?: number | null }
        | undefined;
      setDraft((current) => {
        const next = { ...current };
        for (const key of Object.keys(server) as DraftKey[]) {
          if (
            sameField(key, current, baseline) ||
            (appliedTarget?.ref_id == null && appliedTarget?.key === key)
          ) {
            next[key] = server[key] as never;
          }
        }
        return next;
      });
      setBaseline(server);
    }
    loadedExperienceIdRef.current = experience.experience_id;
    loadedResetSignalRef.current = resetSignal;
    // baseline is deliberately the previous server snapshot for the merge above.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [chat.lastBusinessEvent, experience, resetSignal]);

  useEffect(() => onDirtyChange(dirty), [dirty, onDirtyChange]);

  useEffect(() => {
    onGlobalDraftChange(
      payloadFor(draft, Object.keys(draft) as DraftKey[], experience),
      Boolean(draft.title.trim())
    );
  }, [draft, experience, onGlobalDraftChange]);

  const change = <K extends DraftKey>(key: K, value: ExperienceDraft[K]) => {
    setDraft((current) => ({ ...current, [key]: value }));
  };

  const stateFor = (key: DraftKey): ExperienceFieldState | undefined =>
    (experience.field_states ?? []).find((item) => item.key === key && item.ref_id === null);

  const unitDirty = (key: DraftKey) => units[key].some((item) => dirtyKeys.includes(item));
  const saveKeys = (keys: DraftKey[]) => {
    if (saving || archived || (keys.includes('title') && !draft.title.trim())) return;
    patchMutation.mutate(payloadFor(draft, keys, experience));
  };
  const saveUnit = (key: DraftKey) => saveKeys(units[key]);
  const locked = (key: DraftKey) => chat.isTargetLocked({ key, ref_id: null });
  const entryProps = (key: DraftKey) => ({
    target: { key, ref_id: null },
    state: stateFor(key),
    dirty: unitDirty(key),
    onSave: () => saveUnit(key),
    saveDisabled: saving || globalSaving || archived || chat.phase === 'approval',
  });
  const stopTextareaEnter = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === 'Enter') event.stopPropagation();
  };

  return (
    <section className="space-y-4" aria-label={t('experiences.editor.title')}>
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h3 className="font-mono text-xs font-bold uppercase tracking-widest text-ink-soft">
          {t('experiences.editor.title')}
        </h3>
        {!archived && (
          <Button
            size="sm"
            onClick={onGlobalSave}
            disabled={
              saving ||
              globalSaving ||
              !globalDirty ||
              !draft.title.trim() ||
              chat.phase === 'approval'
            }
          >
            {globalSaving ? t('experiences.editor.saving') : t('experiences.editor.save')}
          </Button>
        )}
      </div>
      <div className="grid gap-3 md:grid-cols-2">
        <FieldAiEntry {...entryProps('title')}>
          <Label htmlFor="experience-title">{t('experiences.editor.titleField')}</Label>
          <Input
            id="experience-title"
            value={draft.title}
            onChange={(event) => change('title', event.target.value)}
            disabled={archived || saving || globalSaving || locked('title')}
          />
        </FieldAiEntry>
        <FieldAiEntry {...entryProps('kind')}>
          <Label htmlFor="experience-kind">{t('experiences.editor.kind')}</Label>
          <select
            id="experience-kind"
            value={draft.kind}
            onChange={(event) => change('kind', event.target.value as ExperienceKind)}
            disabled={archived || saving || globalSaving || locked('kind')}
            className="flex h-10 w-full border border-black bg-transparent px-3 py-2 text-sm"
          >
            {kinds.map((kind) => (
              <option key={kind} value={kind}>
                {t(`experiences.kind.${kind}`)}
              </option>
            ))}
          </select>
        </FieldAiEntry>
        {(['organization', 'role', 'location', 'start_date'] as const).map((field) => (
          <FieldAiEntry key={field} {...entryProps(field)}>
            <Label htmlFor={`experience-${field}`}>{t(`experiences.editor.${field}`)}</Label>
            <Input
              id={`experience-${field}`}
              type={field === 'start_date' ? 'month' : 'text'}
              value={draft[field]}
              onChange={(event) => change(field, event.target.value)}
              disabled={archived || saving || globalSaving || locked(field)}
            />
          </FieldAiEntry>
        ))}
        {!draft.is_current && (
          <FieldAiEntry {...entryProps('end_date')}>
            <Label htmlFor="experience-end-date">{t('experiences.editor.end_date')}</Label>
            <Input
              id="experience-end-date"
              type="month"
              value={draft.end_date}
              onChange={(event) => change('end_date', event.target.value)}
              disabled={archived || saving || globalSaving || locked('end_date')}
            />
          </FieldAiEntry>
        )}
        <FieldAiEntry {...entryProps('is_current')}>
          <label className="flex h-10 items-center gap-2 text-sm">
            <input
              type="checkbox"
              checked={draft.is_current}
              onChange={(event) => {
                const value = event.target.checked;
                setDraft((current) => ({
                  ...current,
                  is_current: value,
                  end_date: value ? '' : current.end_date,
                }));
              }}
              disabled={archived || saving || globalSaving || locked('is_current')}
            />
            {t('experiences.editor.is_current')}
          </label>
        </FieldAiEntry>
        {(['technologies', 'tags'] as const).map((field) => (
          <FieldAiEntry key={field} {...entryProps(field)}>
            <Label htmlFor={`experience-${field}`}>{t(`experiences.editor.${field}`)}</Label>
            <Input
              id={`experience-${field}`}
              value={draft[field]}
              onChange={(event) => change(field, event.target.value)}
              disabled={archived || saving || globalSaving || locked(field)}
            />
          </FieldAiEntry>
        ))}
      </div>
      {(['background', 'notes'] as const).map((field) => (
        <FieldAiEntry key={field} {...entryProps(field)}>
          <Label htmlFor={`experience-${field}`}>{t(`experiences.editor.${field}`)}</Label>
          <Textarea
            id={`experience-${field}`}
            value={draft[field]}
            onChange={(event) => change(field, event.target.value)}
            onKeyDown={stopTextareaEnter}
            disabled={archived || saving || globalSaving || locked(field)}
            rows={3}
          />
        </FieldAiEntry>
      ))}
      {patchMutation.error && (
        <p className="font-mono text-xs text-destructive">
          {patchMutation.error instanceof Error
            ? patchMutation.error.message
            : t('experiences.editor.error')}
        </p>
      )}
      {Boolean(globalError) && (
        <p className="font-mono text-xs text-destructive">
          {globalError instanceof Error ? globalError.message : t('experiences.editor.error')}
        </p>
      )}
    </section>
  );
}
