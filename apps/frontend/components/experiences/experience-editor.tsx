'use client';

import { useEffect, useMemo, useRef, useState, type KeyboardEvent } from 'react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Textarea } from '@/components/ui/textarea';
import {
  patchExperience,
  type ExperienceDetail,
  type ExperienceKind,
  type ExperienceUpdate,
} from '@/lib/api/experiences';
import { useTranslations } from '@/lib/i18n';

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
  raw_input: string;
  background: string;
  technologies: string;
  tags: string;
  notes: string;
}

interface ExperienceEditorProps {
  experience: ExperienceDetail;
  onSaved: (experience: ExperienceDetail) => void;
  onMutationStart: (experienceId: number) => void;
  onDirtyChange: (dirty: boolean) => void;
  resetSignal: number;
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
    raw_input: experience.raw_input,
    background: experience.background ?? '',
    technologies: experience.technologies.join(', '),
    tags: experience.tags.join(', '),
    notes: experience.notes ?? '',
  };
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

function normalizedDraft(draft: ExperienceDraft): string {
  return JSON.stringify({
    ...draft,
    title: draft.title.trim(),
    organization: draft.organization.trim(),
    role: draft.role.trim(),
    location: draft.location.trim(),
    start_date: draft.start_date.trim(),
    end_date: draft.is_current ? '' : draft.end_date.trim(),
    raw_input: draft.raw_input.trim(),
    background: draft.background.trim(),
    technologies: labels(draft.technologies),
    tags: labels(draft.tags),
    notes: draft.notes.trim(),
  });
}

function explicitUpdate(draft: ExperienceDraft, expectedUpdatedAt: string): ExperienceUpdate {
  return {
    kind: draft.kind,
    title: draft.title.trim(),
    organization: draft.organization.trim() || null,
    role: draft.role.trim() || null,
    location: draft.location.trim() || null,
    start_date: draft.start_date.trim() || null,
    end_date: draft.is_current ? null : draft.end_date.trim() || null,
    is_current: draft.is_current,
    raw_input: draft.raw_input,
    background: draft.background.trim() || null,
    technologies: labels(draft.technologies),
    tags: labels(draft.tags),
    notes: draft.notes.trim() || null,
    expected_updated_at: expectedUpdatedAt,
  };
}

