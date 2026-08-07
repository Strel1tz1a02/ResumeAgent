import type { QueryClient } from '@tanstack/react-query';
import type { ExperienceDetail, ExperienceListResponse } from '@/lib/api/experiences';
import { experienceKeys, type ExperienceLibraryView } from './keys';

function revisionKey(key: string, refId: number | null): string {
  return `${key}:${refId ?? 'root'}`;
}

function hasStaleRevision(current: ExperienceDetail, next: ExperienceDetail): boolean {
  const currentRevisions = new Map(
    current.field_states.map((state) => [revisionKey(state.key, state.ref_id), state.revision])
  );
  return next.field_states.some((state) => {
    const observed = currentRevisions.get(revisionKey(state.key, state.ref_id));
    return observed !== undefined && state.revision < observed;
  });
}

function updateList(
  current: ExperienceListResponse | undefined,
  detail: ExperienceDetail,
  view: ExperienceLibraryView
): ExperienceListResponse | undefined {
  if (!current) return current;
  const belongs = view === 'archived' ? detail.status === 'archived' : detail.status !== 'archived';
  const index = current.items.findIndex((item) => item.experience_id === detail.experience_id);

  if (!belongs) {
    if (index === -1) return current;
    return {
      items: current.items.filter((item) => item.experience_id !== detail.experience_id),
      total: Math.max(0, current.total - 1),
    };
  }

  if (index === -1) {
    return { items: [detail, ...current.items], total: current.total + 1 };
  }
  return {
    ...current,
    items: current.items.map((item) =>
      item.experience_id === detail.experience_id ? detail : item
    ),
  };
}

export function writeExperienceDetail(client: QueryClient, detail: ExperienceDetail): void {
  let accepted = detail;
  client.setQueryData<ExperienceDetail>(experienceKeys.detail(detail.experience_id), (current) => {
    if (current && hasStaleRevision(current, detail)) {
      accepted = current;
      return current;
    }
    return detail;
  });
  for (const view of ['active', 'archived'] as const) {
    client.setQueryData<ExperienceListResponse>(experienceKeys.list(view), (current) =>
      updateList(current, accepted, view)
    );
  }
}

export function removeExperienceFromCache(client: QueryClient, experienceId: number): void {
  for (const view of ['active', 'archived'] as const) {
    client.setQueryData<ExperienceListResponse>(experienceKeys.list(view), (current) => {
      if (!current || !current.items.some((item) => item.experience_id === experienceId)) {
        return current;
      }
      return {
        items: current.items.filter((item) => item.experience_id !== experienceId),
        total: Math.max(0, current.total - 1),
      };
    });
  }
  client.removeQueries({ queryKey: experienceKeys.detail(experienceId), exact: true });
  client.removeQueries({ queryKey: experienceKeys.deletionImpact(experienceId), exact: true });
}
