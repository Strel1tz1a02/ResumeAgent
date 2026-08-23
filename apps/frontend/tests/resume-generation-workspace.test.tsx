import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const api = vi.hoisted(() => ({
  listJDImports: vi.fn(),
  previewResumeGeneration: vi.fn(),
  confirmResumeGeneration: vi.fn(),
}));

vi.mock('next/navigation', () => ({ useRouter: () => ({ push: vi.fn() }) }));
vi.mock('@/lib/api/jd-imports', () => ({ listJDImports: api.listJDImports }));
vi.mock('@/lib/api/resume-generations', () => ({
  previewResumeGeneration: api.previewResumeGeneration,
  confirmResumeGeneration: api.confirmResumeGeneration,
}));
vi.mock('@/lib/i18n', () => ({
  useTranslations: () => ({
    t: (key: string, params?: Record<string, string | number>) =>
      params
        ? Object.entries(params).reduce(
            (value, [name, replacement]) => value.replace(`{${name}}`, String(replacement)),
            key
          )
        : key,
  }),
}));

import { ResumeGenerationWorkspace } from '@/components/resume-generation/resume-generation-workspace';

const savedJD = {
  id: 7,
  source_url: null,
  company: 'Acme',
  job_name: 'Backend Engineer',
  type: 'Full-time',
  location: 'Shanghai',
  status: 'confirmed' as const,
  revision: 1,
  requirements: [
    {
      id: 11,
      jd_information_id: 7,
      priority: 'required' as const,
      content: 'Python',
      sort_order: 0,
      revision: 1,
    },
  ],
};

describe('ResumeGenerationWorkspace progress', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    api.listJDImports.mockResolvedValue({ items: [savedJD], total: 1 });
    api.previewResumeGeneration.mockImplementation(() => new Promise(() => undefined));
  });

  it('shows progress and locks generation settings while preview is pending', async () => {
    const { unmount } = render(<ResumeGenerationWorkspace />);
    const generate = await screen.findByRole('button', {
      name: 'resumeGeneration.generate',
    });

    fireEvent.click(generate);

    await waitFor(() => expect(api.previewResumeGeneration).toHaveBeenCalledTimes(1));
    expect(screen.getByRole('progressbar')).toBeInTheDocument();
    expect(screen.getByLabelText('resumeGeneration.jd')).toBeDisabled();
    expect(screen.getByLabelText('resumeGeneration.mode')).toBeDisabled();
    expect(screen.getByLabelText('resumeGeneration.pages')).toBeDisabled();
    expect(screen.queryByText('resumeGeneration.emptyTitle')).not.toBeInTheDocument();

    unmount();
  });
});
