'use client';

import Link from 'next/link';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import ArrowLeft from 'lucide-react/dist/esm/icons/arrow-left';
import Loader2 from 'lucide-react/dist/esm/icons/loader-2';
import Plus from 'lucide-react/dist/esm/icons/plus';
import RefreshCw from 'lucide-react/dist/esm/icons/refresh-cw';
import Trash2 from 'lucide-react/dist/esm/icons/trash-2';
import { Button } from '@/components/ui/button';
import { ConfirmDialog } from '@/components/ui/confirm-dialog';
import { Input } from '@/components/ui/input';
import { Textarea } from '@/components/ui/textarea';
import {
  addJDRequirement,
  createJDConversation,
  deleteJDImport,
  deleteJDRequirement,
  listJDImports,
  resolveJDQuestions,
  streamJDImport,
  updateJDImport,
  updateJDRequirement,
  type JDImport,
  type JDQuestionAnswer,
  type JDQuestionBatch,
  type JDRequirement,
  type JDRequirementPriority,
  type JDStatus,
} from '@/lib/api/jd-imports';
import type { RuntimeEvent } from '@/lib/api/runtime-events';
import { useTranslations } from '@/lib/i18n';

type WorkspaceView = 'import' | 'library';
type ImportState = 'idle' | 'running' | 'questions' | 'completed' | 'failed';

interface MetadataDraft {
  source_url: string;
  company: string;
  job_name: string;
  type: string;
  location: string;
  status: JDStatus;
}

interface RequirementDraft {
  priority: JDRequirementPriority;
  content: string;
  sort_order: number;
}

function clientId(prefix: string): string {
  const suffix =
    typeof crypto !== 'undefined' && crypto.randomUUID
      ? crypto.randomUUID()
      : `${Date.now()}-${Math.random().toString(16).slice(2)}`;
  return `${prefix}-${suffix}`;
}

function metadataDraft(item: JDImport): MetadataDraft {
  return {
    source_url: item.source_url ?? '',
    company: item.company,
    job_name: item.job_name,
    type: item.type,
    location: item.location,
    status: item.status,
  };
}

function eventQuestionBatch(event: RuntimeEvent): JDQuestionBatch | null {
  if (
    event.type !== 'interaction.requested' ||
    event.payload.kind !== 'question_batch' ||
    typeof event.run_id !== 'number' ||
    typeof event.payload.interaction_id !== 'number'
  ) {
    return null;
  }
  const request = event.payload.request;
  if (!request || typeof request !== 'object') return null;
  return {
    ...(request as Omit<JDQuestionBatch, 'run_id' | 'interaction_id'>),
    run_id: event.run_id,
    interaction_id: event.payload.interaction_id,
  };
}

function eventPersistedIds(event: RuntimeEvent): number[] | null {
  if (event.type !== 'result.available' || event.payload.kind !== 'jd_import') return null;
  const result = event.payload.result;
  const ids =
    result && typeof result === 'object' ? (result as Record<string, unknown>).persisted_ids : null;
  return Array.isArray(ids) ? ids.filter((id): id is number => typeof id === 'number') : [];
}

function FieldLabel({ children, htmlFor }: { children: React.ReactNode; htmlFor?: string }) {
  return (
    <label htmlFor={htmlFor} className="mb-1 block font-mono text-xs uppercase tracking-wider">
      {children}
    </label>
  );
}

function Select({
  value,
  onChange,
  children,
  ariaLabel,
}: {
  value: string;
  onChange: (value: string) => void;
  children: React.ReactNode;
  ariaLabel: string;
}) {
  return (
    <select
      aria-label={ariaLabel}
      value={value}
      onChange={(event) => onChange(event.target.value)}
      className="h-10 w-full rounded-none border border-black bg-background px-3 font-mono text-sm focus:outline-none focus:ring-1 focus:ring-primary"
    >
      {children}
    </select>
  );
}

