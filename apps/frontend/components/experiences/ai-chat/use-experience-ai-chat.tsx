'use client';

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type PropsWithChildren,
} from 'react';
import { useQueryClient } from '@tanstack/react-query';
import {
  closeExperienceConversation,
  createExperienceConversation,
  eventExperienceDetail,
  eventExperienceProposal,
  resolveExperienceInteraction,
  streamExperienceMessage,
  streamExperienceOpening,
  type ExperienceChatScope,
  type ExperienceChangeScope,
  type ExperienceProposal,
} from '@/lib/api/experience-ai-chat';
import type { RuntimeEvent } from '@/lib/api/runtime-events';
import { writeExperienceDetail } from '@/lib/queries/experiences/cache';

export type ChatPhase = 'idle' | 'generating' | 'ready' | 'approval';
export type ChatMessage = { id: string; role: 'user' | 'assistant'; content: string };

interface ExperienceAiChatValue {
  phase: ChatPhase;
  scope: ExperienceChatScope | null;
  messages: ChatMessage[];
  proposal: ExperienceProposal | null;
  input: string;
  error: string | null;
  lastBusinessEvent: RuntimeEvent | null;
  lastBusinessResult: Record<string, unknown> | null;
  setInput: (value: string) => void;
  start: (scope: ExperienceChatScope) => Promise<void>;
  send: () => Promise<void>;
  resolve: (decision: 'approve' | 'reject') => Promise<void>;
  close: (reason?: string) => Promise<void>;
  isScopeLocked: (scope: ExperienceChangeScope) => boolean;
}

const ExperienceAiChatContext = createContext<ExperienceAiChatValue | null>(null);

function sameChangeScope(left: ExperienceChangeScope | null, right: ExperienceChangeScope) {
  return Boolean(left && left.field === right.field && left.evidence_id === right.evidence_id);
}

function clientId(prefix: string): string {
  return `${prefix}:${globalThis.crypto?.randomUUID?.() ?? `${Date.now()}-${Math.random()}`}`;
}

function yieldToRenderer(): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, 0));
}

