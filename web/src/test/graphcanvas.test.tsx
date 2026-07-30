import { describe, expect, it } from 'vitest';
import { render, screen } from '@testing-library/react';
import { GraphCanvas } from '@/components/GraphCanvas';

/**
 * jsdom has no WebGL, which makes it a faithful stand-in for the browsers that
 * refuse a context (Firefox with hardware acceleration off / fingerprinting
 * resistance). Before the guard, sigma's constructor threw
 * `TypeError: ... "blendFunc" ... null` out of the effect and the route died in
 * the error boundary. The contract under test: the component renders its
 * fallback message instead of throwing.
 */
describe('graph canvas without WebGL', () => {
  it('degrades to a message instead of crashing the route', () => {
    render(
      <GraphCanvas
        nodes={[
          { id: 'W1', label: 'Paper one', kind: 'paper', trust: 0.5, year: 2001 },
          { id: 'W2', label: 'Paper two', kind: 'paper', trust: 0.2, year: 2002 },
        ]}
        edges={[{ source: 'W1', target: 'W2', relation: 'cites', weight: 1 }]}
        focusId="W1"
        dark={false}
        onSelect={() => undefined}
        className="relative h-64"
      />,
    );
    expect(screen.getByText(/needs WebGL/)).toBeInTheDocument();
    expect(screen.getByText(/list beneath this panel/)).toBeInTheDocument();
  });
});