function ImportPanel({ onImported }: { onImported: (ids: number[]) => Promise<void> }) {
  const { t } = useTranslations();
  const [content, setContent] = useState('');
  const [state, setState] = useState<ImportState>('idle');
  const [conversationId, setConversationId] = useState<number | null>(null);
  const [batch, setBatch] = useState<JDQuestionBatch | null>(null);
  const [answers, setAnswers] = useState<Record<string, { value: string; skipped: boolean }>>({});
  const [error, setError] = useState('');
  const [completedCount, setCompletedCount] = useState(0);
  const abortRef = useRef<AbortController | null>(null);

  useEffect(() => () => abortRef.current?.abort(), []);

  const consume = async (events: AsyncGenerator<RuntimeEvent>) => {
    for await (const event of events) {
      const nextBatch = eventQuestionBatch(event);
      if (nextBatch) {
        setBatch(nextBatch);
        setAnswers(
          Object.fromEntries(
            nextBatch.questions.map((question) => [
              question.question_id,
              { value: '', skipped: false },
            ])
          )
        );
        setState('questions');
      }
      const persistedIds = eventPersistedIds(event);
      if (persistedIds) {
        setBatch(null);
        setCompletedCount(persistedIds.length);
        setState('completed');
        await onImported(persistedIds);
      }
      if (event.type === 'run.failed') {
        throw new Error(t('jdImports.import.errors.execution'));
      }
    }
  };

  const startImport = async () => {
    if (!content.trim() || state === 'running') return;
    setError('');
    setBatch(null);
    setState('running');
    const controller = new AbortController();
    abortRef.current = controller;
    try {
      const nextConversationId = await createJDConversation();
      setConversationId(nextConversationId);
      await consume(
        streamJDImport(nextConversationId, content, clientId('jd-import'), controller.signal)
      );
    } catch (reason) {
      if (controller.signal.aborted) return;
      setError(reason instanceof Error ? reason.message : t('jdImports.import.errors.execution'));
      setState('failed');
    }
  };

  const allAnswered = Boolean(
    batch?.questions.every((question) => {
      const answer = answers[question.question_id];
      return answer?.skipped || Boolean(answer?.value.trim());
    })
  );

  const submitAnswers = async () => {
    if (!batch || conversationId === null || !allAnswered) return;
    setError('');
    setState('running');
    const controller = new AbortController();
    abortRef.current = controller;
    const payload: JDQuestionAnswer[] = batch.questions.map((question) => {
      const answer = answers[question.question_id];
      return answer.skipped
        ? { question_id: question.question_id, skipped: true }
        : { question_id: question.question_id, value: answer.value.trim(), skipped: false };
    });
    try {
      await consume(
        resolveJDQuestions(
          batch.run_id,
          batch.interaction_id,
          batch.batch_id,
          payload,
          clientId('jd-answer'),
          controller.signal
        )
      );
    } catch (reason) {
      if (controller.signal.aborted) return;
      setError(reason instanceof Error ? reason.message : t('jdImports.import.errors.execution'));
      setState('failed');
    }
  };

  return (
    <section className="grid min-h-[34rem] md:grid-cols-[minmax(0,1.3fr)_minmax(18rem,0.7fr)]">
      <div className="border-b border-black p-4 md:border-b-0 md:border-r md:p-6">
        <div className="mb-4 flex items-center gap-2">
          <span className="h-3 w-3 bg-primary" />
          <span className="font-mono text-xs uppercase tracking-wider">
            {t('jdImports.import.inputLabel')}
          </span>
        </div>
        <Textarea
          aria-label={t('jdImports.import.inputLabel')}
          value={content}
          onChange={(event) => setContent(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === 'Enter') event.stopPropagation();
          }}
          disabled={state === 'running' || state === 'questions'}
          placeholder={t('jdImports.import.placeholder')}
          className="min-h-[25rem] resize-y bg-white font-sans leading-6"
        />
        <div className="mt-4 flex flex-wrap items-center justify-between gap-3">
          <p className="font-mono text-xs text-ink-soft">{t('jdImports.import.hint')}</p>
          <Button
            onClick={() => void startImport()}
            disabled={!content.trim() || state === 'running' || state === 'questions'}
          >
            {state === 'running' ? <Loader2 className="h-4 w-4 animate-spin" /> : null}
            {state === 'running' ? t('jdImports.import.processing') : t('jdImports.import.submit')}
          </Button>
        </div>
      </div>

      <div className="bg-paper-tint p-4 md:p-6">
        <div className="mb-4 flex items-center gap-2">
          <span
            className={`h-3 w-3 ${state === 'failed' ? 'bg-destructive' : state === 'completed' ? 'bg-success' : state === 'questions' ? 'bg-warning' : 'bg-steel-grey'}`}
          />
          <span className="font-mono text-xs uppercase tracking-wider">
            {t('jdImports.import.statusLabel')}
          </span>
        </div>
        {state === 'idle' && (
          <p className="font-sans text-sm text-ink-soft">{t('jdImports.import.idle')}</p>
        )}
        {state === 'running' && (
          <div className="border-2 border-primary bg-blue-50 p-4">
            <p className="font-mono text-sm font-bold uppercase text-primary">
              {t('jdImports.import.processing')}
            </p>
            <p className="mt-2 font-sans text-sm">{t('jdImports.import.processingHint')}</p>
          </div>
        )}
        {state === 'completed' && (
          <div className="border-2 border-success bg-green-50 p-4">
            <p className="font-mono text-sm font-bold uppercase text-success">
              {t('jdImports.import.completed')}
            </p>
            <p className="mt-2 font-sans text-sm">
              {t('jdImports.import.completedCount', { count: String(completedCount) })}
            </p>
          </div>
        )}
        {error && (
          <div
            role="alert"
            className="border-2 border-destructive bg-red-50 p-4 font-sans text-sm text-destructive"
          >
            {error}
          </div>
        )}
        {batch && state === 'questions' && (
          <div>
            <p className="mb-4 font-mono text-xs uppercase text-warning">
              {t('jdImports.import.questionRound', { round: String(batch.round) })}
            </p>
            <div className="space-y-4">
              {batch.questions.map((question, index) => {
                const answer = answers[question.question_id] ?? { value: '', skipped: false };
                return (
                  <div key={question.question_id} className="border border-black bg-white p-4">
                    <p className="font-mono text-xs text-ink-soft">
                      {String(index + 1).padStart(2, '0')}
                    </p>
                    <p className="mt-1 font-sans text-sm font-medium">{question.prompt}</p>
                    {question.mode === 'choice' && question.options.length > 0 && (
                      <div className="mt-3 flex flex-wrap gap-2">
                        {question.options.map((option) => (
                          <Button
                            key={option}
                            size="sm"
                            variant={
                              answer.value === option && !answer.skipped ? 'default' : 'outline'
                            }
                            onClick={() =>
                              setAnswers((current) => ({
                                ...current,
                                [question.question_id]: { value: option, skipped: false },
                              }))
                            }
                          >
                            {option}
                          </Button>
                        ))}
                      </div>
                    )}
                    {(question.mode === 'text' || question.allow_custom) && (
                      <Input
                        className="mt-3 bg-background"
                        aria-label={question.prompt}
                        value={answer.value}
                        disabled={answer.skipped}
                        onChange={(event) =>
                          setAnswers((current) => ({
                            ...current,
                            [question.question_id]: { value: event.target.value, skipped: false },
                          }))
                        }
                        placeholder={t('jdImports.import.answerPlaceholder')}
                      />
                    )}
                    <button
                      type="button"
                      className="mt-2 font-mono text-xs uppercase text-ink-soft underline hover:text-ink"
                      onClick={() =>
                        setAnswers((current) => ({
                          ...current,
                          [question.question_id]: { value: '', skipped: !answer.skipped },
                        }))
                      }
                    >
                      {answer.skipped
                        ? t('jdImports.import.restoreAnswer')
                        : t('jdImports.import.skip')}
                    </button>
                  </div>
                );
              })}
            </div>
            <Button
              className="mt-4 w-full"
              onClick={() => void submitAnswers()}
              disabled={!allAnswered}
            >
              {t('jdImports.import.submitAnswers')}
            </Button>
          </div>
        )}
      </div>
    </section>
  );
}

