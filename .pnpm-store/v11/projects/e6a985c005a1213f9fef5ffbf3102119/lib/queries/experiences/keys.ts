export type ExperienceLibraryView = 'active' | 'archived';

export const experienceKeys = {
  all: ['experiences'] as const,
  lists: () => ['experiences', 'list'] as const,
  list: (view: ExperienceLibraryView) => ['experiences', 'list', view] as const,
  details: () => ['experiences', 'detail'] as const,
  detail: (experienceId: number | 'none') => ['experiences', 'detail', experienceId] as const,
  deletionImpact: (experienceId: number | 'none') =>
    ['experiences', 'deletion-impact', experienceId] as const,
};
