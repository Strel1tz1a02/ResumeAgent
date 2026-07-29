import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

vi.mock('next/navigation', () => ({ useRouter: () => ({ push: vi.fn() }) }));
vi.mock('@/lib/i18n', () => ({
  useTranslations: () => ({
    t: (key: string) =>
      ({
        'dashboard.experienceLibrary.title': 'Experience library',
        'dashboard.experienceLibrary.description': 'Turn notes into reusable resume evidence.',
      })[key] ?? key,
    locale: 'en',
  }),
}));
vi.mock('@/lib/context/status-cache', () => ({
  useStatusCache: () => ({
    status: { llm_configured: true },
    isLoading: false,
    incrementResumes: vi.fn(),
    decrementResumes: vi.fn(),
    setHasMasterResume: vi.fn(),
  }),
}));
vi.mock('@/lib/api/resume', () => ({
  fetchResume: vi.fn(),
  fetchResumeList: vi.fn().mockResolvedValue([]),
  deleteResume: vi.fn(),
  retryProcessing: vi.fn(),
  fetchJobDescription: vi.fn(),
}));

import DashboardPage from '@/app/(default)/dashboard/page';

describe('DashboardPage experience library entry', () => {
  it('links the Swiss-grid experience card to the experience library', async () => {
    render(<DashboardPage />);

    const link = await screen.findByRole('link', { name: /Experience library/i });
    expect(link).toHaveAttribute('href', '/experiences');
    expect(screen.getByText('Turn notes into reusable resume evidence.')).toBeInTheDocument();
  });

  it('keeps the initial grid filled to the next row with the experience card included', async () => {
    render(<DashboardPage />);

    expect(await screen.findByRole('link', { name: /Experience library/i })).toBeInTheDocument();
    expect(screen.getAllByTestId('dashboard-filler')).toHaveLength(7);
  });
});