function RequirementEditor({
  item,
  informationRevision,
  onChanged,
}: {
  item: JDRequirement;
  informationRevision: number;
  onChanged: (next: JDImport) => void;
}) {
  const { t } = useTranslations();
  const [draft, setDraft] = useState<RequirementDraft>({
    priority: item.priority,
    content: item.content,
    sort_order: item.sort_order,
  });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  useEffect(
    () => setDraft({ priority: item.priority, content: item.content, sort_order: item.sort_order }),
    [item]
  );

  const run = async (action: () => Promise<JDImport>) => {
    setBusy(true);
    setError('');
    try {
      onChanged(await action());
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : t('jdImports.library.errors.save'));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="border border-black bg-white p-3">
      <div className="grid gap-2 md:grid-cols-[9rem_5rem_minmax(0,1fr)_auto]">
        <Select
          ariaLabel={t('jdImports.library.priority')}
          value={draft.priority}
          onChange={(value) =>
            setDraft((current) => ({ ...current, priority: value as JDRequirementPriority }))
          }
        >
          <option value="required">{t('jdImports.priority.required')}</option>
          <option value="preferred">{t('jdImports.priority.preferred')}</option>
          <option value="normal">{t('jdImports.priority.normal')}</option>
        </Select>
        <Input
          aria-label={t('jdImports.library.sortOrder')}
          type="number"
          min={0}
          value={draft.sort_order}
          onChange={(event) =>
            setDraft((current) => ({ ...current, sort_order: Number(event.target.value) }))
          }
        />
        <Input
          aria-label={t('jdImports.library.requirementContent')}
          value={draft.content}
          onChange={(event) => setDraft((current) => ({ ...current, content: event.target.value }))}
        />
        <div className="flex gap-2">
          <Button
            size="sm"
            variant="outline"
            disabled={busy || !draft.content.trim()}
            onClick={() =>
              void run(() =>
                updateJDRequirement(item.jd_information_id, item, draft, informationRevision)
              )
            }
          >
            {t('common.save')}
          </Button>
          <Button
            size="icon"
            variant="destructive"
            disabled={busy}
            aria-label={t('jdImports.library.deleteRequirement')}
            onClick={() =>
              void run(() => deleteJDRequirement(item.jd_information_id, item, informationRevision))
            }
          >
            <Trash2 className="h-4 w-4" />
          </Button>
        </div>
      </div>
      {error && (
        <p role="alert" className="mt-2 font-mono text-xs text-destructive">
          {error}
        </p>
      )}
    </div>
  );
}

