import { forwardRef } from 'react';
import type { InputHTMLAttributes } from 'react';
import { cn } from '@/lib/cn';

export type SliderProps = Omit<InputHTMLAttributes<HTMLInputElement>, 'type'> & {
  /** Rendered to assistive tech in place of the raw number. */
  valueText?: string;
};

/**
 * A native range input, styled. Native gives us arrow keys, Home/End, PageUp
 * and touch handling without reimplementing any of it.
 */
export const Slider = forwardRef<HTMLInputElement, SliderProps>(function Slider(
  { className, valueText, ...props },
  ref,
) {
  return (
    <input
      ref={ref}
      type="range"
      aria-valuetext={valueText}
      className={cn(
        'h-6 w-full cursor-pointer appearance-none bg-transparent',
        // track
        '[&::-webkit-slider-runnable-track]:h-[3px] [&::-webkit-slider-runnable-track]:rounded-full [&::-webkit-slider-runnable-track]:bg-rule-strong',
        '[&::-moz-range-track]:h-[3px] [&::-moz-range-track]:rounded-full [&::-moz-range-track]:bg-rule-strong',
        // thumb
        '[&::-webkit-slider-thumb]:mt-[-6.5px] [&::-webkit-slider-thumb]:h-4 [&::-webkit-slider-thumb]:w-4 [&::-webkit-slider-thumb]:appearance-none [&::-webkit-slider-thumb]:rounded-full [&::-webkit-slider-thumb]:border [&::-webkit-slider-thumb]:border-canvas [&::-webkit-slider-thumb]:bg-accent',
        '[&::-moz-range-thumb]:h-4 [&::-moz-range-thumb]:w-4 [&::-moz-range-thumb]:rounded-full [&::-moz-range-thumb]:border [&::-moz-range-thumb]:border-canvas [&::-moz-range-thumb]:bg-accent',
        'disabled:cursor-not-allowed disabled:opacity-50',
        className,
      )}
      {...props}
    />
  );
});
