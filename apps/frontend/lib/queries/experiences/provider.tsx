'use client';

import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { useState, type PropsWithChildren } from 'react';

export function createExperienceQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: {
        retry: false,
        refetchOnWindowFocus: false,
        refetchOnReconnect: false,
      },
      mutations: {
        retry: false,
      },
    },
  });
}

export function ExperienceQueryProvider({ children }: PropsWithChildren) {
  const [client] = useState(createExperienceQueryClient);
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}
