import { StrictMode } from 'react';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { ExperienceDetail, ExperienceRead } from '@/lib/api/experiences';

const api = vi.hoisted(() => ({
  listExperiences: vi.fn(),
  fetchExperience: vi.fn(),
  importExperienceText: vi.fn(),
  patchExperience: vi.fn(),
  createEvidence: vi.fn(),
  patchEvidence: vi.fn(),
  deleteEvidence: vi.fn(),
  reorderEvidence: vi.fn(),
  markExperienceReady: vi.fn(),
  archiveExperience: vi.fn(),
  restoreExperience: vi.fn(),
  getDeletionImpact: vi.fn(),
  deleteExperiencePermanently: vi.fn(),
  requestNextExperienceQuestion: vi.fn(),
  submitExperienceAnswer: vi.fn(),
}));

const translate = vi.hoisted(
  () => (key: string) =>
    ({
      'experiences.title': 'Experience library',
      'experiences.search': 'Search experiences',
      'experiences.kind': 'Kind',
      'experiences.kind.all': 'All kinds',
      'experiences.kind.project': 'Project',
      'experiences.kind.volunteer': 'Volunteer',
      'experiences.import': 'Import',
      'experiences.import.title': 'Import experience',
      'experiences.import.description': 'Paste your experience notes.',
      'experiences.import.text': 'Experience text',
      'experiences.import.submit': 'Save import',
      'experiences.import.cancel': 'Cancel',
      'experiences.loading': 'Loading experiences',
      'experiences.empty': 'No experiences',
      'experiences.noResults': 'No matching experiences',
      'experiences.retry': 'Retry',
      'experiences.refresh': 'Refresh',
      'experiences.error': 'Could not load experiences',
      'experiences.rawInput': 'Raw input',
      'experiences.backToList': 'Back to list',
      'experiences.status.draft': 'Draft status',
      'experiences.editor.titleField': 'Title',
      'experiences.editor.is_current': 'Current experience',
      'experiences.editor.save': 'Save experience',
      'experiences.completeness.score': '72% complete',
      'experiences.active': 'Active',
      'experiences.archive': 'Recycle bin',
      'experiences.lifecycle.archive': 'Archive experience',
      'experiences.lifecycle.restore': 'Restore experience',
      'experiences.lifecycle.permanent': 'Delete permanently',
      'experiences.evidence.action': 'Action',
      'experiences.evidence.result': 'Result',
      'experiences.evidence.metrics': 'Metrics',
      'experiences.evidence.add': 'Add evidence',
      'experiences.evidence.save': 'Save evidence',
      'experiences.evidence.moveUp': 'Move evidence up',
      'experiences.evidence.moveDown': 'Move evidence down',
      'experiences.evidence.delete': 'Delete evidence',
      'experiences.unsaved.title': 'Discard unsaved changes?',
      'experiences.unsaved.description': 'Your edits will be lost.',
      'experiences.unsaved.discard': 'Discard edits',
      'experiences.ready.action': 'Mark ready',
      'experiences.ready.conflict': 'Not ready',
      'experiences.ready.error': 'Could not mark ready',
      'experiences.permanent.title': 'Delete permanently?',
      'experiences.permanent.description': 'This cannot be undone.',
      'experiences.permanent.loadingImpact': 'Loading impact',
      'experiences.permanent.affectedMatches': '1 affected matches',
      'experiences.permanent.affectedResumes': '1 affected resumes',
      'experiences.permanent.confirm': 'Delete permanently',
      'experiences.ai.title': 'Organize with AI',
      'experiences.ai.start': 'Help me organize with AI',
      'experiences.ai.answer': 'Your answer',
      'experiences.ai.submit': 'Apply answer',
      'experiences.ai.next': 'Ask another question',
      'experiences.ai.loading': 'Preparing question',
      'experiences.ai.answering': 'Applying answer',
      'experiences.ai.retry': 'Try again',
      'experiences.ai.error':
        'AI could not organize this experience. You can keep editing it manually.',
      'experiences.ai.fallback': 'Suggested question',
      'experiences.ai.manual': 'You can edit this experience manually at any time.',
      'common.cancel': 'Cancel',
    })[key] ?? key
);

vi.mock('@/lib/api/experiences', () => api);

vi.mock('@/lib/i18n', () => ({
  useTranslations: () => ({ t: translate }),
}));

import { ExperienceLibraryPage } from '@/components/experiences/experience-library-page';

const listItem: ExperienceRead = {
  experience_id: 1,
  kind: 'project',
  title: 'Searchable project',
  organization: 'Acme',
  role: 'Engineer',
  location: null,
  start_date: null,
  end_date: null,
  is_current: false,
  raw_input: 'Initial note',
  background: null,
  evidence_ids: [],
  technologies: ['TypeScript'],
  tags: ['search'],
  notes: null,
  status: 'draft',
  completeness: 10,
  archived_at: null,
  created_at: '2025-01-01T00:00:00Z',
  updated_at: '2025-01-02T00:00:00Z',
};

const imported: ExperienceDetail = {
  ...listItem,
  experience_id: 9,
  title: 'Imported experience',
  raw_input: 'Exactly this valid text',
  evidence_items: [],
  missing_dimensions: ['evidence'],
  suggested_questions: ['What changed?'],
};

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

