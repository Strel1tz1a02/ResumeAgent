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
  resolveExperienceProposal,
  streamExperienceMessage,
  streamExperienceOpening,
  type ExperienceChatEvent,
  type ExperienceChatTarget,
  type ExperienceProposal,
} from '@/lib/api/experience-ai-chat';
import { writeExperienceDetail } from '@/lib/queries/experiences/cache';

export type ChatPhase = 'idle' | 'generating' | 'ready' | 'approval';
export type ChatMessage = { id: string; role: 'user' | 'assistant'; content: string };

interface ExperienceAiChatValue {
  phase: ChatPhase;
  target: ExperienceChatTarget | null;
  messages: ChatMessage[];
  proposal: ExperienceProposal | null;
  input: string;
  error: string | null;
  lastBusinessEvent: ExperienceChatEvent | null;
  setInput: (value: string) => void;
  start: (target: ExperienceChatTarget) => Promise<void>;
  send: () => Promise<void>;
  resolve: (decision: 'approve' | 'reject') => Promise<void>;
  close: (reason?: string) => Promise<void>;
  isTargetLocked: (target: ExperienceChatTarget) => boolean;
}

const ExperienceAiChatContext = createContext<ExperienceAiChatValue | null>(null);

function sameTarget(left: ExperienceChatTarget | null, right: ExperienceChatTarget): boolean {
  return Boolean(left && left.key === right.key && left.ref_id === right.ref_id);
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
  const [target, setTarget] = useState<ExperienceChatTarget | null>(null);
  const [conversationId, setConversationId] = useState<number | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [proposal, setProposal] = useState<ExperienceProposal | null>(null);
  const [input, setInput] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [lastBusinessEvent, setLastBusinessEvent] = useState<ExperienceChatEvent | null>(null);
  const streamRef = useRef<AbortController | null>(null);
  const conversationRef = useRef<number | null>(null);

  const applyEvent = useCallback(
    (event: ExperienceChatEvent) => {
      if (event.event === 'assistant.started') {
        setMessages((current) => [
          ...current,
          { id: clientId('assistant'), role: 'assistant', content: '' },
        ]);
        return;
      }
      if (event.event === 'assistant.delta') {
        const text = typeof event.data.text === 'string' ? event.data.text : '';
        setMessages((current) => {
          const next = [...current];
          const last = next.at(-1);
          if (last?.role === 'assistant')
            next[next.length - 1] = { ...last, content: last.content + text };
          return next;
        });
        return;
      }
      if (event.event === 'assistant.completed') {
        setPhase('ready');
        return;
      }
      if (event.event.endsWith('.requested')) {
        streamRef.current?.abort();
        setProposal(event.data as unknown as ExperienceProposal);
        setPhase('approval');
        return;
      }
      if (/\.(applied|rejected|invalidated)$/.test(event.event)) {
        const detail = eventExperienceDetail(event);
        if (detail) writeExperienceDetail(queryClient, detail);
        setLastBusinessEvent(event);
        setProposal(null);
        return;
      }
      if (event.event === 'chat.error') {
        setError('response_failed');
        setPhase('ready');
      }
    },
    [queryClient]
  );

  const consume = useCallback(
    async (
      events: AsyncGenerator<ExperienceChatEvent>,
      controller: AbortController,
      silentError = false
    ) => {
      try {
        for await (const event of events) {
          applyEvent(event);
          // 同一个网络包里可能包含多个 delta。让出一个浏览器任务，避免 React
          // 将它们全部批处理成一次最终渲染，保证文本仍然逐段可见。
          if (event.event === 'assistant.delta') await yieldToRenderer();
        }
      } catch (reason) {
        if (!controller.signal.aborted) {
          if (!silentError) {
            setError(reason instanceof Error ? reason.message : 'response_failed');
          }
          setPhase('ready');
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
    setTarget(null);
    setProposal(null);
    setMessages([]);
    setPhase('idle');
    setError(null);
    if (current !== null) {
      await closeExperienceConversation(current, reason).catch(() => undefined);
    }
  }, []);

  const start = useCallback(
    async (nextTarget: ExperienceChatTarget) => {
      await close('changed_field');
      setTarget(nextTarget);
      setMessages([]);
      setError(null);
      setPhase('generating');
      try {
        const conversation = await createExperienceConversation(experienceId, nextTarget);
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
        setTarget(null);
        setError(reason instanceof Error ? reason.message : 'response_failed');
        setPhase('idle');
      }
    },
    [close, consume, experienceId]
  );

  const send = useCallback(async () => {
    const content = input.trim();
    if (phase !== 'ready' || conversationId === null || !content) return;
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
  }, [consume, conversationId, input, phase]);

  const resolve = useCallback(
    async (decision: 'approve' | 'reject') => {
      if (phase !== 'approval' || proposal === null) return;
      setPhase('generating');
      setError(null);
      const controller = new AbortController();
      streamRef.current = controller;
      await consume(
        resolveExperienceProposal(
          proposal.proposal_id,
          decision,
          clientId('resolution'),
          controller.signal
        ),
        controller,
        true
      );
      if (!controller.signal.aborted) setPhase('ready');
    },
    [consume, phase, proposal]
  );

  useEffect(() => () => void close('left_page'), [close]);

  const value = useMemo<ExperienceAiChatValue>(
    () => ({
      phase,
      target,
      messages,
      proposal,
      input,
      error,
      lastBusinessEvent,
      setInput,
      start,
      send,
      resolve,
      close,
      isTargetLocked: (candidate) =>
        phase === 'approval' && sameTarget(proposal?.proposal.target ?? target, candidate),
    }),
    [
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
      target,
    ]
  );
  return (
    <ExperienceAiChatContext.Provider value={value}>{children}</ExperienceAiChatContext.Provider>
  );
}

export function useExperienceAiChat(): ExperienceAiChatValue {
  const value = useContext(ExperienceAiChatContext);
  if (!value) throw new Error('useExperienceAiChat must be used inside ExperienceAiChatProvider');
  return value;
}
