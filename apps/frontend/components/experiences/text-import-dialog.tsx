'use client';

import React, { useEffect, useState } from 'react';
import Loader2 from 'lucide-react/dist/esm/icons/loader-2';
import Plus from 'lucide-react/dist/esm/icons/plus';
import Trash2 from 'lucide-react/dist/esm/icons/trash-2';
import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Textarea } from '@/components/ui/textarea';
import type {
  EvidenceCreate,
  ExperienceCreate,
  ExperienceDetail,
  ExperienceGlobalSave,
  ExperienceKind,
} from '@/lib/api/experiences';
import { useTranslations } from '@/lib/i18n';
import {
  usePreviewExperienceImportMutation,
  useSaveExperienceMutation,
} from '@/lib/queries/experiences/mutations';

const kinds: ExperienceKind[] = [
  'work',
  'internship',
  'project',
  'research',
  'campus',
  'volunteer',
  'other',
];

interface TextImportDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onImported: (experience: ExperienceDetail) => void;
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

export function TextImportDialog({ open, onOpenChange, onImported }: TextImportDialogProps) {
  const { t } = useTranslations();
  const [text, setText] = useState('');
  const [draft, setDraft] = useState<ExperienceGlobalSave | null>(null);
  const previewMutation = usePreviewExperienceImportMutation();
  const saveMutation = useSaveExperienceMutation();
  const resetPreview = previewMutation.reset;
  const resetSave = saveMutation.reset;
  const busy = previewMutation.isPending || saveMutation.isPending;

  useEffect(() => {
    if (!open) {
      setText('');
      setDraft(null);
      resetPreview();
      resetSave();
    }
  }, [open, resetPreview, resetSave]);

  const handlePreview = async () => {
    if (!text.trim()) return;
    try {
      setDraft(await previewMutation.mutateAsync(text));
    } catch {
      // 当前步骤下方展示解析错误，并保留原文供用户重试。
    }
  };

  const handleSave = async () => {
    if (!draft) return;
    try {
      const experience = await saveMutation.mutateAsync(draft);
      onImported(experience);
      onOpenChange(false);
    } catch {
      // 当前步骤下方展示保存错误，并保留可编辑草稿。
    }
  };

  const updateExperience = <K extends keyof ExperienceCreate>(
    key: K,
    value: ExperienceCreate[K]
  ) => {
    setDraft((current) =>
      current ? { ...current, experience: { ...current.experience, [key]: value } } : current
    );
  };

  const updateEvidence = (index: number, patch: Partial<EvidenceCreate>) => {
    setDraft((current) => {
      if (!current) return current;
      return {
        ...current,
        evidence_items: current.evidence_items.map((item, itemIndex) =>
          itemIndex === index ? { ...item, ...patch } : item
        ),
      };
    });
  };

  const addEvidence = () => {
    setDraft((current) =>
      current
        ? {
            ...current,
            evidence_items: [
              ...current.evidence_items,
              { background: null, action: '', result: null },
            ],
          }
        : current
    );
  };

  const removeEvidence = (index: number) => {
    setDraft((current) =>
      current
        ? {
            ...current,
            evidence_items: current.evidence_items.filter(
              (_item, itemIndex) => itemIndex !== index
            ),
          }
        : current
    );
  };

  const handleTextKeyDown = (event: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === 'Enter') event.stopPropagation();
  };

  const handleOpenChange = (nextOpen: boolean) => {
    if (!nextOpen && busy) return;
    onOpenChange(nextOpen);
  };

  const backToText = () => {
    setDraft(null);
    saveMutation.reset();
  };

  const evidenceValid = draft?.evidence_items.every((item) => item.action.trim()) ?? false;
  const error = draft ? saveMutation.error : previewMutation.error;

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogContent className="max-h-[90vh] max-w-4xl overflow-y-auto p-6">
        <DialogHeader>
          <DialogTitle>{t('experiences.import.title')}</DialogTitle>
          <DialogDescription>
            {draft
              ? t('experiences.import.previewDescription')
              : t('experiences.import.description')}
          </DialogDescription>
        </DialogHeader>

        {!draft ? (
          <div className="mt-5 space-y-2">
            <Label htmlFor="experience-import-text">{t('experiences.import.text')}</Label>
            <Textarea
              id="experience-import-text"
              value={text}
              onChange={(event) => setText(event.target.value)}
              onKeyDown={handleTextKeyDown}
              rows={12}
              disabled={busy}
            />
          </div>
        ) : (
          <div className="mt-5 space-y-6">
            <div className="grid gap-4 md:grid-cols-2">
              <div className="space-y-2">
                <Label htmlFor="import-kind">{t('experiences.editor.kind')}</Label>
                <select
                  id="import-kind"
                  className="h-10 w-full rounded-md border border-input bg-background px-3 text-sm"
                  value={draft.experience.kind ?? 'other'}
                  onChange={(event) =>
                    updateExperience('kind', event.target.value as ExperienceKind)
                  }
                >
                  {kinds.map((kind) => (
                    <option key={kind} value={kind}>
                      {t(`experiences.kind.${kind}`)}
                    </option>
                  ))}
                </select>
              </div>
              <div className="space-y-2">
                <Label htmlFor="import-title">{t('experiences.editor.titleField')}</Label>
                <Input
                  id="import-title"
                  value={draft.experience.title ?? ''}
                  onChange={(event) => updateExperience('title', event.target.value)}
                />
              </div>
              {(['organization', 'role', 'location'] as const).map((key) => (
                <div key={key} className="space-y-2">
                  <Label htmlFor={`import-${key}`}>{t(`experiences.editor.${key}`)}</Label>
                  <Input
                    id={`import-${key}`}
                    value={draft.experience[key] ?? ''}
                    onChange={(event) => updateExperience(key, event.target.value || null)}
                  />
                </div>
              ))}
              <div className="space-y-2">
                <Label htmlFor="import-start-date">{t('experiences.editor.start_date')}</Label>
                <Input
                  id="import-start-date"
                  type="month"
                  value={draft.experience.start_date ?? ''}
                  onChange={(event) => updateExperience('start_date', event.target.value || null)}
                />
              </div>
              <div className="space-y-2">
                <Label htmlFor="import-end-date">{t('experiences.editor.end_date')}</Label>
                <Input
                  id="import-end-date"
                  type="month"
                  value={draft.experience.end_date ?? ''}
                  disabled={draft.experience.is_current}
                  onChange={(event) => updateExperience('end_date', event.target.value || null)}
                />
              </div>
              <label className="flex items-center gap-2 text-sm" htmlFor="import-is-current">
                <input
                  id="import-is-current"
                  type="checkbox"
                  checked={draft.experience.is_current ?? false}
                  onChange={(event) => {
                    updateExperience('is_current', event.target.checked);
                    if (event.target.checked) updateExperience('end_date', null);
                  }}
                />
                {t('experiences.editor.is_current')}
              </label>
              <div className="space-y-2 md:col-span-2">
                <Label htmlFor="import-background">{t('experiences.editor.background')}</Label>
                <Textarea
                  id="import-background"
                  value={draft.experience.background ?? ''}
                  onChange={(event) => updateExperience('background', event.target.value || null)}
                  rows={4}
                />
              </div>
              <div className="space-y-2">
                <Label htmlFor="import-technologies">{t('experiences.editor.technologies')}</Label>
                <Input
                  id="import-technologies"
                  value={(draft.experience.technologies ?? []).join(', ')}
                  onChange={(event) => updateExperience('technologies', labels(event.target.value))}
                />
              </div>
              <div className="space-y-2">
                <Label htmlFor="import-tags">{t('experiences.editor.tags')}</Label>
                <Input
                  id="import-tags"
                  value={(draft.experience.tags ?? []).join(', ')}
                  onChange={(event) => updateExperience('tags', labels(event.target.value))}
                />
              </div>
              <div className="space-y-2 md:col-span-2">
                <Label htmlFor="import-notes">{t('experiences.editor.notes')}</Label>
                <Textarea
                  id="import-notes"
                  value={draft.experience.notes ?? ''}
                  onChange={(event) => updateExperience('notes', event.target.value || null)}
                  rows={3}
                />
              </div>
            </div>

            <section className="space-y-3" aria-label={t('experiences.evidence.title')}>
              <div className="flex items-center justify-between gap-3">
                <h3 className="font-mono text-xs font-bold uppercase tracking-widest">
                  {t('experiences.evidence.title')}
                </h3>
                <Button type="button" variant="outline" size="sm" onClick={addEvidence}>
                  <Plus className="h-4 w-4" />
                  {t('experiences.import.addEvidence')}
                </Button>
              </div>
              {draft.evidence_items.map((item, index) => (
                <div key={index} className="space-y-3 rounded-md border border-border p-4">
                  <div className="flex items-center justify-between gap-3">
                    <span className="font-mono text-xs text-ink-soft">
                      {t('experiences.evidence.card')} {index + 1}
                    </span>
                    <Button
                      type="button"
                      variant="ghost"
                      size="icon"
                      aria-label={t('experiences.import.removeEvidence')}
                      onClick={() => removeEvidence(index)}
                    >
                      <Trash2 className="h-4 w-4" />
                    </Button>
                  </div>
                  {(['background', 'action', 'result'] as const).map((key) => (
                    <div key={key} className="space-y-2">
                      <Label htmlFor={`import-evidence-${key}-${index}`}>
                        {t(`experiences.evidence.${key}`)}
                      </Label>
                      <Textarea
                        id={`import-evidence-${key}-${index}`}
                        value={item[key] ?? ''}
                        onChange={(event) =>
                          updateEvidence(index, {
                            [key]:
                              key === 'action' ? event.target.value : event.target.value || null,
                          })
                        }
                        rows={key === 'action' ? 3 : 2}
                      />
                    </div>
                  ))}
                </div>
              ))}
            </section>
          </div>
        )}

        {error && (
          <p className="mt-4 font-mono text-xs text-destructive">
            {error instanceof Error ? error.message : t('experiences.import.error')}
          </p>
        )}

        <DialogFooter className="mt-6 gap-2">
          {draft ? (
            <>
              <Button variant="outline" onClick={backToText} disabled={busy}>
                {t('experiences.import.back')}
              </Button>
              <Button onClick={handleSave} disabled={busy || !evidenceValid}>
                {saveMutation.isPending ? (
                  <Loader2 className="h-4 w-4 animate-spin" />
                ) : (
                  t('experiences.import.save')
                )}
              </Button>
            </>
          ) : (
            <>
              <Button variant="outline" onClick={() => onOpenChange(false)} disabled={busy}>
                {t('experiences.import.cancel')}
              </Button>
              <Button onClick={handlePreview} disabled={busy || !text.trim()}>
                {previewMutation.isPending ? (
                  <Loader2 className="h-4 w-4 animate-spin" />
                ) : (
                  t('experiences.import.preview')
                )}
              </Button>
            </>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
