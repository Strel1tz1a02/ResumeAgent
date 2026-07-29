'use client';

import React, { useEffect, useState } from 'react';
import Loader2 from 'lucide-react/dist/esm/icons/loader-2';
import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Label } from '@/components/ui/label';
import { Textarea } from '@/components/ui/textarea';
import { importExperienceText, type ExperienceDetail } from '@/lib/api/experiences';
import { useTranslations } from '@/lib/i18n';

interface TextImportDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onImported: (experience: ExperienceDetail) => void;
}

export function TextImportDialog({ open, onOpenChange, onImported }: TextImportDialogProps) {
  const { t } = useTranslations();
  const [text, setText] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!open) {
      setText('');
      setError(null);
    }
  }, [open]);

  const handleSubmit = async () => {
    if (!text.trim()) return;
    setSubmitting(true);
    setError(null);
    try {
      const experience = await importExperienceText(text);
      onImported(experience);
      onOpenChange(false);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : t('experiences.import.error'));
    } finally {
      setSubmitting(false);
    }
  };

  const handleTextKeyDown = (event: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === 'Enter') event.stopPropagation();
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-2xl p-6">
        <DialogHeader>
          <DialogTitle>{t('experiences.import.title')}</DialogTitle>
          <DialogDescription>{t('experiences.import.description')}</DialogDescription>
        </DialogHeader>
        <div className="mt-5 space-y-2">
          <Label htmlFor="experience-import-text">{t('experiences.import.text')}</Label>
          <Textarea
            id="experience-import-text"
            value={text}
            onChange={(event) => setText(event.target.value)}
            onKeyDown={handleTextKeyDown}
            rows={12}
            disabled={submitting}
          />
          {error && <p className="font-mono text-xs text-destructive">{error}</p>}
        </div>
        <DialogFooter className="mt-6 gap-2">
          <Button variant="outline" onClick={() => onOpenChange(false)} disabled={submitting}>
            {t('experiences.import.cancel')}
          </Button>
          <Button onClick={handleSubmit} disabled={submitting || !text.trim()}>
            {submitting ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : (
              t('experiences.import.submit')
            )}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
