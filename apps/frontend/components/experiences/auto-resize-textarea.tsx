'use client';

import { useLayoutEffect, useRef } from 'react';
import { Textarea, type TextareaProps } from '@/components/ui/textarea';
import { cn } from '@/lib/utils';

interface AutoResizeTextareaProps extends TextareaProps {
  minRows?: number;
}

function fitContent(element: HTMLTextAreaElement | null) {
  if (!element) return;
  element.style.height = 'auto';
  if (element.scrollHeight > 0) element.style.height = `${element.scrollHeight}px`;
}

export function AutoResizeTextarea({
  value,
  onChange,
  className,
  minRows = 3,
  ...props
}: AutoResizeTextareaProps) {
  const ref = useRef<HTMLTextAreaElement>(null);

  useLayoutEffect(() => fitContent(ref.current), [value]);

  return (
    <Textarea
      {...props}
      ref={ref}
      rows={minRows}
      value={value}
      onChange={(event) => {
        fitContent(event.currentTarget);
        onChange?.(event);
      }}
      className={cn(
        'resize-none overflow-hidden whitespace-pre-wrap break-words leading-relaxed',
        className
      )}
    />
  );
}
