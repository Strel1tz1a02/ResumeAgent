'use client';

import { useEffect, useState } from 'react';
import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import {
  deleteExperiencePermanently,
  getDeletionImpact,
  type DeletionImpactResponse,
} from '@/lib/api/experiences';
import { useTranslations } from '@/lib/i18n';

interface PermanentDeleteDialogProps {
  experienceId: number | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onDeleted: (experienceId: number) => void;
}

export function PermanentDeleteDialog({
  experienceId,
  open,
  onOpenChange,
  onDeleted,
}: PermanentDeleteDialogProps) {
  const { t } = useTranslations();
  const [impact, setImpact] = useState<DeletionImpactResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    if (!open || experienceId === null) return;
    let active = true;
    setImpact(null);
    setError(null);
    void getDeletionImpact(experienceId)
      .then((value) => {
        if (active) setImpact(value);
      })
      .catch((reason) => {
        if (active)
          setError(reason instanceof Error ? reason.message : t('experiences.permanent.error'));
      });
    return () => {
      active = false;
    };
  }, [experienceId, open, t]);

  const remove = async () => {
    if (experienceId === null || !impact || submitting) return;
    setSubmitting(true);
    setError(null);
    try {
      await deleteExperiencePermanently(experienceId);
      onDeleted(experienceId);
      onOpenChange(false);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : t('experiences.permanent.error'));
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-[425px] p-0 gap-0">
        <DialogHeader className="p-6 pb-4">
          <DialogTitle>{t('experiences.permanent.title')}</DialogTitle>
          <DialogDescription>{t('experiences.permanent.description')}</DialogDescription>
        </DialogHeader>
        <div className="space-y-3 px-6 pb-4 text-sm">
          {!impact && !error && <p>{t('experiences.permanent.loadingImpact')}</p>}
          {impact && (
            <>
              <p>
                {t('experiences.permanent.affectedMatches', {
                  count: impact.affected_matches.length,
                })}
              </p>
              {impact.affected_matches.length > 0 && <p>{impact.affected_matches.join(', ')}</p>}
              <p>
                {t('experiences.permanent.affectedResumes', {
                  count: impact.affected_resumes.length,
                })}
              </p>
              {impact.affected_resumes.length > 0 && <p>{impact.affected_resumes.join(', ')}</p>}
            </>
          )}
          {error && <p className="font-mono text-xs text-destructive">{error}</p>}
        </div>
        <DialogFooter className="border-t border-black bg-secondary p-4">
          <Button variant="outline" onClick={() => onOpenChange(false)} disabled={submitting}>
            {t('common.cancel')}
          </Button>
          <Button
            variant="destructive"
            onClick={() => void remove()}
            disabled={!impact || submitting}
          >
            {submitting ? t('experiences.permanent.deleting') : t('experiences.permanent.confirm')}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