function LibraryPanel({
  items,
  loading,
  selectedId,
  onSelect,
  onRefresh,
  onItemChanged,
  onDeleted,
}: {
  items: JDImport[];
  loading: boolean;
  selectedId: number | null;
  onSelect: (id: number) => void;
  onRefresh: () => Promise<void>;
  onItemChanged: (item: JDImport) => void;
  onDeleted: (id: number) => void;
}) {
  const { t } = useTranslations();
  const [query, setQuery] = useState('');
  const [status, setStatus] = useState<JDStatus | 'all'>('all');
  const selected = items.find((item) => item.id === selectedId) ?? null;
  const [draft, setDraft] = useState<MetadataDraft | null>(
    selected ? metadataDraft(selected) : null
  );
  const [newRequirement, setNewRequirement] = useState<RequirementDraft>({
    priority: 'normal',
    content: '',
    sort_order: 0,
  });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [deleteOpen, setDeleteOpen] = useState(false);

  useEffect(() => setDraft(selected ? metadataDraft(selected) : null), [selected]);

  const filtered = useMemo(() => {
    const normalized = query.trim().toLocaleLowerCase();
    return items.filter((item) => {
      if (status !== 'all' && item.status !== status) return false;
      if (!normalized) return true;
      return [item.company, item.job_name, item.type, item.location].some((value) =>
        value.toLocaleLowerCase().includes(normalized)
      );
    });
  }, [items, query, status]);

  const mutate = async (action: () => Promise<JDImport>) => {
    setBusy(true);
    setError('');
    try {
      onItemChanged(await action());
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : t('jdImports.library.errors.save'));
    } finally {
      setBusy(false);
    }
  };

  const saveMetadata = async () => {
    if (!selected || !draft) return;
    await mutate(() =>
      updateJDImport(
        selected.id,
        { ...draft, source_url: draft.source_url.trim() || null },
        selected.revision
      )
    );
  };

  const addRequirement = async () => {
    if (!selected || !newRequirement.content.trim()) return;
    await mutate(async () => {
      const next = await addJDRequirement(
        selected.id,
        { ...newRequirement, content: newRequirement.content.trim() },
        selected.revision
      );
      setNewRequirement({ priority: 'normal', content: '', sort_order: next.requirements.length });
      return next;
    });
  };

  const confirmDelete = async () => {
    if (!selected) return;
    setBusy(true);
    setError('');
    try {
      await deleteJDImport(selected.id);
      onDeleted(selected.id);
      setDeleteOpen(false);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : t('jdImports.library.errors.delete'));
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="grid min-h-[38rem] md:grid-cols-[minmax(17rem,0.72fr)_minmax(0,1.5fr)]">
      <aside className="border-b border-black md:border-b-0 md:border-r">
        <div className="grid gap-2 border-b border-black p-4">
          <div className="flex gap-2">
            <Input
              aria-label={t('jdImports.library.search')}
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder={t('jdImports.library.search')}
            />
            <Button
              size="icon"
              variant="outline"
              aria-label={t('common.refresh')}
              disabled={loading}
              onClick={() => void onRefresh()}
            >
              {loading ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                <RefreshCw className="h-4 w-4" />
              )}
            </Button>
          </div>
          <Select
            ariaLabel={t('jdImports.library.statusFilter')}
            value={status}
            onChange={(value) => setStatus(value as JDStatus | 'all')}
          >
            <option value="all">{t('jdImports.library.allStatuses')}</option>
            <option value="confirmed">{t('jdImports.status.confirmed')}</option>
            <option value="incomplete">{t('jdImports.status.incomplete')}</option>
          </Select>
        </div>
        <div className="max-h-[34rem] overflow-y-auto">
          {!loading && filtered.length === 0 && (
            <p className="p-6 font-sans text-sm text-ink-soft">{t('jdImports.library.empty')}</p>
          )}
          {filtered.map((item, index) => (
            <button
              key={item.id}
              type="button"
              onClick={() => onSelect(item.id)}
              className={`block w-full border-b border-black p-4 text-left hover:bg-blue-50 ${item.id === selectedId ? 'bg-blue-50' : 'bg-background'}`}
            >
              <div className="flex items-start justify-between gap-3">
                <span className="font-mono text-xs text-ink-soft">
                  {String(index + 1).padStart(2, '0')}
                </span>
                <span
                  className={`border border-black px-2 py-0.5 font-mono text-[10px] uppercase ${item.status === 'confirmed' ? 'bg-green-50 text-success' : 'bg-orange-50 text-warning'}`}
                >
                  {t(`jdImports.status.${item.status}`)}
                </span>
              </div>
              <p className="mt-2 font-serif text-lg font-bold">
                {item.job_name || t('jdImports.library.untitled')}
              </p>
              <p className="mt-1 font-sans text-sm text-ink-soft">
                {item.company || t('jdImports.library.unknownCompany')}
              </p>
              <p className="mt-3 font-mono text-xs text-ink-soft">
                {t('jdImports.library.requirementCount', {
                  count: String(item.requirements.length),
                })}
              </p>
            </button>
          ))}
        </div>
      </aside>
      <div className="p-4 md:p-6">
        {!selected || !draft ? (
          <div className="flex min-h-72 items-center justify-center border border-black bg-paper-tint p-8 font-mono text-sm uppercase text-ink-soft">
            {t('jdImports.library.selectHint')}
          </div>
        ) : (
          <div>
            <div className="flex flex-wrap items-start justify-between gap-4 border-b-2 border-black pb-4">
              <div>
                <p className="font-mono text-xs uppercase text-primary">
                  JD #{selected.id} · REV {selected.revision}
                </p>
                <h2 className="mt-2 font-serif text-2xl font-bold">
                  {selected.job_name || t('jdImports.library.untitled')}
                </h2>
              </div>
              <Button variant="destructive" size="sm" onClick={() => setDeleteOpen(true)}>
                <Trash2 className="h-4 w-4" />
                {t('common.delete')}
              </Button>
            </div>
            <div className="mt-6 grid gap-4 md:grid-cols-2">
              {(['company', 'job_name', 'type', 'location'] as const).map((field) => (
                <div key={field}>
                  <FieldLabel htmlFor={`jd-${field}`}>{t(`jdImports.fields.${field}`)}</FieldLabel>
                  <Input
                    id={`jd-${field}`}
                    value={draft[field]}
                    onChange={(event) =>
                      setDraft((current) =>
                        current ? { ...current, [field]: event.target.value } : current
                      )
                    }
                  />
                </div>
              ))}
              <div className="md:col-span-2">
                <FieldLabel htmlFor="jd-source-url">{t('jdImports.fields.source_url')}</FieldLabel>
                <Input
                  id="jd-source-url"
                  type="url"
                  value={draft.source_url}
                  onChange={(event) =>
                    setDraft((current) =>
                      current ? { ...current, source_url: event.target.value } : current
                    )
                  }
                />
              </div>
              <div>
                <FieldLabel>{t('jdImports.fields.status')}</FieldLabel>
                <Select
                  ariaLabel={t('jdImports.fields.status')}
                  value={draft.status}
                  onChange={(value) =>
                    setDraft((current) =>
                      current ? { ...current, status: value as JDStatus } : current
                    )
                  }
                >
                  <option value="incomplete">{t('jdImports.status.incomplete')}</option>
                  <option value="confirmed">{t('jdImports.status.confirmed')}</option>
                </Select>
              </div>
            </div>
            {error && (
              <p
                role="alert"
                className="mt-4 border-2 border-destructive bg-red-50 p-3 font-sans text-sm text-destructive"
              >
                {error}
              </p>
            )}
            <Button
              className="mt-4"
              variant="success"
              disabled={busy}
              onClick={() => void saveMetadata()}
            >
              {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : null}
              {t('jdImports.library.saveMetadata')}
            </Button>
            <div className="mt-10 border-t-2 border-black pt-4">
              <div className="flex items-end justify-between gap-4">
                <div>
                  <p className="font-mono text-xs uppercase text-primary">
                    {t('jdImports.library.requirementsKicker')}
                  </p>
                  <h3 className="mt-1 font-serif text-xl font-bold">
                    {t('jdImports.library.requirements')}
                  </h3>
                </div>
                <span className="font-mono text-xs text-ink-soft">
                  {selected.requirements.length}
                </span>
              </div>
              <div className="mt-4 space-y-3">
                {selected.requirements
                  .slice()
                  .sort((a, b) => a.sort_order - b.sort_order)
                  .map((item) => (
                    <RequirementEditor
                      key={`${item.id}-${item.revision}`}
                      item={item}
                      informationRevision={selected.revision}
                      onChanged={onItemChanged}
                    />
                  ))}
              </div>
              <div className="mt-4 border-2 border-black bg-paper-tint p-4">
                <p className="mb-3 font-mono text-xs font-bold uppercase">
                  {t('jdImports.library.addRequirement')}
                </p>
                <div className="grid gap-2 md:grid-cols-[9rem_5rem_minmax(0,1fr)_auto]">
                  <Select
                    ariaLabel={t('jdImports.library.priority')}
                    value={newRequirement.priority}
                    onChange={(value) =>
                      setNewRequirement((current) => ({
                        ...current,
                        priority: value as JDRequirementPriority,
                      }))
                    }
                  >
                    <option value="required">{t('jdImports.priority.required')}</option>
                    <option value="preferred">{t('jdImports.priority.preferred')}</option>
                    <option value="normal">{t('jdImports.priority.normal')}</option>
                  </Select>
                  <Input
                    aria-label={t('jdImports.library.sortOrder')}
                    type="number"
                    min={0}
                    value={newRequirement.sort_order}
                    onChange={(event) =>
                      setNewRequirement((current) => ({
                        ...current,
                        sort_order: Number(event.target.value),
                      }))
                    }
                  />
                  <Input
                    aria-label={t('jdImports.library.requirementContent')}
                    value={newRequirement.content}
                    onChange={(event) =>
                      setNewRequirement((current) => ({ ...current, content: event.target.value }))
                    }
                    placeholder={t('jdImports.library.requirementPlaceholder')}
                  />
                  <Button
                    disabled={busy || !newRequirement.content.trim()}
                    onClick={() => void addRequirement()}
                  >
                    <Plus className="h-4 w-4" />
                    {t('common.add')}
                  </Button>
                </div>
              </div>
            </div>
            <ConfirmDialog
              open={deleteOpen}
              onOpenChange={setDeleteOpen}
              title={t('jdImports.library.deleteTitle')}
              description={t('jdImports.library.deleteDescription', {
                title: selected.job_name || t('jdImports.library.untitled'),
              })}
              confirmLabel={t('common.delete')}
              variant="danger"
              closeOnConfirm={false}
              confirmDisabled={busy}
              onConfirm={() => void confirmDelete()}
            />
          </div>
        )}
      </div>
    </section>
  );
}

