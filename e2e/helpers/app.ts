import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { expect, type ConsoleMessage, type Locator, type Page } from '@playwright/test';
import type { WarmProfile } from '../global-setup';

const here = path.dirname(fileURLToPath(import.meta.url));
export const SHOTS_DIR = path.join(here, '..', 'screenshots');
export const WARM_STATE = path.join(here, '..', '.auth', 'warm.json');
export const EMPTY_STATE = { cookies: [], origins: [] } as const;

export function warmProfile(): WarmProfile {
  const file = path.join(here, '..', '.auth', 'warm-profile.json');
  return JSON.parse(fs.readFileSync(file, 'utf8')) as WarmProfile;
}

/** Full-page screenshot into `e2e/screenshots/`. Screenshots are a deliverable. */
export async function shot(page: Page, name: string): Promise<void> {
  fs.mkdirSync(SHOTS_DIR, { recursive: true });
  // Let fonts, KaTeX and any in-flight layout settle so the image is not a
  // half-painted frame.
  await page.evaluate(() => document.fonts?.ready);
  await page.waitForTimeout(250);
  await page.screenshot({ path: path.join(SHOTS_DIR, name), fullPage: true });
}

/** Navigate to a hash route and wait for the app shell to be interactive. */
export async function gotoRoute(page: Page, hash: string): Promise<void> {
  await page.goto(`/#${hash}`, { waitUntil: 'domcontentloaded' });
  await expect(page.getByRole('navigation', { name: 'Primary' })).toBeVisible();
}

/**
 * The rankings table is rendered only once the (potentially very slow) first
 * ranking arrives. Waits on a real row rather than a spinner disappearing.
 */
export async function waitForRankingRows(page: Page, timeout = 180_000): Promise<Locator> {
  const explain = page.getByRole('button', { name: /^Explain the score for/ });
  await expect(explain.first()).toBeVisible({ timeout });
  return explain;
}

/** Ordered list of paper ids as currently rendered in the parameter playground top-20. */
export async function paramsTopTwenty(page: Page): Promise<string[]> {
  return page.evaluate(() => {
    const list = document.querySelector('ol.divide-y');
    if (!list) return [];
    return Array.from(list.querySelectorAll('li a[href*="#/paper/"]')).map((a) => {
      const href = (a as HTMLAnchorElement).getAttribute('href') ?? '';
      return href.split('#/paper/')[1] ?? href;
    });
  });
}

/**
 * Console/page error collector.
 *
 * `ignore` exists for messages that are genuinely not application faults —
 * currently only the browser's own WebGL software-rendering notices. Anything
 * else is a test failure.
 */
export const CONSOLE_NOISE: RegExp[] = [
  // Software WebGL under headless Chromium is expected, not an app fault.
  /SwiftShader/i,
  /software (webgl|rendering)/i,
  /Automatic fallback to software webgl/i,
  /GroupMarkerNotSet/i,
  /Failed to create GLES3 context/i,
  // The browser asks for /favicon.ico unprompted; the app ships no favicon, so
  // nginx answers 404. Chrome logs that as a console error. It is the browser's
  // request, not the application's.
  /favicon\.ico/i,
];

export type Collected = { errors: string[]; pageErrors: string[] };

export function collectConsole(page: Page, ignore: RegExp[] = CONSOLE_NOISE): Collected {
  const collected: Collected = { errors: [], pageErrors: [] };
  page.on('console', (msg: ConsoleMessage) => {
    if (msg.type() !== 'error') return;
    // Resource-load failures carry the URL in the location, not the text, so
    // record both — otherwise "404 (Not Found)" is undiagnosable.
    const url = msg.location()?.url ?? '';
    const text = url ? `${msg.text()} [${url}]` : msg.text();
    if (ignore.some((re) => re.test(text))) return;
    collected.errors.push(text);
  });
  page.on('pageerror', (err) => {
    const text = `${err.name}: ${err.message}`;
    if (ignore.some((re) => re.test(text))) return;
    collected.pageErrors.push(text);
  });
  return collected;
}

/** Read the anonymous session token the app minted for itself. */
export async function sessionFromPage(
  page: Page,
): Promise<{ profileId: string | null; token: string | null }> {
  return page.evaluate(() => ({
    profileId: localStorage.getItem('provenance.profileId'),
    token: localStorage.getItem('provenance.token'),
  }));
}

/** True when the document scrolls horizontally at the current viewport width. */
export async function hasHorizontalOverflow(page: Page): Promise<{
  overflow: boolean;
  scrollWidth: number;
  clientWidth: number;
  culprits: string[];
}> {
  return page.evaluate(() => {
    const doc = document.documentElement;
    const scrollWidth = Math.max(doc.scrollWidth, document.body.scrollWidth);
    const clientWidth = doc.clientWidth;
    const culprits: string[] = [];
    if (scrollWidth > clientWidth + 1) {
      for (const el of Array.from(document.querySelectorAll('*'))) {
        const rect = el.getBoundingClientRect();
        if (rect.width === 0) continue;
        if (rect.right > clientWidth + 1 || rect.left < -1) {
          // Only report elements that are not inside a deliberate scroll container.
          let node: HTMLElement | null = el as HTMLElement;
          let contained = false;
          while (node && node !== doc) {
            const overflowX = getComputedStyle(node).overflowX;
            if (overflowX === 'auto' || overflowX === 'scroll' || overflowX === 'hidden') {
              contained = true;
              break;
            }
            node = node.parentElement;
          }
          if (!contained) {
            culprits.push(
              `${el.tagName.toLowerCase()}.${(el.className || '').toString().slice(0, 80)} right=${Math.round(rect.right)}`,
            );
          }
        }
      }
    }
    return { overflow: scrollWidth > clientWidth + 1, scrollWidth, clientWidth, culprits: culprits.slice(0, 10) };
  });
}
