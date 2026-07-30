import '@testing-library/jest-dom/vitest';

// jsdom has no WebGL, and sigma's bundle references the constructor NAME at
// module load. Defining it lets the module import; canvas.getContext('webgl')
// still returns null, so constructing a renderer still fails -- which is the
// exact no-WebGL environment GraphCanvas's fallback exists for.
if (!('WebGL2RenderingContext' in globalThis)) {
  (globalThis as Record<string, unknown>).WebGL2RenderingContext = class {};
}
if (!('WebGLRenderingContext' in globalThis)) {
  (globalThis as Record<string, unknown>).WebGLRenderingContext = class {};
}

// jsdom does not implement matchMedia, and the theme code asks for it on import.
if (!window.matchMedia) {
  window.matchMedia = ((query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addListener: () => {},
    removeListener: () => {},
    addEventListener: () => {},
    removeEventListener: () => {},
    dispatchEvent: () => false,
  })) as unknown as typeof window.matchMedia;
}