describe('ExperienceLibraryPage', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    api.listExperiences.mockResolvedValue({ items: [listItem], total: 1 });
    api.fetchExperience.mockResolvedValue({
      ...listItem,
      evidence_items: [],
      missing_dimensions: ['evidence'],
      suggested_questions: ['What changed?'],
    });
  });

  afterEach(() => {
    vi.clearAllMocks();
  });

  it('shows a loading state before the experience list resolves', () => {
    api.listExperiences.mockReturnValue(new Promise(() => {}));

    render(<ExperienceLibraryPage />);

    expect(screen.getByText('Loading experiences')).toBeInTheDocument();
  });

  it('offers retry after a list failure and loads the retry result', async () => {
    api.listExperiences
      .mockRejectedValueOnce(new Error('offline'))
      .mockResolvedValueOnce({ items: [listItem], total: 1 });

    render(<ExperienceLibraryPage />);

    expect(await screen.findByText('Could not load experiences')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
    expect(await screen.findByRole('button', { name: /Searchable project/ })).toBeInTheDocument();
  });

  it('renders an empty state when the library has no experiences', async () => {
    api.listExperiences.mockResolvedValueOnce({ items: [], total: 0 });
    render(<ExperienceLibraryPage />);
    expect(await screen.findAllByText('No experiences')).toHaveLength(2);
  });

  it('filters the active list by query and kind', async () => {
    const volunteer = {
      ...listItem,
      experience_id: 2,
      kind: 'volunteer' as const,
      title: 'Volunteer event',
    };
    api.listExperiences.mockResolvedValueOnce({ items: [listItem, volunteer], total: 2 });
    render(<ExperienceLibraryPage />);

    expect(await screen.findByRole('button', { name: /Searchable project/ })).toBeInTheDocument();
    expect(screen.getByText('Volunteer event')).toBeInTheDocument();
    fireEvent.change(screen.getByRole('textbox', { name: 'Search experiences' }), {
      target: { value: 'searchable' },
    });
    expect(screen.getByRole('button', { name: /Searchable project/ })).toBeInTheDocument();
    fireEvent.change(screen.getByRole('textbox', { name: 'Search experiences' }), {
      target: { value: 'other' },
    });
    expect(screen.getByText('No matching experiences')).toBeInTheDocument();
    fireEvent.change(screen.getByRole('textbox', { name: 'Search experiences' }), {
      target: { value: '' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Kind' }));
    fireEvent.click(screen.getByRole('menuitemradio', { name: 'Project' }));
    expect(screen.getByRole('button', { name: /Searchable project/ })).toBeInTheDocument();
    expect(screen.queryByText('Volunteer event')).not.toBeInTheDocument();
  });

  it('finds experiences from raw input and technologies', async () => {
    const rawOnly = {
      ...listItem,
      experience_id: 2,
      title: 'Untitled note',
      raw_input: 'Migrated a legacy warehouse',
      technologies: [],
      tags: [],
    };
    const technologyOnly = {
      ...listItem,
      experience_id: 3,
      title: 'Implementation',
      raw_input: '',
      technologies: ['Rust'],
      tags: [],
    };
    api.listExperiences.mockResolvedValueOnce({ items: [rawOnly, technologyOnly], total: 2 });
    render(<ExperienceLibraryPage />);
    await screen.findByRole('button', { name: /Untitled note/ });

    fireEvent.change(screen.getByRole('textbox', { name: 'Search experiences' }), {
      target: { value: 'legacy warehouse' },
    });
    expect(screen.getByRole('button', { name: /Untitled note/ })).toBeInTheDocument();

    fireEvent.change(screen.getByRole('textbox', { name: 'Search experiences' }), {
      target: { value: 'rust' },
    });
    expect(screen.getByRole('button', { name: /Implementation/ })).toBeInTheDocument();
  });

  it('keeps an imported draft when an older initial list request resolves later', async () => {
    const initialList = deferred<{ items: ExperienceRead[]; total: number }>();
    api.listExperiences.mockReturnValueOnce(initialList.promise);
    api.importExperienceText.mockResolvedValue(imported);
    render(<ExperienceLibraryPage />);

    fireEvent.click(screen.getByRole('button', { name: 'Import' }));
    fireEvent.change(screen.getByRole('textbox', { name: 'Experience text' }), {
      target: { value: 'Exactly this valid text' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Save import' }));
    await screen.findByRole('heading', { name: 'Imported experience' });

    initialList.resolve({ items: [listItem], total: 1 });
    await waitFor(() => expect(api.listExperiences).toHaveBeenCalledTimes(1));
    expect(screen.getByRole('heading', { name: 'Imported experience' })).toBeInTheDocument();
    expect(screen.getAllByText('Exactly this valid text').length).toBeGreaterThan(0);
  });

  it('reselects the first visible item when a filter hides the selected detail', async () => {
    const volunteer = {
      ...listItem,
      experience_id: 2,
      kind: 'volunteer' as const,
      title: 'Volunteer event',
    };
    api.listExperiences.mockResolvedValueOnce({ items: [listItem, volunteer], total: 2 });
    render(<ExperienceLibraryPage />);
    await screen.findByRole('heading', { name: 'Searchable project' });

    fireEvent.change(screen.getByRole('textbox', { name: 'Search experiences' }), {
      target: { value: 'volunteer' },
    });
    expect(screen.getByRole('heading', { name: 'Volunteer event' })).toBeInTheDocument();
  });

  it('clears incompatible filters and opens the imported draft detail pane', async () => {
    api.importExperienceText.mockResolvedValue(imported);
    render(<ExperienceLibraryPage />);
    await screen.findByRole('button', { name: /Searchable project/ });
    fireEvent.change(screen.getByRole('textbox', { name: 'Search experiences' }), {
      target: { value: 'only-existing-item' },
    });

    fireEvent.click(screen.getByRole('button', { name: 'Import' }));
    fireEvent.change(screen.getByRole('textbox', { name: 'Experience text' }), {
      target: { value: 'Exactly this valid text' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Save import' }));

    expect(await screen.findByRole('heading', { name: 'Imported experience' })).toBeInTheDocument();
    expect(screen.getByTestId('experience-detail-pane')).toHaveClass('block');
    expect(screen.getByTestId('experience-list-pane')).toHaveClass('hidden');
  });

  it('switches mobile panes on list selection and returns to the list with Back', async () => {
    render(<ExperienceLibraryPage />);
    await screen.findByRole('button', { name: /Searchable project/ });

    expect(screen.getByTestId('experience-list-pane')).toHaveClass('block');
    expect(screen.getByTestId('experience-detail-pane')).toHaveClass('hidden');
    fireEvent.click(screen.getByRole('button', { name: /Searchable project/ }));
    expect(screen.getByTestId('experience-list-pane')).toHaveClass('hidden');
    expect(screen.getByTestId('experience-detail-pane')).toHaveClass('block');
    fireEvent.click(screen.getByRole('button', { name: 'Back to list' }));
    expect(screen.getByTestId('experience-list-pane')).toHaveClass('block');
    expect(screen.getByTestId('experience-detail-pane')).toHaveClass('hidden');
  });

  it('localizes the status label and stops Enter from escaping the import textarea', async () => {
    const documentKeydown = vi.fn();
    document.addEventListener('keydown', documentKeydown);
    render(<ExperienceLibraryPage />);
    await screen.findByRole('button', { name: /Searchable project/ });
    expect(screen.getByText(/Draft status/)).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Import' }));
    fireEvent.keyDown(screen.getByRole('textbox', { name: 'Experience text' }), { key: 'Enter' });
    expect(documentKeydown).not.toHaveBeenCalled();
    document.removeEventListener('keydown', documentKeydown);
  });

  it('returns focus to Import when the dialog closes with Escape', async () => {
    render(<ExperienceLibraryPage />);
    const importButton = screen.getByRole('button', { name: 'Import' });
    importButton.focus();
    fireEvent.click(importButton);
    await screen.findByRole('textbox', { name: 'Experience text' });

    fireEvent.keyDown(screen.getByRole('dialog'), { key: 'Escape' });
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
    expect(importButton).toHaveFocus();
  });

  it('persists exact pasted text, selects the returned draft, and does not start questions', async () => {
    api.importExperienceText.mockResolvedValue(imported);
    render(<ExperienceLibraryPage />);
    await screen.findByRole('button', { name: /Searchable project/ });

    fireEvent.click(screen.getByRole('button', { name: 'Import' }));
    fireEvent.change(screen.getByRole('textbox', { name: 'Experience text' }), {
      target: { value: 'Exactly this valid text' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Save import' }));

    await waitFor(() => {
      expect(api.importExperienceText).toHaveBeenCalledWith('Exactly this valid text');
    });
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(screen.getAllByText('Imported experience')).toHaveLength(2);
    expect(screen.getAllByText('Exactly this valid text').length).toBeGreaterThan(0);
    expect(api.requestNextExperienceQuestion).not.toHaveBeenCalled();
  });

  it('keeps the persisted import selected when a later reload fails', async () => {
    api.importExperienceText.mockResolvedValue(imported);
    api.listExperiences
      .mockResolvedValueOnce({ items: [listItem], total: 1 })
      .mockRejectedValueOnce(new Error('offline'));
    render(<ExperienceLibraryPage />);
    await screen.findByRole('button', { name: /Searchable project/ });

    fireEvent.click(screen.getByRole('button', { name: 'Import' }));
    fireEvent.change(screen.getByRole('textbox', { name: 'Experience text' }), {
      target: { value: 'Exactly this valid text' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Save import' }));
    await screen.findAllByText('Imported experience');

    fireEvent.click(screen.getByRole('button', { name: 'Refresh' }));
    expect(await screen.findByText('Could not load experiences')).toBeInTheDocument();
    expect(screen.getAllByText('Imported experience')).toHaveLength(2);
    expect(screen.getAllByText('Exactly this valid text').length).toBeGreaterThan(0);
  });

  it('saves controlled metadata with current experiences clearing the end date', async () => {
    const refreshed = {
      ...listItem,
      title: 'Current platform',
      is_current: true,
      end_date: null,
      completeness: 72,
      evidence_items: [],
      missing_dimensions: ['evidence'],
      suggested_questions: [],
    };
    api.patchExperience.mockResolvedValue(refreshed);
    render(<ExperienceLibraryPage />);

    await screen.findByRole('heading', { name: 'Searchable project' });
    fireEvent.change(await screen.findByRole('textbox', { name: 'Title' }), {
      target: { value: 'Current platform' },
    });
    fireEvent.click(screen.getByRole('checkbox', { name: 'Current experience' }));
    fireEvent.click(screen.getByRole('button', { name: 'Save experience' }));

    await waitFor(() =>
      expect(api.patchExperience).toHaveBeenCalledWith(
        1,
        expect.objectContaining({ title: 'Current platform', is_current: true, end_date: null })
      )
    );
    expect(screen.getByText('72% complete')).toBeInTheDocument();
  });

  it('keeps action, result, and metrics together when adding an evidence card', async () => {
    const withEvidence = {
      ...listItem,
      completeness: 60,
      evidence_ids: [4],
      evidence_items: [
        {
          id: 4,
          action: 'Automated imports',
          result: 'Faster review',
          metrics: '40%',
          created_at: '2025-01-01',
          updated_at: '2025-01-01',
        },
      ],
      missing_dimensions: [],
      suggested_questions: [],
    };
    api.createEvidence.mockResolvedValue(withEvidence);
    render(<ExperienceLibraryPage />);

    await screen.findByLabelText('Action new');
    fireEvent.change(screen.getByLabelText('Action new'), {
      target: { value: 'Automated imports' },
    });
    fireEvent.change(screen.getByLabelText('Result new'), { target: { value: 'Faster review' } });
    fireEvent.change(screen.getByLabelText('Metrics new'), { target: { value: '40%' } });
    fireEvent.click(screen.getByRole('button', { name: 'Add evidence' }));

    await waitFor(() =>
      expect(api.createEvidence).toHaveBeenCalledWith(1, {
        action: 'Automated imports',
        result: 'Faster review',
        metrics: '40%',
      })
    );
    expect(screen.getByLabelText('Action 4')).toHaveValue('Automated imports');
    expect(screen.getByLabelText('Result 4')).toHaveValue('Faster review');
    expect(screen.getByLabelText('Metrics 4')).toHaveValue('40%');
  });

  it('keeps the edited metadata visible after a save failure', async () => {
    api.patchExperience.mockRejectedValue(new Error('offline'));
    render(<ExperienceLibraryPage />);

    const title = await screen.findByRole('textbox', { name: 'Title' });
    fireEvent.change(title, { target: { value: 'Still editing locally' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save experience' }));

    expect(await screen.findByText('offline')).toBeInTheDocument();
    expect(screen.getByRole('textbox', { name: 'Title' })).toHaveValue('Still editing locally');
  });

  it('renders the server 409 completeness and missing guidance without changing the draft', async () => {
    api.fetchExperience.mockResolvedValue({
      ...listItem,
      completeness: 20,
      evidence_items: [],
      missing_dimensions: [],
      suggested_questions: [],
    });
    api.markExperienceReady.mockRejectedValue({
      conflict: { completeness: 44, missing_dimensions: ['metrics'] },
    });
    render(<ExperienceLibraryPage />);

    await screen.findByRole('button', { name: 'Mark ready' });
    fireEvent.click(screen.getByRole('button', { name: 'Mark ready' }));

    expect(await screen.findByText('metrics')).toBeInTheDocument();
    expect(screen.getByText('Not ready')).toBeInTheDocument();
  });

  it('archives from the active list and restores from the recycle bin', async () => {
    const archived = {
      ...listItem,
      status: 'archived' as const,
      archived_at: '2025-02-01T00:00:00Z',
      evidence_items: [],
      missing_dimensions: [],
      suggested_questions: [],
    };
    api.listExperiences.mockImplementation(({ status }: { status?: string } = {}) =>
      Promise.resolve(
        status === 'archived' ? { items: [archived], total: 1 } : { items: [listItem], total: 1 }
      )
    );
    api.fetchExperience.mockImplementation((id: number) =>
      Promise.resolve(
        id === 1 && api.listExperiences.mock.calls.at(-1)?.[0]?.status === 'archived'
          ? archived
          : { ...listItem, evidence_items: [], missing_dimensions: [], suggested_questions: [] }
      )
    );
    api.archiveExperience.mockResolvedValue(archived);
    api.restoreExperience.mockResolvedValue({
      ...listItem,
      evidence_items: [],
      missing_dimensions: [],
      suggested_questions: [],
    });
    render(<ExperienceLibraryPage />);

    await screen.findByRole('button', { name: 'Archive experience' });
    fireEvent.click(screen.getByRole('button', { name: 'Archive experience' }));
    await waitFor(() =>
      expect(screen.queryByRole('button', { name: /Searchable project/ })).not.toBeInTheDocument()
    );
    fireEvent.click(screen.getByRole('tab', { name: 'Recycle bin' }));
    await screen.findByRole('button', { name: 'Restore experience' });
    fireEvent.click(screen.getByRole('button', { name: 'Restore experience' }));
    await waitFor(() => expect(api.restoreExperience).toHaveBeenCalledWith(1));
    expect(screen.queryByRole('button', { name: 'Restore experience' })).not.toBeInTheDocument();
  });

  it('loads deletion impact before allowing an archived experience to be permanently deleted', async () => {
    const archived = {
      ...listItem,
      status: 'archived' as const,
      archived_at: '2025-02-01T00:00:00Z',
      evidence_items: [],
      missing_dimensions: [],
      suggested_questions: [],
    };
    api.listExperiences.mockResolvedValue({ items: [archived], total: 1 });
    api.fetchExperience.mockResolvedValue(archived);
    api.getDeletionImpact.mockResolvedValue({
      affected_matches: [7],
      affected_resumes: ['resume-1'],
    });
    api.deleteExperiencePermanently.mockResolvedValue(undefined);
    render(<ExperienceLibraryPage />);
    fireEvent.click(await screen.findByRole('tab', { name: 'Recycle bin' }));

    fireEvent.click(await screen.findByRole('button', { name: 'Delete permanently' }));
    expect(await screen.findByText('1 affected matches')).toBeInTheDocument();
    expect(screen.getByText('resume-1')).toBeInTheDocument();
    fireEvent.click(screen.getAllByRole('button', { name: 'Delete permanently' }).at(-1)!);
    await waitFor(() => expect(api.deleteExperiencePermanently).toHaveBeenCalledWith(1));
    expect(screen.queryByRole('button', { name: /Searchable project/ })).not.toBeInTheDocument();
  });

  it('prompts before changing selection with a dirty draft and registers beforeunload only while dirty', async () => {
    const second = { ...listItem, experience_id: 2, title: 'Second experience' };
    api.listExperiences.mockResolvedValue({ items: [listItem, second], total: 2 });
    api.fetchExperience.mockImplementation((id: number) =>
      Promise.resolve({
        ...(id === 2 ? second : listItem),
        evidence_items: [],
        missing_dimensions: [],
        suggested_questions: [],
      })
    );
    render(<ExperienceLibraryPage />);

    const title = await screen.findByRole('textbox', { name: 'Title' });
    fireEvent.change(title, { target: { value: 'Unsaved title' } });
    const beforeUnload = new Event('beforeunload', { cancelable: true });
    window.dispatchEvent(beforeUnload);
    expect(beforeUnload.defaultPrevented).toBe(true);
    fireEvent.click(screen.getByRole('button', { name: /Second experience/ }));
    expect(
      await screen.findByRole('dialog', { name: /Discard unsaved changes/ })
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Discard edits' }));
    expect(await screen.findByRole('heading', { name: 'Second experience' })).toBeInTheDocument();
  });

  it('keeps a dirty metadata draft and query unchanged until filter discard is confirmed', async () => {
    const second = { ...listItem, experience_id: 2, title: 'Second experience' };
    api.listExperiences.mockResolvedValue({ items: [listItem, second], total: 2 });
    api.fetchExperience.mockImplementation((id: number) =>
      Promise.resolve({
        ...(id === 2 ? second : listItem),
        evidence_items: [],
        missing_dimensions: [],
        suggested_questions: [],
      })
    );
    render(<ExperienceLibraryPage />);

    fireEvent.change(await screen.findByRole('textbox', { name: 'Title' }), {
      target: { value: 'Unsaved title' },
    });
    const search = screen.getByRole('textbox', { name: 'Search experiences' });
    fireEvent.change(search, { target: { value: 'Second' } });

    expect(
      await screen.findByRole('dialog', { name: /Discard unsaved changes/ })
    ).toBeInTheDocument();
    expect(search).toHaveValue('');
    fireEvent.click(screen.getByRole('button', { name: 'Discard edits' }));
    expect(search).toHaveValue('Second');
    expect(await screen.findByRole('heading', { name: 'Second experience' })).toBeInTheDocument();
  });

  it('keeps a dirty metadata draft and kind unchanged when filter discard is cancelled', async () => {
    const volunteer = {
      ...listItem,
      experience_id: 2,
      kind: 'volunteer' as const,
      title: 'Volunteer event',
    };
    api.listExperiences.mockResolvedValue({ items: [listItem, volunteer], total: 2 });
    render(<ExperienceLibraryPage />);

    fireEvent.change(await screen.findByRole('textbox', { name: 'Title' }), {
      target: { value: 'Unsaved title' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Kind' }));
    fireEvent.click(screen.getByRole('menuitemradio', { name: 'Volunteer' }));

    expect(
      await screen.findByRole('dialog', { name: /Discard unsaved changes/ })
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    expect(screen.getByRole('button', { name: 'Kind' })).toHaveTextContent('All kinds');
    expect(screen.getByRole('heading', { name: 'Searchable project' })).toBeInTheDocument();
  });

  it('protects unsaved evidence edits during selection and beforeunload', async () => {
    const second = { ...listItem, experience_id: 2, title: 'Second experience' };
    api.listExperiences.mockResolvedValue({ items: [listItem, second], total: 2 });
    api.fetchExperience.mockImplementation((id: number) =>
      Promise.resolve({
        ...(id === 2 ? second : listItem),
        evidence_items: [],
        missing_dimensions: [],
        suggested_questions: [],
      })
    );
    render(<ExperienceLibraryPage />);

    fireEvent.change(await screen.findByLabelText('Action new'), {
      target: { value: 'Unsaved evidence' },
    });
    const beforeUnload = new Event('beforeunload', { cancelable: true });
    window.dispatchEvent(beforeUnload);
    expect(beforeUnload.defaultPrevented).toBe(true);
    fireEvent.click(screen.getByRole('button', { name: /Second experience/ }));
    expect(
      await screen.findByRole('dialog', { name: /Discard unsaved changes/ })
    ).toBeInTheDocument();
  });

  it('does not let an older A detail response replace a saved A response after A to B to A selection', async () => {
    const second = { ...listItem, experience_id: 2, title: 'Second experience' };
    const firstA = deferred<ExperienceDetail>();
    const savedA = {
      ...listItem,
      title: 'Saved A title',
      updated_at: '2025-02-01T00:00:00Z',
      evidence_items: [],
      missing_dimensions: [],
      suggested_questions: [],
    };
    const staleA = {
      ...listItem,
      title: 'Stale A title',
      updated_at: '2025-01-01T00:00:00Z',
      evidence_items: [],
      missing_dimensions: [],
      suggested_questions: [],
    };
    api.listExperiences.mockResolvedValue({ items: [listItem, second], total: 2 });
    api.fetchExperience
      .mockReturnValueOnce(firstA.promise)
      .mockResolvedValueOnce({
        ...second,
        evidence_items: [],
        missing_dimensions: [],
        suggested_questions: [],
      })
      .mockResolvedValueOnce({
        ...listItem,
        evidence_items: [],
        missing_dimensions: [],
        suggested_questions: [],
      });
    api.patchExperience.mockResolvedValue(savedA);
    render(<ExperienceLibraryPage />);

    await screen.findByRole('button', { name: /Searchable project/ });
    fireEvent.click(screen.getByRole('button', { name: /Second experience/ }));
    await screen.findByRole('heading', { name: 'Second experience' });
    fireEvent.click(screen.getByRole('button', { name: /Searchable project/ }));
    const title = await screen.findByRole('textbox', { name: 'Title' });
    fireEvent.change(title, { target: { value: 'Saved A title' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save experience' }));
    await screen.findByDisplayValue('Saved A title');

    firstA.resolve(staleA);
    await waitFor(() =>
      expect(screen.queryByDisplayValue('Stale A title')).not.toBeInTheDocument()
    );
  });

  it('ignores a late ready conflict for A after B becomes selected', async () => {
    const second = { ...listItem, experience_id: 2, title: 'Second experience' };
    const pendingReady = deferred<ExperienceDetail>();
    api.listExperiences.mockResolvedValue({ items: [listItem, second], total: 2 });
    api.fetchExperience.mockImplementation((id: number) =>
      Promise.resolve({
        ...(id === 2 ? second : listItem),
        evidence_items: [],
        missing_dimensions: [],
        suggested_questions: [],
      })
    );
    api.markExperienceReady.mockReturnValue(pendingReady.promise);
    render(<ExperienceLibraryPage />);

    fireEvent.click(await screen.findByRole('button', { name: 'Mark ready' }));
    fireEvent.click(screen.getByRole('button', { name: /Second experience/ }));
    await screen.findByRole('heading', { name: 'Second experience' });
    pendingReady.reject({ conflict: { completeness: 44, missing_dimensions: ['metrics'] } });

    await waitFor(() => expect(screen.queryByText('Not ready')).not.toBeInTheDocument());
    expect(screen.queryByText('metrics')).not.toBeInTheDocument();
  });

  it('does not clear B when a late archive for A completes and prevents double archive submits', async () => {
    const second = { ...listItem, experience_id: 2, title: 'Second experience' };
    const pendingArchive = deferred<ExperienceDetail>();
    api.listExperiences.mockResolvedValue({ items: [listItem, second], total: 2 });
    api.fetchExperience.mockImplementation((id: number) =>
      Promise.resolve({
        ...(id === 2 ? second : listItem),
        evidence_items: [],
        missing_dimensions: [],
        suggested_questions: [],
      })
    );
    api.archiveExperience.mockReturnValue(pendingArchive.promise);
    render(<ExperienceLibraryPage />);

    const archive = await screen.findByRole('button', { name: 'Archive experience' });
    fireEvent.click(archive);
    fireEvent.click(archive);
    expect(api.archiveExperience).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByRole('button', { name: /Second experience/ }));
    await screen.findByRole('heading', { name: 'Second experience' });
    pendingArchive.resolve({
      ...listItem,
      status: 'archived',
      archived_at: '2025-02-01T00:00:00Z',
      evidence_items: [],
      missing_dimensions: [],
      suggested_questions: [],
    });

    expect(await screen.findByRole('heading', { name: 'Second experience' })).toBeInTheDocument();
  });

  it('preserves a dirty evidence card through an unrelated metadata save and still guards navigation', async () => {
    const second = { ...listItem, experience_id: 2, title: 'Second experience' };
    const detail = {
      ...listItem,
      evidence_ids: [7],
      evidence_items: [
        {
          id: 7,
          action: 'Original action',
          result: 'Original result',
          metrics: null,
          created_at: '2025-01-01T00:00:00Z',
          updated_at: '2025-01-01T00:00:00Z',
        },
      ],
      missing_dimensions: [],
      suggested_questions: [],
    };
    api.listExperiences.mockResolvedValue({ items: [listItem, second], total: 2 });
    api.fetchExperience.mockImplementation((id: number) =>
      Promise.resolve(
        id === 1
          ? detail
          : { ...second, evidence_items: [], missing_dimensions: [], suggested_questions: [] }
      )
    );
    api.patchExperience.mockResolvedValue({
      ...detail,
      title: 'Metadata saved',
      evidence_items: [...detail.evidence_items],
    });
    render(<ExperienceLibraryPage />);

    fireEvent.change(await screen.findByLabelText('Action 7'), {
      target: { value: 'Unsaved action' },
    });
    fireEvent.change(screen.getByRole('textbox', { name: 'Title' }), {
      target: { value: 'Metadata saved' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Save experience' }));

    expect(await screen.findByLabelText('Action 7')).toHaveValue('Unsaved action');
    fireEvent.click(screen.getByRole('button', { name: /Second experience/ }));
    expect(
      await screen.findByRole('dialog', { name: /Discard unsaved changes/ })
    ).toBeInTheDocument();
  });

  it('preserves a dirty evidence card through mark-ready and resets it on explicit discard', async () => {
    const detail = {
      ...listItem,
      evidence_ids: [7],
      evidence_items: [
        {
          id: 7,
          action: 'Original action',
          result: 'Original result',
          metrics: null,
          created_at: '2025-01-01T00:00:00Z',
          updated_at: '2025-01-01T00:00:00Z',
        },
      ],
      missing_dimensions: [],
      suggested_questions: [],
    };
    api.fetchExperience.mockResolvedValue(detail);
    api.markExperienceReady.mockResolvedValue({
      ...detail,
      status: 'ready',
      evidence_items: [...detail.evidence_items],
    });
    render(<ExperienceLibraryPage />);

    fireEvent.change(await screen.findByLabelText('Action 7'), {
      target: { value: 'Unsaved action' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Mark ready' }));
    expect(await screen.findByLabelText('Action 7')).toHaveValue('Unsaved action');
    fireEvent.click(screen.getByRole('button', { name: 'Back to list' }));
    expect(
      await screen.findByRole('dialog', { name: /Discard unsaved changes/ })
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Discard edits' }));
    expect(screen.getByLabelText('Action 7')).toHaveValue('Original action');
  });

  it('does not show a late A conflict as B generic ready error', async () => {
    const second = { ...listItem, experience_id: 2, title: 'Second experience' };
    const pendingReady = deferred<ExperienceDetail>();
    api.listExperiences.mockResolvedValue({ items: [listItem, second], total: 2 });
    api.fetchExperience.mockImplementation((id: number) =>
      Promise.resolve({
        ...(id === 2 ? second : listItem),
        evidence_items: [],
        missing_dimensions: [],
        suggested_questions: [],
      })
    );
    api.markExperienceReady.mockReturnValue(pendingReady.promise);
    render(<ExperienceLibraryPage />);

    fireEvent.click(await screen.findByRole('button', { name: 'Mark ready' }));
    fireEvent.click(screen.getByRole('button', { name: /Second experience/ }));
    await screen.findByRole('heading', { name: 'Second experience' });
    pendingReady.reject({ conflict: { completeness: 44, missing_dimensions: ['metrics'] } });

    await waitFor(() => expect(screen.queryByText('Could not mark ready')).not.toBeInTheDocument());
    expect(screen.queryByText('metrics')).not.toBeInTheDocument();
  });

  it('clears an A ready error when selecting B', async () => {
    const second = { ...listItem, experience_id: 2, title: 'Second experience' };
    api.listExperiences.mockResolvedValue({ items: [listItem, second], total: 2 });
    api.fetchExperience.mockImplementation((id: number) =>
      Promise.resolve({
        ...(id === 2 ? second : listItem),
        evidence_items: [],
        missing_dimensions: [],
        suggested_questions: [],
      })
    );
    api.markExperienceReady.mockRejectedValue(new Error('ready failed'));
    render(<ExperienceLibraryPage />);

    fireEvent.click(await screen.findByRole('button', { name: 'Mark ready' }));
    expect(await screen.findByText('ready failed')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /Second experience/ }));
    await screen.findByRole('heading', { name: 'Second experience' });
    expect(screen.queryByText('ready failed')).not.toBeInTheDocument();
  });

  it('starts AI only on request, applies one answer, and offers the returned next question', async () => {
    const enriched = {
      ...listItem,
      title: 'Organized project',
      evidence_items: [],
      missing_dimensions: [],
      suggested_questions: [],
      next_question: {
        question_id: 'next-2',
        question: 'What result did it achieve?',
        is_fallback: false,
      },
    };
    api.requestNextExperienceQuestion.mockResolvedValue({
      question_id: 'start-1',
      question: 'What did you personally build?',
      is_fallback: true,
    });
    api.submitExperienceAnswer.mockResolvedValue(enriched);
    render(<ExperienceLibraryPage />);

    await screen.findByRole('button', { name: 'Help me organize with AI' });
    expect(api.requestNextExperienceQuestion).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: 'Help me organize with AI' }));

    expect(await screen.findByText('What did you personally build?')).toBeInTheDocument();
    expect(screen.getByText('Suggested question')).toBeInTheDocument();
    fireEvent.change(screen.getByRole('textbox', { name: 'Your answer' }), {
      target: { value: 'I designed the search service.' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Apply answer' }));

    await waitFor(() =>
      expect(api.submitExperienceAnswer).toHaveBeenCalledWith(1, {
        question_id: 'start-1',
        answer: 'I designed the search service.',
      })
    );
    expect(await screen.findByRole('heading', { name: 'Organized project' })).toBeInTheDocument();
    expect(screen.getByText('What result did it achieve?')).toBeInTheDocument();
    expect(screen.getByRole('textbox', { name: 'Your answer' })).toHaveValue('');
    expect(
      screen.getByText('You can edit this experience manually at any time.')
    ).toBeInTheDocument();
  });

  it('keeps manual editing available and retries the current AI request after an error', async () => {
    api.requestNextExperienceQuestion
      .mockRejectedValueOnce(new Error('offline'))
      .mockResolvedValueOnce({
        question_id: 'retry-1',
        question: 'What changed?',
        is_fallback: false,
      });
    render(<ExperienceLibraryPage />);

    await screen.findByRole('button', { name: 'Help me organize with AI' });
    fireEvent.click(screen.getByRole('button', { name: 'Help me organize with AI' }));
    expect(
      await screen.findByText(
        'AI could not organize this experience. You can keep editing it manually.'
      )
    ).toBeInTheDocument();
    expect(screen.getByRole('textbox', { name: 'Title' })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Try again' }));
    expect(await screen.findByText('What changed?')).toBeInTheDocument();
  });

  it('ignores a late AI answer after the user selects a different experience', async () => {
    const second = { ...listItem, experience_id: 2, title: 'Second experience' };
    const pendingAnswer = deferred<ExperienceDetail & { next_question: null }>();
    api.listExperiences.mockResolvedValue({ items: [listItem, second], total: 2 });
    api.fetchExperience.mockImplementation((id: number) =>
      Promise.resolve({
        ...(id === 2 ? second : listItem),
        evidence_items: [],
        missing_dimensions: [],
        suggested_questions: [],
      })
    );
    api.requestNextExperienceQuestion.mockResolvedValue({
      question_id: 'late-1',
      question: 'What did you build?',
      is_fallback: false,
    });
    api.submitExperienceAnswer.mockReturnValue(pendingAnswer.promise);
    render(<ExperienceLibraryPage />);

    fireEvent.click(await screen.findByRole('button', { name: 'Help me organize with AI' }));
    await screen.findByText('What did you build?');
    fireEvent.change(screen.getByRole('textbox', { name: 'Your answer' }), {
      target: { value: 'A service.' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Apply answer' }));
    fireEvent.click(screen.getByRole('button', { name: /Second experience/ }));
    await screen.findByRole('heading', { name: 'Second experience' });

    pendingAnswer.resolve({
      ...listItem,
      title: 'Late AI overwrite',
      evidence_items: [],
      missing_dimensions: [],
      suggested_questions: [],
      next_question: null,
    });

    await waitFor(() =>
      expect(screen.queryByRole('heading', { name: 'Late AI overwrite' })).not.toBeInTheDocument()
    );
    expect(screen.getByRole('heading', { name: 'Second experience' })).toBeInTheDocument();
  });

  it('exposes the active and archived views as ARIA tabs', async () => {
    render(<ExperienceLibraryPage />);
    expect(await screen.findByRole('tab', { name: 'Active', selected: true })).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: 'Recycle bin', selected: false })).toBeInTheDocument();
  });

  it('uses roving tab focus and arrow keys to switch library views', async () => {
    render(<ExperienceLibraryPage />);
    const active = await screen.findByRole('tab', { name: 'Active', selected: true });
    const archived = screen.getByRole('tab', { name: 'Recycle bin', selected: false });

    expect(active).toHaveAttribute('tabindex', '0');
    expect(archived).toHaveAttribute('tabindex', '-1');
    active.focus();
    fireEvent.keyDown(active, { key: 'ArrowRight' });

    expect(await screen.findByRole('tab', { name: 'Recycle bin', selected: true })).toHaveFocus();
    expect(screen.getByRole('tab', { name: 'Active', selected: false })).toHaveAttribute(
      'tabindex',
      '-1'
    );

    fireEvent.keyDown(screen.getByRole('tab', { name: 'Recycle bin' }), { key: 'ArrowLeft' });
    expect(await screen.findByRole('tab', { name: 'Active', selected: true })).toHaveFocus();
  });

  it('applies AI question and answer responses in StrictMode and clears the answer pending state', async () => {
    const pendingAnswer = deferred<ExperienceDetail & { next_question: null }>();
    api.requestNextExperienceQuestion.mockResolvedValue({
      question_id: 'strict-1',
      question: 'Which outcome mattered most?',
      is_fallback: false,
    });
    api.submitExperienceAnswer.mockReturnValue(pendingAnswer.promise);
    render(
      <StrictMode>
        <ExperienceLibraryPage />
      </StrictMode>
    );

    fireEvent.click(await screen.findByRole('button', { name: 'Help me organize with AI' }));
    await screen.findByText('Which outcome mattered most?');
    fireEvent.change(screen.getByRole('textbox', { name: 'Your answer' }), {
      target: { value: 'Reduced response time.' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Apply answer' }));
    expect(await screen.findByText('Applying answer')).toBeInTheDocument();

    pendingAnswer.resolve({
      ...listItem,
      title: 'Strict mode update',
      evidence_items: [],
      missing_dimensions: [],
      suggested_questions: [],
      next_question: null,
    });

    expect(await screen.findByRole('heading', { name: 'Strict mode update' })).toBeInTheDocument();
    expect(screen.queryByText('Applying answer')).not.toBeInTheDocument();
  });

  it('submits an AI answer only once while the answer request is pending', async () => {
    const pendingAnswer = deferred<ExperienceDetail & { next_question: null }>();
    api.requestNextExperienceQuestion.mockResolvedValue({
      question_id: 'double-1',
      question: 'What did you improve?',
      is_fallback: false,
    });
    api.submitExperienceAnswer.mockReturnValue(pendingAnswer.promise);
    render(<ExperienceLibraryPage />);

    fireEvent.click(await screen.findByRole('button', { name: 'Help me organize with AI' }));
    await screen.findByText('What did you improve?');
    fireEvent.change(screen.getByRole('textbox', { name: 'Your answer' }), {
      target: { value: 'Search speed.' },
    });
    const submit = screen.getByRole('button', { name: 'Apply answer' });
    fireEvent.click(submit);
    fireEvent.click(submit);

    expect(api.submitExperienceAnswer).toHaveBeenCalledTimes(1);
  });

  it('does not apply a late AI answer after the selected detail becomes dirty', async () => {
    const pendingAnswer = deferred<ExperienceDetail & { next_question: null }>();
    api.requestNextExperienceQuestion.mockResolvedValue({
      question_id: 'dirty-1',
      question: 'What was your contribution?',
      is_fallback: false,
    });
    api.submitExperienceAnswer.mockReturnValue(pendingAnswer.promise);
    render(<ExperienceLibraryPage />);

    fireEvent.click(await screen.findByRole('button', { name: 'Help me organize with AI' }));
    await screen.findByText('What was your contribution?');
    fireEvent.change(screen.getByRole('textbox', { name: 'Your answer' }), {
      target: { value: 'I owned the launch.' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Apply answer' }));
    fireEvent.change(screen.getByRole('textbox', { name: 'Title' }), {
      target: { value: 'Unsaved local title' },
    });

    pendingAnswer.resolve({
      ...listItem,
      title: 'Late AI overwrite',
      evidence_items: [],
      missing_dimensions: [],
      suggested_questions: [],
      next_question: null,
    });

    await waitFor(() =>
      expect(screen.queryByRole('heading', { name: 'Late AI overwrite' })).not.toBeInTheDocument()
    );
    expect(screen.getByRole('textbox', { name: 'Title' })).toHaveValue('Unsaved local title');
  });
});
