import { expect, test } from '@playwright/test';
import { gotoRoute, shot } from '../helpers/app';

/**
 * The graph explorer must actually render a WebGL scene with nodes in it —
 * not an empty canvas, and not a silent 2D fallback.
 *
 * To read pixels back out of a WebGL canvas we force `preserveDrawingBuffer`
 * on every context the page creates. That is test instrumentation injected
 * before any application script runs; the application source is untouched.
 */
test.beforeEach(async ({ page }) => {
  await page.addInitScript(() => {
    const original = HTMLCanvasElement.prototype.getContext;
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    (HTMLCanvasElement.prototype as any).getContext = function (type: string, attrs?: object) {
      if (type === 'webgl' || type === 'webgl2' || type === 'experimental-webgl') {
        return original.call(this, type, { ...(attrs ?? {}), preserveDrawingBuffer: true });
      }
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      return (original as any).call(this, type, attrs);
    };
  });
});

test('the graph explorer renders a WebGL canvas containing nodes', async ({ page }) => {
  await gotoRoute(page, '/graph');

  const application = page.getByRole('application', { name: /Trust neighbourhood graph/ });
  await expect(application).toBeVisible();

  // The app publishes its own node/edge count in the canvas's accessible name.
  const label = (await application.getAttribute('aria-label')) ?? '';
  const match = /(\d+) nodes, (\d+) edges/.exec(label);
  expect(match, `unexpected canvas label: ${label}`).not.toBeNull();
  const nodeCount = Number(match![1]);
  const edgeCount = Number(match![2]);
  expect(nodeCount, 'the subgraph must contain nodes').toBeGreaterThan(10);
  expect(edgeCount, 'the subgraph must contain edges').toBeGreaterThan(0);

  // sigma.js mounts its canvas stack inside the application container.
  const canvases = application.locator('canvas');
  expect(await canvases.count(), 'sigma should mount canvases').toBeGreaterThan(0);

  // Wait for ForceAtlas2 to finish so we measure a settled scene.
  await expect(page.getByText('settling layout…')).toHaveCount(0, { timeout: 120_000 });

  const report = await application.evaluate((container) => {
    const out: {
      className: string;
      width: number;
      height: number;
      contextType: string | null;
      paintedPixels: number;
    }[] = [];
    for (const canvas of Array.from(container.querySelectorAll('canvas'))) {
      const el = canvas as HTMLCanvasElement;
      let contextType: string | null = null;
      // getContext returns the *existing* context when the type matches.
      if (el.getContext('webgl2')) contextType = 'webgl2';
      else if (el.getContext('webgl')) contextType = 'webgl';
      else if (el.getContext('2d')) contextType = '2d';

      let painted = 0;
      try {
        const off = document.createElement('canvas');
        off.width = Math.min(el.width, 900);
        off.height = Math.min(el.height, 900);
        const ctx = off.getContext('2d', { willReadFrequently: true });
        if (ctx && off.width > 0 && off.height > 0) {
          ctx.drawImage(el, 0, 0, el.width, el.height, 0, 0, off.width, off.height);
          const data = ctx.getImageData(0, 0, off.width, off.height).data;
          for (let i = 3; i < data.length; i += 4) if (data[i] > 8) painted++;
        }
      } catch {
        painted = -1;
      }
      out.push({
        className: el.className,
        width: el.width,
        height: el.height,
        contextType,
        paintedPixels: painted,
      });
    }
    return out;
  });

  // eslint-disable-next-line no-console
  console.log('[graph] canvas stack:', JSON.stringify(report));

  const webgl = report.filter((c) => c.contextType === 'webgl' || c.contextType === 'webgl2');
  expect(
    webgl.length,
    `no WebGL context was created. Canvas stack: ${JSON.stringify(report)}`,
  ).toBeGreaterThan(0);

  for (const canvas of webgl) {
    expect(canvas.width, 'the WebGL canvas must have real dimensions').toBeGreaterThan(100);
    expect(canvas.height, 'the WebGL canvas must have real dimensions').toBeGreaterThan(100);
  }

  const painted = report.reduce((sum, c) => sum + Math.max(0, c.paintedPixels), 0);
  expect(
    painted,
    `the graph canvases are blank — nothing was drawn. Stack: ${JSON.stringify(report)}`,
  ).toBeGreaterThan(500);

  // And the keyboard-accessible mirror of the same data is populated.
  await page.getByRole('group').filter({ hasText: 'Node list' }).locator('summary').click();
  const nodeButtons = page.locator('details ul li button');
  expect(await nodeButtons.count(), 'the node list should mirror the canvas').toBeGreaterThan(10);

  await shot(page, '12-graph-webgl-canvas.png');
});
