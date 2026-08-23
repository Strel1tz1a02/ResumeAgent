import type { Metadata } from 'next';
import './(default)/css/globals.css';

export const metadata: Metadata = {
  title: 'ResumeAgent',
  description: 'Build evidence-grounded resumes for real job applications.',
  applicationName: 'ResumeAgent',
  keywords: ['resume', 'agent', 'job search', 'evidence', 'application'],
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en-US" className="h-full" suppressHydrationWarning>
      <body className="antialiased bg-background text-ink-soft min-h-full">{children}</body>
    </html>
  );
}
