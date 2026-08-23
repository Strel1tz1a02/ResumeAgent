import { act, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { GenerationProgress } from '@/components/resume-generation/generation-progress';

vi.mock('@/lib/i18n', () => ({
  useTranslations: () => ({
    t: (key: string, params?: Record<string, string | number>) =>
      key === 'resumeGeneration.progressElapsed' ? `elapsed:${String(params?.seconds)}` : key,
  }),
}));

describe('GenerationProgress', () => {
  afterEach(() => {
    vi.useRealTimers();
  });

  it('shows indeterminate steps and only reports factual elapsed time', () => {
    vi.useFakeTimers();
    render(<GenerationProgress />);

    const progress = screen.getByRole('progressbar');
    expect(progress).not.toHaveAttribute('aria-valuenow');
    expect(screen.getByText('resumeGeneration.progressAnalyze')).toBeInTheDocument();
    expect(screen.getByText('resumeGeneration.progressPlan')).toBeInTheDocument();
    expect(screen.getByText('resumeGeneration.progressValidate')).toBeInTheDocument();
    expect(screen.getByText('elapsed:0')).toBeInTheDocument();

    act(() => vi.advanceTimersByTime(3000));
    expect(screen.getByText('elapsed:3')).toBeInTheDocument();
  });
});
