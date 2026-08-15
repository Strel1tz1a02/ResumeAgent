import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

vi.mock('next/navigation', () => ({ useRouter: () => ({ push: vi.fn() }) }));
vi.mock('@/lib/i18n', () => ({
  useTranslations: () => ({
    t: (key: string) =>
      ({
        'dashboard.experienceLibrary.title': 'Experience library',
        'dashboard.experienceLibrary.description': 'Turn notes into reusable resume evidence.',
        'dashboard.jdImports.title': 'JD Workspace',
        'dashboard.jdImports.description': 'Import and manage saved JDs.',
        'dashboard.resumeGeneration.title': 'Smart Resume Generation',
        'dashboard.resumeGeneration.description': 'Generate from evidence.',
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

describe('DashboardPage library entries', () => {
  it('links the Swiss-grid experience card to the experience library', async () => {
    render(<DashboardPage />);

    const link = await screen.findByRole('link', { name: /Experience library/i });
    expect(link).toHaveAttribute('href', '/experiences');
    expect(screen.getByText('Turn notes into reusable resume evidence.')).toBeInTheDocument();
  });

  it('links the JD workspace card to import and management', async () => {
    render(<DashboardPage />);

    const link = await screen.findByRole('link', { name: /JD Workspace/i });
    expect(link).toHaveAttribute('href', '/jd-imports');
    expect(screen.getByText('Import and manage saved JDs.')).toBeInTheDocument();
  });

  it('links the evidence-grounded generation workspace', async () => {
    render(<DashboardPage />);

    const link = await screen.findByRole('link', { name: /Smart Resume Generation/i });
    expect(link).toHaveAttribute('href', '/resume-generation');
    expect(screen.getByText('Generate from evidence.')).toBeInTheDocument();
  });

  it('keeps the initial grid filled to the next row with the experience card included', async () => {
    render(<DashboardPage />);

    expect(await screen.findByRole('link', { name: /Experience library/i })).toBeInTheDocument();
    expect(screen.getAllByTestId('dashboard-filler')).toHaveLength(5);
  });
});
