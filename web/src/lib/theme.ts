import { useEffect, useState } from 'react';

export type Theme = 'light' | 'dark';

/**
 * WebGL colours cannot read CSS variables, so the graph needs to know the theme
 * as a value. Observed from the `dark` class rather than from state, so it stays
 * correct however the class was set (toggle, palette command, or the inline
 * bootstrap script in index.html).
 */
export function useIsDark(): boolean {
  const [dark, setDark] = useState(
    () => typeof document !== 'undefined' && document.documentElement.classList.contains('dark'),
  );

  useEffect(() => {
    const root = document.documentElement;
    const observer = new MutationObserver(() => setDark(root.classList.contains('dark')));
    observer.observe(root, { attributes: true, attributeFilter: ['class'] });
    setDark(root.classList.contains('dark'));
    return () => observer.disconnect();
  }, []);

  return dark;
}

const KEY = 'provenance.theme';

export function readStoredTheme(): Theme | null {
  try {
    const value = localStorage.getItem(KEY);
    return value === 'dark' || value === 'light' ? value : null;
  } catch {
    return null;
  }
}

export function systemTheme(): Theme {
  if (typeof window === 'undefined' || !window.matchMedia) return 'light';
  return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
}

export function resolveInitialTheme(): Theme {
  return readStoredTheme() ?? systemTheme();
}

export function applyTheme(theme: Theme): void {
  const root = document.documentElement;
  root.classList.toggle('dark', theme === 'dark');
  root.style.colorScheme = theme;
  try {
    localStorage.setItem(KEY, theme);
  } catch {
    /* preference simply will not persist */
  }
}
