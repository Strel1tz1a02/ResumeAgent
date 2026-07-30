import { useIsMutating, useMutation, useQueryClient } from '@tanstack/react-query';
import {
  archiveExperience,
  createEvidence,
  createExperience,
  deleteEvidence,
  deleteExperiencePermanently,
  importExperienceText,
  markExperienceReady,
  patchEvidence,
  patchExperience,
  reorderEvidence,
  requestNextExperienceQuestion,
  restoreExperience,
  submitExperienceAnswer,
  type EvidenceCreate,
  type EvidenceUpdate,
  type ExperienceCreate,
  type ExperienceUpdate,
} from '@/lib/api/experiences';
import { removeExperienceFromCache, writeExperienceDetail } from './cache';
import { experienceKeys } from './keys';

export const EXPERIENCE_CREATION_SCOPE = 'experience-library:creation';

export const experienceMutationKeys = {
  all: () => ['experiences', 'mutation'] as const,
  creation: () => ['experiences', 'mutation', 'creation'] as const,
  item: (experienceId: number, operation: string) =>
    ['experiences', 'mutation', experienceId, operation] as const,
};

export function experienceMutationScope(experienceId: number) {
  return { id: `experience:${experienceId}` };
}

export function useExperienceCreationPending(): boolean {
  return useIsMutating({ mutationKey: experienceMutationKeys.creation() }) > 0;
}

export function useCreateExperienceMutation() {
  const client = useQueryClient();
  return useMutation({
    mutationKey: experienceMutationKeys.creation(),
    mutationFn: (payload: ExperienceCreate) => createExperience(payload),
    scope: { id: EXPERIENCE_CREATION_SCOPE },
    onSuccess: (detail) => writeExperienceDetail(client, detail),
  });
}

export function useImportExperienceMutation() {
  const client = useQueryClient();
  return useMutation({
    mutationKey: experienceMutationKeys.creation(),
    mutationFn: (text: string) => importExperienceText(text),
    scope: { id: EXPERIENCE_CREATION_SCOPE },
    onSuccess: (detail) => writeExperienceDetail(client, detail),
  });
}

export function usePatchExperienceMutation(experienceId: number) {
  const client = useQueryClient();
  return useMutation({
    mutationKey: experienceMutationKeys.item(experienceId, 'patch'),
    mutationFn: (payload: ExperienceUpdate) => patchExperience(experienceId, payload),
    scope: experienceMutationScope(experienceId),
    onSuccess: (detail) => writeExperienceDetail(client, detail),
  });
}

export function useCreateEvidenceMutation(experienceId: number) {
  const client = useQueryClient();
  return useMutation({
    mutationKey: experienceMutationKeys.item(experienceId, 'evidence-create'),
    mutationFn: (payload: EvidenceCreate) => createEvidence(experienceId, payload),
    scope: experienceMutationScope(experienceId),
    onSuccess: (detail) => writeExperienceDetail(client, detail),
  });
}

export function usePatchEvidenceMutation(experienceId: number) {
  const client = useQueryClient();
  return useMutation({
    mutationKey: experienceMutationKeys.item(experienceId, 'evidence-patch'),
    mutationFn: ({ evidenceId, payload }: { evidenceId: number; payload: EvidenceUpdate }) =>
      patchEvidence(experienceId, evidenceId, payload),
    scope: experienceMutationScope(experienceId),
    onSuccess: (detail) => writeExperienceDetail(client, detail),
  });
}

export function useDeleteEvidenceMutation(experienceId: number) {
  const client = useQueryClient();
  return useMutation({
    mutationKey: experienceMutationKeys.item(experienceId, 'evidence-delete'),
    mutationFn: (evidenceId: number) => deleteEvidence(experienceId, evidenceId),
    scope: experienceMutationScope(experienceId),
    onSuccess: (detail) => writeExperienceDetail(client, detail),
  });
}

export function useReorderEvidenceMutation(experienceId: number) {
  const client = useQueryClient();
  return useMutation({
    mutationKey: experienceMutationKeys.item(experienceId, 'evidence-reorder'),
    mutationFn: (evidenceIds: number[]) => reorderEvidence(experienceId, evidenceIds),
    scope: experienceMutationScope(experienceId),
    onSuccess: (detail) => writeExperienceDetail(client, detail),
  });
}

export function useNextExperienceQuestionMutation(experienceId: number) {
  return useMutation({
    mutationKey: experienceMutationKeys.item(experienceId, 'question'),
    mutationFn: () => requestNextExperienceQuestion(experienceId),
    scope: experienceMutationScope(experienceId),
  });
}

export function useSubmitExperienceAnswerMutation(experienceId: number) {
  const client = useQueryClient();
  return useMutation({
    mutationKey: experienceMutationKeys.item(experienceId, 'answer'),
    mutationFn: (payload: Parameters<typeof submitExperienceAnswer>[1]) =>
      submitExperienceAnswer(experienceId, payload),
    scope: experienceMutationScope(experienceId),
    onSuccess: (detail) => writeExperienceDetail(client, detail),
  });
}

export function useMarkExperienceReadyMutation(experienceId: number) {
  const client = useQueryClient();
  return useMutation({
    mutationKey: experienceMutationKeys.item(experienceId, 'ready'),
    mutationFn: () => markExperienceReady(experienceId),
    scope: experienceMutationScope(experienceId),
    onSuccess: (detail) => writeExperienceDetail(client, detail),
  });
}

export function useArchiveExperienceMutation(experienceId: number) {
  const client = useQueryClient();
  return useMutation({
    mutationKey: experienceMutationKeys.item(experienceId, 'archive'),
    mutationFn: () => archiveExperience(experienceId),
    scope: experienceMutationScope(experienceId),
    onSuccess: (detail) => {
      writeExperienceDetail(client, detail);
      client.removeQueries({
        queryKey: experienceKeys.deletionImpact(experienceId),
        exact: true,
      });
    },
  });
}

export function useRestoreExperienceMutation(experienceId: number) {
  const client = useQueryClient();
  return useMutation({
    mutationKey: experienceMutationKeys.item(experienceId, 'restore'),
    mutationFn: () => restoreExperience(experienceId),
    scope: experienceMutationScope(experienceId),
    onSuccess: (detail) => {
      writeExperienceDetail(client, detail);
      client.removeQueries({
        queryKey: experienceKeys.deletionImpact(experienceId),
        exact: true,
      });
    },
  });
}

export function usePermanentDeleteExperienceMutation(experienceId: number) {
  const client = useQueryClient();
  return useMutation({
    mutationKey: experienceMutationKeys.item(experienceId, 'permanent-delete'),
    mutationFn: () => deleteExperiencePermanently(experienceId),
    scope: experienceMutationScope(experienceId),
    onSuccess: () => removeExperienceFromCache(client, experienceId),
  });
}
