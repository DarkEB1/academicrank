import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useState } from 'react';
import {
  clampDiversity,
  describeDiversity,
  diversityBand,
  diversityValueText,
  DIVERSITY_STEP,
  noveltyLabel,
  quantizeDiversity,
  tradeoffSplit,
} from '@/lib/diversity';
import { Slider } from '@/components/ui/Slider';

describe('the diversity dial value', () => {
  it('clamps to 0..1', () => {
    expect(clampDiversity(-2)).toBe(0);
    expect(clampDiversity(4)).toBe(1);
    expect(clampDiversity(0.42)).toBe(0.42);
  });

  it('falls back to the middle for a non-numeric value', () => {
    expect(clampDiversity(Number.NaN)).toBe(0.5);
  });

  it('snaps to the slider step and kills float dust', () => {
    // 0.1 + 0.2 style artefacts must never reach the query string.
    expect(quantizeDiversity(0.30000000000000004)).toBe(0.3);
    expect(quantizeDiversity(0.37)).toBe(0.35);
    expect(quantizeDiversity(0.38)).toBe(0.4);
    expect(String(quantizeDiversity(0.7))).toBe('0.7');
  });

  it('quantizes to a multiple of the step', () => {
    for (let raw = 0; raw <= 1.0001; raw += 0.013) {
      const q = quantizeDiversity(raw);
      expect(Math.abs(Math.round(q / DIVERSITY_STEP) * DIVERSITY_STEP - q)).toBeLessThan(1e-9);
    }
  });
});

describe('bands across the dial', () => {
  it('maps the extremes to exploitation and exploration', () => {
    expect(diversityBand(0)).toBe('exploit');
    expect(diversityBand(1)).toBe('explore');
  });

  it('is monotonic from left to right', () => {
    const order = ['exploit', 'lean-exploit', 'balanced', 'lean-explore', 'explore'];
    let previous = -1;
    for (let v = 0; v <= 1.0001; v += 0.05) {
      const index = order.indexOf(diversityBand(v));
      expect(index).toBeGreaterThanOrEqual(previous);
      previous = index;
    }
  });
});

describe('the trade-off is always stated', () => {
  it('gives both a gain and a cost at every setting', () => {
    for (let v = 0; v <= 1.0001; v += 0.05) {
      const description = describeDiversity(v);
      expect(description.gain.length).toBeGreaterThan(20);
      expect(description.cost.length).toBeGreaterThan(20);
    }
  });

  it('describes exploitation as reinforcing existing belief', () => {
    expect(describeDiversity(0).cost.toLowerCase()).toContain('already believe');
  });

  it('describes exploration as abandoning relevance', () => {
    expect(describeDiversity(1).cost.toLowerCase()).toContain('relevance');
  });

  it('splits complementary percentages', () => {
    expect(tradeoffSplit(0.3)).toEqual({ exploitation: 70, exploration: 30 });
    expect(tradeoffSplit(0)).toEqual({ exploitation: 100, exploration: 0 });
    const split = tradeoffSplit(0.67);
    expect(split.exploitation + split.exploration).toBe(100);
  });

  it('produces accessible value text naming both ends', () => {
    const text = diversityValueText(0.25);
    expect(text).toContain('0.25');
    expect(text).toContain('75% exploitation');
    expect(text).toContain('25% exploration');
  });
});

describe('novelty is described in words, not a bare number', () => {
  it('separates near from far', () => {
    expect(noveltyLabel(0.1)).toBe('adjacent to your trust set');
    expect(noveltyLabel(0.6)).toBe('distant from your trust set');
    expect(noveltyLabel(0.95)).toBe('far outside your trust set');
    expect(noveltyLabel(Number.NaN)).toBe('unknown distance');
  });
});

/* ------------------------------------------------------------------ */
/* The control itself                                                  */
/* ------------------------------------------------------------------ */

function Dial({ onChange }: { onChange: (value: number) => void }): JSX.Element {
  const [value, setValue] = useState(0.5);
  return (
    <>
      <Slider
        min={0}
        max={1}
        step={DIVERSITY_STEP}
        value={value}
        aria-label="Diversity: exploitation to exploration"
        valueText={diversityValueText(value)}
        onChange={(e) => {
          const next = quantizeDiversity(Number(e.target.value));
          setValue(next);
          onChange(next);
        }}
      />
      <p data-testid="cost">{describeDiversity(value).cost}</p>
    </>
  );
}

describe('the dial as a control', () => {
  it('exposes its meaning to assistive technology, not just a number', () => {
    render(<Dial onChange={() => {}} />);
    const slider = screen.getByRole('slider', { name: /exploitation to exploration/i });
    expect(slider).toHaveAttribute('aria-valuetext', expect.stringContaining('exploitation'));
  });

  it('is reachable by keyboard alone', async () => {
    const user = userEvent.setup();
    render(<Dial onChange={() => {}} />);
    const slider = screen.getByRole('slider', { name: /exploitation to exploration/i });

    await user.tab();

    expect(slider).toHaveFocus();
  });

  // jsdom does not implement arrow-key stepping on <input type="range">, so the
  // control is exercised through the change event the browser would emit.
  it('quantizes whatever value the control reports', () => {
    const onChange = vi.fn();
    render(<Dial onChange={onChange} />);
    const slider = screen.getByRole('slider', { name: /exploitation to exploration/i });

    fireEvent.change(slider, { target: { value: '0.37' } });

    expect(onChange).toHaveBeenCalledWith(0.35);
  });

  it('restates the cost when the setting moves', () => {
    render(<Dial onChange={() => {}} />);
    const slider = screen.getByRole('slider', { name: /exploitation to exploration/i });
    const before = screen.getByTestId('cost').textContent;

    fireEvent.change(slider, { target: { value: '0.95' } });

    const after = screen.getByTestId('cost').textContent;
    expect(after).not.toBe(before);
    expect(after?.toLowerCase()).toContain('relevance');
  });
});
