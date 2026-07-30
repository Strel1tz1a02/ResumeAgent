'use client';

import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { useTranslations } from '@/lib/i18n';
import { usePermanentDeleteExperienceMutation } from '@/lib/queries/experiences/mutations';
import { useDeletionImpact } from '@/lib/queries/experiences/queries';

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
  const impactQuery = useDeletionImpact(experienceId, open);
  const deleteMutation = usePermanentDeleteExperienceMutation(experienceId ?? 0);
  const impact = impactQuery.data ?? null;
  const error = impactQuery.error ?? deleteMutation.error;
  const submitting = deleteMutation.isPending;

  const remove = async () => {
    if (experienceId === null || !impact || submitting) return;
    try {
      await deleteMutation.mutateAsync();
      onDeleted(experienceId);
      onOpenChange(false);
    } catch {
      // Mutation error remains visible and retryable in this dialog.
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
          {impactQuery.isPending && <p>{t('experiences.permanent.loadingImpact')}</p>}
          {impact && (
            <>
              <p>
                {t('experiences.permanent.affectedMatches', {
                  count: impact.affected_matches.length,
                })}
              </p>
              {impact.affected_matches.length > 0 && (
                <p>
                  {impact.affected_matches
                    .map((match) => `${match.job_title} (#${match.match_id})`)
                    .join(', ')}
                </p>
              )}
              <p>
                {t('experiences.permanent.affectedResumes', {
                  count: impact.affected_resumes.length,
                })}
              </p>
              {impact.affected_resumes.length > 0 && <p>{impact.affected_resumes.join(', ')}</p>}
            </>
          )}
          {error && (
            <p className="font-mono text-xs text-destructive">
              {error instanceof Error ? error.message : t('experiences.permanent.error')}
            </p>
          )}
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
