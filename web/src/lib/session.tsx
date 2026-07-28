import { createContext, useContext, useEffect, useMemo, useRef, useState } from 'react';
import type { ReactNode } from 'react';
import { api, ApiError, clearSession, NetworkError, storeSession } from './api';
import type { ProfileMe } from './types';

/**
 * Anonymous profile bootstrap. There is no login: on first visit we create a
 * profile and keep its token in localStorage (the server also sets a pv_token
 * cookie). Everything else in the app needs `profileId`, so the whole tree
 * waits on this one call.
 */

export type SessionState =
  | { status: 'loading'; profile: null; error: null }
  | { status: 'ready'; profile: ProfileMe; error: null }
  | { status: 'error'; profile: null; error: Error };

type SessionContextValue = SessionState & {
  profileId: string | null;
  retry: () => void;
  reset: () => void;
};

const SessionContext = createContext<SessionContextValue | null>(null);

export function SessionProvider({ children }: { children: ReactNode }): JSX.Element {
  const [state, setState] = useState<SessionState>({
    status: 'loading',
    profile: null,
    error: null,
  });
  const [attempt, setAttempt] = useState(0);
  const inFlight = useRef(false);

  useEffect(() => {
    if (inFlight.current) return;
    inFlight.current = true;
    let cancelled = false;

    const bootstrap = async () => {
      setState({ status: 'loading', profile: null, error: null });
      try {
        let profile: ProfileMe;
        try {
          profile = await api.me();
        } catch (err) {
          // No usable identity yet (or a stale token): mint a fresh profile.
          if (err instanceof ApiError && (err.status === 401 || err.status === 403 || err.status === 404)) {
            clearSession();
            const created = await api.createProfile();
            storeSession(created.id, created.token);
            profile = await api.me();
          } else {
            throw err;
          }
        }
        if (!cancelled) setState({ status: 'ready', profile, error: null });
      } catch (err) {
        if (cancelled) return;
        const error =
          err instanceof NetworkError || err instanceof ApiError
            ? err
            : new Error('Unexpected error while starting a session.');
        setState({ status: 'error', profile: null, error });
      } finally {
        inFlight.current = false;
      }
    };

    void bootstrap();
    return () => {
      cancelled = true;
    };
  }, [attempt]);

  const value = useMemo<SessionContextValue>(
    () => ({
      ...state,
      profileId: state.status === 'ready' ? state.profile.id : null,
      retry: () => setAttempt((n) => n + 1),
      reset: () => {
        clearSession();
        setAttempt((n) => n + 1);
      },
    }),
    [state],
  );

  return <SessionContext.Provider value={value}>{children}</SessionContext.Provider>;
}

export function useSession(): SessionContextValue {
  const ctx = useContext(SessionContext);
  if (!ctx) throw new Error('useSession must be used inside <SessionProvider>');
  return ctx;
}

/** For screens that only run once the session is ready. */
export function useProfileId(): string {
  const { profileId } = useSession();
  if (!profileId) throw new Error('useProfileId used before the session was ready');
  return profileId;
}
