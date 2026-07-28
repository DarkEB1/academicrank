import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { HashRouter } from 'react-router-dom';
import { App } from './App';
import { ErrorBoundary } from './components/ErrorBoundary';
import { SessionProvider } from './lib/session';
import { ApiError } from './lib/api';
import './index.css';

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      refetchOnWindowFocus: false,
      // A cold profile's first ranking warms the ego walks server-side. Hammering
      // it with retries makes that slower, not faster.
      retry: (failureCount, error) => {
        if (error instanceof ApiError && error.status < 500) return false;
        return failureCount < 2;
      },
      retryDelay: (attempt) => Math.min(4000, 500 * 2 ** attempt),
    },
    mutations: { retry: false },
  },
});

const container = document.getElementById('root');
if (!container) throw new Error('#root is missing from index.html');

createRoot(container).render(
  <StrictMode>
    <ErrorBoundary section="Provenance">
      <QueryClientProvider client={queryClient}>
        <SessionProvider>
          {/* Hash routing so `dist/` works from any static file server with no
              rewrite rules. See FRONTEND_NOTES.md. */}
          <HashRouter>
            <App />
          </HashRouter>
        </SessionProvider>
      </QueryClientProvider>
    </ErrorBoundary>
  </StrictMode>,
);