export function ExperienceAiChatProvider({
  experienceId,
  children,
}: PropsWithChildren<{ experienceId: number }>) {
  const queryClient = useQueryClient();
  const [phase, setPhase] = useState<ChatPhase>('idle');
  const [scope, setScope] = useState<ExperienceChatScope | null>(null);
  const [conversationId, setConversationId] = useState<number | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [proposal, setProposal] = useState<ExperienceProposal | null>(null);
  const [input, setInput] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [lastBusinessEvent, setLastBusinessEvent] = useState<RuntimeEvent | null>(null);
  const streamRef = useRef<AbortController | null>(null);
  const conversationRef = useRef<number | null>(null);

  const applyEvent = useCallback(
    (event: RuntimeEvent) => {
      if (event.type === 'run.started') {
        setMessages((current) => [
          ...current,
          { id: clientId('assistant'), role: 'assistant', content: '' },
        ]);
        return;
      }
      if (event.type === 'output.delta') {
        const text = typeof event.payload.text === 'string' ? event.payload.text : '';
        setMessages((current) => {
          const next = [...current];
          const last = next.at(-1);
          if (last?.role === 'assistant')
            next[next.length - 1] = { ...last, content: last.content + text };
          return next;
        });
        return;
      }
      if (event.type === 'run.completed') {
        setPhase('ready');
        return;
      }
      if (event.type === 'interaction.resolved') {
        setProposal(null);
        return;
      }
      const requested = eventExperienceProposal(event);
      if (requested) {
        streamRef.current?.abort();
        setMessages((current) => {
          const last = current.at(-1);
          return last?.role === 'assistant' && last.content.length === 0
            ? current.slice(0, -1)
            : current;
        });
        setProposal(requested);
        setPhase('approval');
        return;
      }
      if (event.type === 'result.available' && event.payload.kind === 'tool_result') {
        const detail = eventExperienceDetail(event);
        if (detail) writeExperienceDetail(queryClient, detail);
        setLastBusinessEvent(event);
        setProposal(null);
        return;
      }
      if (event.type === 'run.failed') {
        setError('response_failed');
        setPhase('ready');
      }
    },
    [queryClient]
  );

  const consume = useCallback(
    async (
      events: AsyncGenerator<RuntimeEvent>,
      controller: AbortController,
      silentError = false,
      onEvent?: (event: RuntimeEvent) => void
    ) => {
      try {
        for await (const event of events) {
          onEvent?.(event);
          applyEvent(event);
          // 同一个网络包里可能包含多个 delta。让出一个浏览器任务，避免 React
          // 将它们全部批处理成一次最终渲染，保证文本仍然逐段可见。
          if (event.type === 'output.delta') await yieldToRenderer();
        }
      } catch (reason) {
        if (!controller.signal.aborted) {
          if (!silentError) {
            setError(reason instanceof Error ? reason.message : 'response_failed');
            setPhase('ready');
          }
        }
      } finally {
        if (streamRef.current === controller) streamRef.current = null;
      }
    },
    [applyEvent]
  );

  const close = useCallback(async (reason = 'left_field') => {
    streamRef.current?.abort();
    streamRef.current = null;
    const current = conversationRef.current;
    conversationRef.current = null;
    setConversationId(null);
    setScope(null);
    setProposal(null);
    setMessages([]);
    setPhase('idle');
    setError(null);
    if (current !== null) {
      await closeExperienceConversation(current, reason).catch(() => undefined);
    }
  }, []);

  const start = useCallback(
    async (nextScope: ExperienceChatScope) => {
      await close('changed_field');
      setScope(nextScope);
      setMessages([]);
      setError(null);
      setPhase('generating');
      try {
        const conversation = await createExperienceConversation(experienceId, nextScope);
        conversationRef.current = conversation.conversation_id;
        setConversationId(conversation.conversation_id);
        const controller = new AbortController();
        streamRef.current = controller;
        await consume(
          streamExperienceOpening(conversation.conversation_id, controller.signal),
          controller
        );
      } catch (reason) {
        conversationRef.current = null;
        setConversationId(null);
        setScope(null);
        setError(reason instanceof Error ? reason.message : 'response_failed');
        setPhase('idle');
      }
    },
    [close, consume, experienceId]
  );

  const send = useCallback(async () => {
    const content = input.trim();
    if (phase !== 'ready' || conversationId === null || proposal !== null || !content) return;
    setInput('');
    setError(null);
    setMessages((current) => [...current, { id: clientId('user'), role: 'user', content }]);
    setPhase('generating');
    const controller = new AbortController();
    streamRef.current = controller;
    await consume(
      streamExperienceMessage(conversationId, content, clientId('message'), controller.signal),
      controller
    );
  }, [consume, conversationId, input, phase, proposal]);

  const resolve = useCallback(
    async (decision: 'approve' | 'reject') => {
      if (phase === 'generating' || proposal === null) return;
      const currentProposal = proposal;
      let accepted = false;
      setPhase('generating');
      setError(null);
      const controller = new AbortController();
      streamRef.current = controller;
      await consume(
        resolveExperienceInteraction(
          currentProposal.run_id,
          currentProposal.interaction_id,
          decision,
          clientId('resolution'),
          controller.signal
        ),
        controller,
        true,
        (event) => {
          if (event.type === 'interaction.resolved') accepted = true;
        }
      );
      if (!controller.signal.aborted) setPhase(accepted ? 'ready' : 'approval');
    },
    [consume, phase, proposal]
  );

  useEffect(() => () => void close('left_page'), [close]);

  const value = useMemo<ExperienceAiChatValue>(() => {
    const result = lastBusinessEvent?.payload.result;
    const lastBusinessResult =
      result && typeof result === 'object' ? (result as Record<string, unknown>) : null;
    return {
      phase,
      scope,
      messages,
      proposal,
      input,
      error,
      lastBusinessEvent,
      lastBusinessResult,
      setInput,
      start,
      send,
      resolve,
      close,
      isScopeLocked: (candidate) =>
        phase === 'approval' && sameChangeScope(proposal?.proposal.scope ?? null, candidate),
    };
  }, [
    close,
    error,
    input,
    lastBusinessEvent,
    messages,
    phase,
    proposal,
    resolve,
    send,
    start,
    scope,
  ]);
  return (
    <ExperienceAiChatContext.Provider value={value}>{children}</ExperienceAiChatContext.Provider>
  );
}

export function useExperienceAiChat(): ExperienceAiChatValue {
  const value = useContext(ExperienceAiChatContext);
  if (!value) throw new Error('useExperienceAiChat must be used inside ExperienceAiChatProvider');
  return value;
}