export function JDImportWorkspace() {
  const { t } = useTranslations();
  const [view, setView] = useState<WorkspaceView>('library');
  const [items, setItems] = useState<JDImport[]>([]);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState('');

  const refresh = useCallback(async () => {
    setLoading(true);
    setLoadError('');
    try {
      const result = await listJDImports();
      setItems(result.items);
      setSelectedId((current) =>
        current && result.items.some((item) => item.id === current)
          ? current
          : (result.items[0]?.id ?? null)
      );
    } catch (reason) {
      setLoadError(reason instanceof Error ? reason.message : t('jdImports.library.errors.load'));
    } finally {
      setLoading(false);
    }
  }, [t]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const onImported = async (ids: number[]) => {
    await refresh();
    if (ids[0]) setSelectedId(ids[0]);
    setView('library');
  };

  const onItemChanged = (next: JDImport) =>
    setItems((current) => current.map((item) => (item.id === next.id ? next : item)));
  const onDeleted = (id: number) => {
    setItems((current) => {
      const next = current.filter((item) => item.id !== id);
      setSelectedId(next[0]?.id ?? null);
      return next;
    });
  };

  return (
    <main
      className="min-h-[100dvh] bg-background px-4 py-6 md:px-8"
      style={{
        backgroundImage:
          'linear-gradient(rgba(29, 78, 216, 0.1) 1px, transparent 1px), linear-gradient(90deg, rgba(29, 78, 216, 0.1) 1px, transparent 1px)',
        backgroundSize: '40px 40px',
      }}
    >
      <div className="mx-auto w-full max-w-[104rem] border border-black bg-background shadow-sw-lg">
        <header className="flex flex-col gap-4 border-b border-black p-4 md:flex-row md:items-end md:justify-between md:p-6">
          <div>
            <Link
              href="/dashboard"
              className="mb-3 inline-flex items-center gap-1 font-mono text-xs uppercase text-ink-soft hover:text-primary"
            >
              <ArrowLeft className="h-3.5 w-3.5" />
              {t('nav.backToDashboard')}
            </Link>
            <p className="font-mono text-xs uppercase tracking-widest text-primary">
              {t('jdImports.kicker')}
            </p>
            <h1 className="mt-2 font-serif text-3xl font-bold tracking-tight md:text-4xl">
              {t('jdImports.title')}
            </h1>
            <p className="mt-2 max-w-2xl font-sans text-sm text-ink-soft">
              {t('jdImports.subtitle')}
            </p>
          </div>
          <div className="font-mono text-xs uppercase text-ink-soft">
            {t('jdImports.total', { count: String(items.length) })}
          </div>
        </header>
        <div
          role="tablist"
          aria-label={t('jdImports.views')}
          className="flex gap-2 border-b border-black p-4 md:p-6"
        >
          <Button
            role="tab"
            aria-selected={view === 'import'}
            variant={view === 'import' ? 'default' : 'outline'}
            onClick={() => setView('import')}
          >
            {t('jdImports.tabs.import')}
          </Button>
          <Button
            role="tab"
            aria-selected={view === 'library'}
            variant={view === 'library' ? 'default' : 'outline'}
            onClick={() => setView('library')}
          >
            {t('jdImports.tabs.library')}
          </Button>
        </div>
        {loadError && (
          <div
            role="alert"
            className="border-b border-destructive bg-red-50 p-4 font-sans text-sm text-destructive"
          >
            {loadError}
          </div>
        )}
        {view === 'import' ? (
          <ImportPanel onImported={onImported} />
        ) : (
          <LibraryPanel
            items={items}
            loading={loading}
            selectedId={selectedId}
            onSelect={setSelectedId}
            onRefresh={refresh}
            onItemChanged={onItemChanged}
            onDeleted={onDeleted}
          />
        )}
      </div>
    </main>
  );
}
