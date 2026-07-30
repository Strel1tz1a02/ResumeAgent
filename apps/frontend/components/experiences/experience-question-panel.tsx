'use client';

import { useEffect, useRef, useState, type KeyboardEvent } from 'react';
import Loader2 from 'lucide-react/dist/esm/icons/loader-2';
import { Button } from '@/components/ui/button';
import { Textarea } from '@/components/ui/textarea';
import type { ExperienceEnrichmentQuestion } from '@/lib/api/experiences';
import { useTranslations } from '@/lib/i18n';
import {
  useNextExperienceQuestionMutation,
  useSubmitExperienceAnswerMutation,
} from '@/lib/queries/experiences/mutations';

interface ExperienceQuestionPanelProps {
  experienceId: number;
  hasUnsavedChanges: boolean;
}

type RetryAction = 'question' | 'answer' | null;

/**
 * Holds only one in-memory question and answer. The persisted experience remains
 * the source of truth; no conversational state is written to browser or server storage.
 */
export function ExperienceQuestionPanel({
  experienceId,
  hasUnsavedChanges,
}: ExperienceQuestionPanelProps) {
  const { t } = useTranslations();
  const [question, setQuestion] = useState<ExperienceEnrichmentQuestion | null>(null);
  const [hasStarted, setHasStarted] = useState(false);
  const [answer, setAnswer] = useState('');
  const [retryAction, setRetryAction] = useState<RetryAction>(null);
  const dirtyRef = useRef(hasUnsavedChanges);
  const questionMutation = useNextExperienceQuestionMutation(experienceId);
  const answerMutation = useSubmitExperienceAnswerMutation(experienceId);
  const pending: RetryAction = questionMutation.isPending
    ? 'question'
    : answerMutation.isPending
      ? 'answer'
      : null;
  const error = questionMutation.error ?? answerMutation.error;

  useEffect(() => {
    dirtyRef.current = hasUnsavedChanges;
  }, [hasUnsavedChanges]);

  const loadQuestion = () => {
    if (pending || hasUnsavedChanges) return;
    setHasStarted(true);
    setRetryAction(null);
    questionMutation.mutate(undefined, {
      onSuccess: (nextQuestion) => {
        if (dirtyRef.current) return;
        setQuestion(nextQuestion);
        setAnswer('');
      },
      onError: () => {
        if (!dirtyRef.current) setRetryAction('question');
      },
    });
  };

  const submitAnswer = () => {
    if (pending || hasUnsavedChanges || !question || !answer.trim()) return;
    const currentQuestion = question;
    const currentAnswer = answer.trim();
    setRetryAction(null);
    answerMutation.mutate(
      {
        question_id: currentQuestion.question_id,
        answer: currentAnswer,
        evidence_id: currentQuestion.evidence_id,
      },
      {
        onSuccess: (response) => {
          if (dirtyRef.current) return;
          setQuestion(response.next_question);
          setAnswer('');
        },
        onError: () => {
          if (!dirtyRef.current) setRetryAction('answer');
        },
      }
    );
  };

  const retry = () => {
    if (retryAction === 'answer') void submitAnswer();
    if (retryAction === 'question') void loadQuestion();
  };

  const controlsDisabled = Boolean(pending) || hasUnsavedChanges;
  const stopTextareaEnter = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === 'Enter') event.stopPropagation();
  };

  return (
    <section
      className="border border-black bg-paper-tint p-4"
      aria-label={t('experiences.ai.title')}
    >
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h3 className="font-mono text-xs font-bold uppercase tracking-widest text-ink-soft">
            {t('experiences.ai.title')}
          </h3>
          <p className="mt-1 text-sm text-ink-soft">{t('experiences.ai.manual')}</p>
        </div>
        {!hasStarted && !question && !pending && !error && (
          <Button size="sm" onClick={() => void loadQuestion()} disabled={controlsDisabled}>
            {t('experiences.ai.start')}
          </Button>
        )}
      </div>
      {pending === 'question' && (
        <p className="mt-4 flex items-center gap-2 font-mono text-xs uppercase text-ink-soft">
          <Loader2 className="h-4 w-4 animate-spin" /> {t('experiences.ai.loading')}
        </p>
      )}
      {question && (
        <div className="mt-4 space-y-3">
          <div className="flex flex-wrap items-center gap-2">
            <p className="font-serif text-lg font-bold">{question.question}</p>
            {question.is_fallback && (
              <span className="border border-black bg-secondary px-2 py-1 font-mono text-[10px] font-bold uppercase tracking-wide">
                {t('experiences.ai.fallback')}
              </span>
            )}
          </div>
          <Textarea
            aria-label={t('experiences.ai.answer')}
            value={answer}
            onChange={(event) => setAnswer(event.target.value)}
            onKeyDown={stopTextareaEnter}
            disabled={controlsDisabled}
            rows={4}
          />
          <div className="flex flex-wrap gap-2">
            <Button
              size="sm"
              onClick={() => void submitAnswer()}
              disabled={controlsDisabled || !answer.trim()}
            >
              {pending === 'answer' ? (
                <>
                  <Loader2 className="h-4 w-4 animate-spin" /> {t('experiences.ai.answering')}
                </>
              ) : (
                t('experiences.ai.submit')
              )}
            </Button>
          </div>
        </div>
      )}
      {hasStarted && !question && !pending && !error && retryAction === null && (
        <Button
          className="mt-4"
          size="sm"
          variant="outline"
          onClick={() => void loadQuestion()}
          disabled={controlsDisabled}
        >
          {t('experiences.ai.next')}
        </Button>
      )}
      {error && (
        <div className="mt-4 flex flex-wrap items-center gap-3 border border-destructive bg-red-50 p-3">
          <p className="flex-1 font-mono text-xs text-destructive">{t('experiences.ai.error')}</p>
          <Button size="sm" variant="outline" onClick={retry} disabled={controlsDisabled}>
            {t('experiences.ai.retry')}
          </Button>
        </div>
      )}
    </section>
  );
}
