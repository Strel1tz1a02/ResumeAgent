import { useQuery } from '@tanstack/react-query';
import {
  fetchExperience,
  getDeletionImpact,
  listExperiences,
} from '@/lib/api/experiences';
import { experienceKeys, type ExperienceLibraryView } from './keys';

export function useExperienceList(view: ExperienceLibraryView) {
  return useQuery({
    queryKey: experienceKeys.list(view),
    queryFn: ({ signal }) => listExperiences({ status: view }, signal),
    staleTime: Infinity,
  });
}

export function useExperienceDetail(experienceId: number | null) {
  return useQuery({
    queryKey: experienceKeys.detail(experienceId ?? 'none'),
    queryFn: ({ signal }) => fetchExperience(experienceId as number, signal),
    enabled: experienceId !== null,
    staleTime: Infinity,
  });
}

export function useDeletionImpact(experienceId: number | null, enabled: boolean) {
  return useQuery({
    queryKey: experienceKeys.deletionImpact(experienceId ?? 'none'),
    queryFn: ({ signal }) => getDeletionImpact(experienceId as number, signal),
    enabled: enabled && experienceId !== null,
    staleTime: Infinity,
  });
}