export function ExperienceEditor({
  experience,
  onSaved,
  onMutationStart,
  onDirtyChange,
  resetSignal,
}: ExperienceEditorProps) {
  const { t } = useTranslations();
  const [draft, setDraft] = useState(() => draftFromExperience(experience));
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const loadedExperienceIdRef = useRef(experience.experience_id);
  const loadedResetSignalRef = useRef(resetSignal);
  const saveGenerationRef = useRef(0);
  const baseline = useMemo(() => normalizedDraft(draftFromExperience(experience)), [experience]);
  const dirty = normalizedDraft(draft) !== baseline;
  const archived = experience.status === 'archived';

  useEffect(() => {
    if (
      loadedExperienceIdRef.current === experience.experience_id &&
      loadedResetSignalRef.current === resetSignal
    ) {
      return;
    }
    setDraft(draftFromExperience(experience));
    saveGenerationRef.current += 1;
    setSaving(false);
    setError(null);
    loadedExperienceIdRef.current = experience.experience_id;
    loadedResetSignalRef.current = resetSignal;
  }, [experience, resetSignal]);

  useEffect(() => {
    onDirtyChange(dirty);
  }, [dirty, onDirtyChange]);

  const change = <K extends keyof ExperienceDraft>(key: K, value: ExperienceDraft[K]) => {
    setDraft((current) => ({ ...current, [key]: value }));
  };

  const save = async () => {
    if (saving || archived || !draft.title.trim()) return;
    const targetId = experience.experience_id;
    const generation = ++saveGenerationRef.current;
    setSaving(true);
    setError(null);
    onMutationStart(targetId);
    try {
      const detail = await patchExperience(targetId, explicitUpdate(draft, experience.updated_at));
      onSaved(detail);
      if (loadedExperienceIdRef.current === targetId && generation === saveGenerationRef.current) {
        setDraft(draftFromExperience(detail));
      }
    } catch (reason) {
      if (loadedExperienceIdRef.current === targetId && generation === saveGenerationRef.current) {
        setError(reason instanceof Error ? reason.message : t('experiences.editor.error'));
      }
    } finally {
      if (loadedExperienceIdRef.current === targetId && generation === saveGenerationRef.current) {
        setSaving(false);
      }
    }
  };

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
            onClick={() => void save()}
            disabled={saving || !dirty || !draft.title.trim()}
          >
            {saving ? t('experiences.editor.saving') : t('experiences.editor.save')}
          </Button>
        )}
      </div>
      <div className="grid gap-3 md:grid-cols-2">
        <div>
          <Label htmlFor="experience-title">{t('experiences.editor.titleField')}</Label>
          <Input
            id="experience-title"
            aria-label={t('experiences.editor.titleField')}
            value={draft.title}
            onChange={(event) => change('title', event.target.value)}
            disabled={archived || saving}
          />
        </div>
        <div>
          <Label htmlFor="experience-kind">{t('experiences.editor.kind')}</Label>
          <select
            id="experience-kind"
            aria-label={t('experiences.editor.kind')}
            value={draft.kind}
            onChange={(event) => change('kind', event.target.value as ExperienceKind)}
            disabled={archived || saving}
            className="flex h-10 w-full border border-black bg-transparent px-3 py-2 text-sm"
          >
            {kinds.map((kind) => (
              <option key={kind} value={kind}>
                {t(`experiences.kind.${kind}`)}
              </option>
            ))}
          </select>
        </div>
        {(['organization', 'role', 'location', 'start_date'] as const).map((field) => (
          <div key={field}>
            <Label htmlFor={`experience-${field}`}>{t(`experiences.editor.${field}`)}</Label>
            <Input
              id={`experience-${field}`}
              aria-label={t(`experiences.editor.${field}`)}
              type={field === 'start_date' ? 'month' : 'text'}
              value={draft[field]}
              onChange={(event) => change(field, event.target.value)}
              disabled={archived || saving}
            />
          </div>
        ))}
        {!draft.is_current && (
          <div>
            <Label htmlFor="experience-end-date">{t('experiences.editor.end_date')}</Label>
            <Input
              id="experience-end-date"
              aria-label={t('experiences.editor.end_date')}
              type="month"
              value={draft.end_date}
              onChange={(event) => change('end_date', event.target.value)}
              disabled={archived || saving}
            />
          </div>
        )}
        <label className="flex items-center gap-2 self-end pb-2 text-sm">
          <input
            aria-label={t('experiences.editor.is_current')}
            type="checkbox"
            checked={draft.is_current}
            onChange={(event) =>
              setDraft((current) => ({
                ...current,
                is_current: event.target.checked,
                end_date: event.target.checked ? '' : current.end_date,
              }))
            }
            disabled={archived || saving}
          />
          {t('experiences.editor.is_current')}
        </label>
        <div>
          <Label htmlFor="experience-technologies">{t('experiences.editor.technologies')}</Label>
          <Input
            id="experience-technologies"
            aria-label={t('experiences.editor.technologies')}
            value={draft.technologies}
            onChange={(event) => change('technologies', event.target.value)}
            disabled={archived || saving}
          />
        </div>
        <div>
          <Label htmlFor="experience-tags">{t('experiences.editor.tags')}</Label>
          <Input
            id="experience-tags"
            aria-label={t('experiences.editor.tags')}
            value={draft.tags}
            onChange={(event) => change('tags', event.target.value)}
            disabled={archived || saving}
          />
        </div>
      </div>
      {(['raw_input', 'background', 'notes'] as const).map((field) => (
        <div key={field}>
          <Label htmlFor={`experience-${field}`}>{t(`experiences.editor.${field}`)}</Label>
          <Textarea
            id={`experience-${field}`}
            aria-label={t(`experiences.editor.${field}`)}
            value={draft[field]}
            onChange={(event) => change(field, event.target.value)}
            onKeyDown={stopTextareaEnter}
            disabled={archived || saving}
            rows={field === 'raw_input' ? 5 : 3}
          />
        </div>
      ))}
      {error && <p className="font-mono text-xs text-destructive">{error}</p>}
    </section>
  );
}
