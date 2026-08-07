import {
  useIsMutating,
  useMutation,
  useQueryClient,
  type QueryClient,
} from '@tanstack/react-query';
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
  saveExperience,
  restoreExperience,
  type EvidenceCreateRequest,
  type EvidenceUpdate,
  type ExperienceCreate,
  type ExperienceGlobalSave,
  type ExperienceListResponse,
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

async function storeAuthoritativeDetail(
  client: QueryClient,
  detail: Parameters<typeof writeExperienceDetail>[1]
): Promise<void> {
  await Promise.all([
    client.cancelQueries({ queryKey: experienceKeys.lists() }),
    client.cancelQueries({ queryKey: experienceKeys.detail(detail.experience_id), exact: true }),
  ]);
  writeExperienceDetail(client, detail);
}

async function storeCreatedDetail(
  client: QueryClient,
  detail: Parameters<typeof writeExperienceDetail>[1]
): Promise<void> {
  await storeAuthoritativeDetail(client, detail);
  const activeKey = experienceKeys.list('active');
  if (!client.getQueryData(activeKey)) {
    client.setQueryData<ExperienceListResponse>(activeKey, { items: [detail], total: 1 });
  }
}

export function useCreateExperienceMutation() {
  const client = useQueryClient();
  return useMutation({
    mutationKey: experienceMutationKeys.creation(),
    mutationFn: (payload: ExperienceCreate) => createExperience(payload),
    scope: { id: EXPERIENCE_CREATION_SCOPE },
    onSuccess: (detail) => storeCreatedDetail(client, detail),
  });
}

export function useImportExperienceMutation() {
  const client = useQueryClient();
  return useMutation({
    mutationKey: experienceMutationKeys.creation(),
    mutationFn: (text: string) => importExperienceText(text),
    scope: { id: EXPERIENCE_CREATION_SCOPE },
    onSuccess: (detail) => storeCreatedDetail(client, detail),
  });
}

export function usePatchExperienceMutation(experienceId: number) {
  const client = useQueryClient();
  return useMutation({
    mutationKey: experienceMutationKeys.item(experienceId, 'patch'),
    mutationFn: (payload: ExperienceUpdate) => patchExperience(experienceId, payload),
    scope: experienceMutationScope(experienceId),
    onSuccess: (detail) => storeAuthoritativeDetail(client, detail),
  });
}

export function useSaveExperienceMutation(experienceId: number) {
  const client = useQueryClient();
  return useMutation({
    mutationKey: experienceMutationKeys.item(experienceId, 'save'),
    mutationFn: (payload: ExperienceGlobalSave) => saveExperience(experienceId, payload),
    scope: experienceMutationScope(experienceId),
    onSuccess: (detail) => storeAuthoritativeDetail(client, detail),
  });
}

export function useCreateEvidenceMutation(experienceId: number) {
  const client = useQueryClient();
  return useMutation({
    mutationKey: experienceMutationKeys.item(experienceId, 'evidence-create'),
    mutationFn: (payload: EvidenceCreateRequest) => createEvidence(experienceId, payload),
    scope: experienceMutationScope(experienceId),
    onSuccess: (detail) => storeAuthoritativeDetail(client, detail),
  });
}

export function usePatchEvidenceMutation(experienceId: number) {
  const client = useQueryClient();
  return useMutation({
    mutationKey: experienceMutationKeys.item(experienceId, 'evidence-patch'),
    mutationFn: ({ evidenceId, payload }: { evidenceId: number; payload: EvidenceUpdate }) =>
      patchEvidence(experienceId, evidenceId, payload),
    scope: experienceMutationScope(experienceId),
    onSuccess: (detail) => storeAuthoritativeDetail(client, detail),
  });
}

export function useDeleteEvidenceMutation(experienceId: number) {
  const client = useQueryClient();
  return useMutation({
    mutationKey: experienceMutationKeys.item(experienceId, 'evidence-delete'),
    mutationFn: (value: {
      evidenceId: number;
      expectedRevision: number;
      expectedCollectionRevision: number;
    }) =>
      deleteEvidence(
        experienceId,
        value.evidenceId,
        value.expectedRevision,
        value.expectedCollectionRevision
      ),
    scope: experienceMutationScope(experienceId),
    onSuccess: (detail) => storeAuthoritativeDetail(client, detail),
  });
}

export function useReorderEvidenceMutation(experienceId: number) {
  const client = useQueryClient();
  return useMutation({
    mutationKey: experienceMutationKeys.item(experienceId, 'evidence-reorder'),
    mutationFn: (value: { evidenceIds: number[]; expectedCollectionRevision: number }) =>
      reorderEvidence(experienceId, value.evidenceIds, value.expectedCollectionRevision),
    scope: experienceMutationScope(experienceId),
    onSuccess: (detail) => storeAuthoritativeDetail(client, detail),
  });
}

export function useMarkExperienceReadyMutation(experienceId: number) {
  const client = useQueryClient();
  return useMutation({
    mutationKey: experienceMutationKeys.item(experienceId, 'ready'),
    mutationFn: () => markExperienceReady(experienceId),
    scope: experienceMutationScope(experienceId),
    onSuccess: (detail) => storeAuthoritativeDetail(client, detail),
  });
}

export function useArchiveExperienceMutation(experienceId: number) {
  const client = useQueryClient();
  return useMutation({
    mutationKey: experienceMutationKeys.item(experienceId, 'archive'),
    mutationFn: () => archiveExperience(experienceId),
    scope: experienceMutationScope(experienceId),
    onSuccess: async (detail) => {
      await storeAuthoritativeDetail(client, detail);
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
    onSuccess: async (detail) => {
      await storeAuthoritativeDetail(client, detail);
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
    onSuccess: async () => {
      await Promise.all([
        client.cancelQueries({ queryKey: experienceKeys.lists() }),
        client.cancelQueries({ queryKey: experienceKeys.detail(experienceId), exact: true }),
        client.cancelQueries({
          queryKey: experienceKeys.deletionImpact(experienceId),
          exact: true,
        }),
      ]);
      removeExperienceFromCache(client, experienceId);
    },
  });
}
