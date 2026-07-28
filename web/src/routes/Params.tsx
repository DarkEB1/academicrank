import { useEffect, useMemo, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { AlertTriangle, RotateCcw } from 'lucide-react';
import { useRankings, useSeedCount, useSetParams } from '@/lib/queries';
import { useSession } from '@/lib/session';
import { useDebounced } from '@/lib/hooks';
import { ApiError } from '@/lib/api';
import type { Params, ScoredPaper } from '@/lib/types';
import { domainFor, formatMillis, formatSignedRank } from '@/lib/format';
import { PaperTitle } from '@/components/Math';
import { ScoreBar } from '@/components/ScoreBar';
import { CoverageNote, Disclaimer, Notice } from '@/components/Honesty';
import { ErrorState, NoTrustSetYet } from '@/components/States';
import { Card, CardBody, CardHeader } from '@/components/ui/Card';
import { Slider } from '@/components/ui/Slider';
import { Button } from '@/components/ui/Button';
import { Badge } from '@/components/ui/Badge';
import { LoadingRegion, Skeleton } from '@/components/ui/Skeleton';
import { groupTies } from '@/lib/ties';
import { cn } from '@/lib/cn';

/**
 * Slider ranges are a presentation choice: the contract does not state bounds.
 * See FRONTEND_NOTES.md.
 */
const SCALAR_CONTROLS: Record<
  string,
  { min: number; max: number; step: number; label: string; gloss: string }
> = {
  alpha: {
    min: 0,
    max: 1,
    step: 0.01,
    label: 'alpha',
    gloss:
      'Random-walk restart probability. Higher values keep the walk close to your seeds; lower values let it wander into the wider corpus.',
  },
  epoch_half_life_years: {
    min: 0.5,
    max: 50,
    step: 0.5,
    label: 'epoch half-life (years)',
    gloss:
      'How fast an edge decays with age. A short half-life favours recent work and will bury anything foundational.',
  },
  num_walks: {
    min: 100,
    max: 10000,
    step: 100,
    label: 'walks',
    gloss:
      'Sample count. More walks narrow the confidence intervals and cost time; they do not make the estimate more correct, only more precise.',
  },
};

const INSTITUTION_WARNING =
  'Institutional edges push trust towards whoever is already at a well-connected institution. That is existing academic hierarchy re-entering through the side door and coming back out looking like an objective measurement. The weight is low by default for that reason, and it is exposed here so that the choice is yours and visible rather than ours and hidden.';

export function ParamsScreen(): JSX.Element {
  const { profile } = useSession();
  const profileId = profile?.id ?? '';

  const serverParams = profile?.params ?? {};
  const initialWeights = useMemo(
    () => ({ ...(serverParams.context_weights ?? {}) }),
    // Only re-derive when the identity of the params object changes.
    [serverParams.context_weights],
  );

  const [weights, setWeights] = useState<Record<string, number>>(initialWeights);
  const [scalars, setScalars] = useState<Record<string, number>>(() => {
    const out: Record<string, number> = {};
    for (const key of Object.keys(SCALAR_CONTROLS)) {
      const value = serverParams[key as keyof Params];
      if (typeof value === 'number') out[key] = value;
    }
    return out;
  });
  const [rejected, setRejected] = useState<Record<string, string>>({});

  const draft = useMemo(
    () => ({ weights, scalars }),
    [weights, scalars],
  );
  const settled = useDebounced(draft, 350);
  const setParams = useSetParams(profileId);
  const applied = useRef<string>('');

  useEffect(() => {
    if (!profileId) return;
    const payload: Params = {};
    if (Object.keys(settled.weights).length > 0) payload.context_weights = settled.weights;
    for (const [key, value] of Object.entries(settled.scalars)) {
      if (rejected[key]) continue;
      (payload as Record<string, unknown>)[key] = value;
    }
    if (Object.keys(payload).length === 0) return;

    const signature = JSON.stringify(payload);
    if (signature === applied.current) return;
    applied.current = signature;

    setParams.mutate(payload, {
      onError: (error) => {
        if (error instanceof ApiError && error.status === 422) {
          // The engine does not honour something in this payload. Mark every
          // scalar in the request as suspect rather than guessing which.
          const message = error.message;
          setRejected((prev) => {
            const next = { ...prev };
            for (const key of Object.keys(settled.scalars)) next[key] = message;
            return next;
          });
        }
      },
    });
    // `setParams` is a stable mutation object from react-query.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [settled, profileId]);

  const rankings = useRankings(
    profileId,
    { limit: 20, context: 'aggregate', exclude_trusted: true },
    Boolean(profileId),
  );

  // Baseline captured the first time a ranking arrives, so movement is measured
  // against where the user started rather than against the previous keystroke.
  const [baseline, setBaseline] = useState<ScoredPaper[] | null>(null);
  useEffect(() => {
    if (!baseline && rankings.data?.items && rankings.data.items.length > 0) {
      setBaseline(rankings.data.items);
    }
  }, [rankings.data, baseline]);

  const baselineRanks = useMemo(() => {
    const map = new Map<string, number>();
    (baseline ?? []).forEach((paper, index) => map.set(paper.id, index));
    return map;
  }, [baseline]);

  const items = rankings.data?.items ?? [];
  const domain = domainFor(items);
  const runs = groupTies(items);
  const seeds = useSeedCount(profileId).count;

  const revert = () => {
    setWeights(initialWeights);
    setScalars(() => {
      const out: Record<string, number> = {};
      for (const key of Object.keys(SCALAR_CONTROLS)) {
        const value = serverParams[key as keyof Params];
        if (typeof value === 'number') out[key] = value;
      }
      return out;
    });
  };

  const hasControls = Object.keys(weights).length > 0 || Object.keys(scalars).length > 0;
  const dirty =
    JSON.stringify(weights) !== JSON.stringify(initialWeights) ||
    Object.keys(scalars).some((key) => scalars[key] !== serverParams[key as keyof Params]);

  return (
    <div className="space-y-8">
      <header className="space-y-3">
        <h1 className="font-serif text-2xl tracking-tight text-ink">Parameter playground</h1>
        <p className="max-w-measure text-sm leading-relaxed text-ink-muted">
          Every ranking in this system is the output of a handful of numbers that somebody chose.
          Here they are. Move them and watch the top twenty rearrange. If the order collapses under
          a small change to a weight, then the order was never telling you much — and you should
          know that rather than take it on trust.
        </p>
        <p className="max-w-measure text-sm leading-relaxed text-ink-muted">
          Changes are written to your profile and affect every other screen. Only parameters the
          engine actually honours are accepted; the rest are rejected outright rather than silently
          ignored.
        </p>
      </header>

      <div className="grid gap-8 xl:grid-cols-[24rem_minmax(0,1fr)]">
        <div className="space-y-5">
          {!hasControls ? (
            <Notice title="The server reported no adjustable parameters" tone="neutral">
              <p>
                <code className="font-mono">GET /api/profiles/me</code> returned a params object
                with no context weights and no scalar parameters. Nothing is invented here, so
                there is nothing to show. This usually means the engine build in use exposes no
                tunable knobs.
              </p>
            </Notice>
          ) : null}

          {Object.keys(weights).length > 0 ? (
            <Card>
              <CardHeader
                title="Context weights"
                description="How much each relation family contributes relative to the citation backbone."
                actions={
                  dirty ? (
                    <Button size="sm" onClick={revert}>
                      <RotateCcw aria-hidden className="h-3.5 w-3.5" />
                      Revert
                    </Button>
                  ) : undefined
                }
              />
              <CardBody className="space-y-6">
                {Object.keys(weights)
                  .sort()
                  .map((context) => (
                    <div key={context}>
                      <div className="flex items-baseline justify-between gap-3">
                        <label
                          htmlFor={`w-${context}`}
                          className="text-xs font-medium text-ink"
                        >
                          {context}
                        </label>
                        <span className="font-mono text-2xs tnum text-ink-muted">
                          {weights[context].toFixed(2)}
                        </span>
                      </div>
                      <Slider
                        id={`w-${context}`}
                        min={0}
                        max={1}
                        step={0.01}
                        value={weights[context]}
                        valueText={`${context} weight ${weights[context].toFixed(2)}`}
                        onChange={(e) =>
                          setWeights((prev) => ({ ...prev, [context]: Number(e.target.value) }))
                        }
                        className="mt-1.5"
                      />
                      {context.includes('institution') ? (
                        <p className="mt-2 flex gap-2 rounded-sm border border-caution/40 bg-caution-wash/60 px-3 py-2 text-xs leading-relaxed text-ink">
                          <AlertTriangle
                            aria-hidden
                            className="mt-0.5 h-3.5 w-3.5 shrink-0 text-caution"
                          />
                          <span>{INSTITUTION_WARNING}</span>
                        </p>
                      ) : null}
                    </div>
                  ))}
              </CardBody>
            </Card>
          ) : null}

          {Object.keys(scalars).length > 0 ? (
            <Card>
              <CardHeader title="Walk parameters" />
              <CardBody className="space-y-6">
                {Object.entries(scalars).map(([key, value]) => {
                  const control = SCALAR_CONTROLS[key];
                  if (!control) return null;
                  const reject = rejected[key];
                  return (
                    <div key={key}>
                      <div className="flex items-baseline justify-between gap-3">
                        <label htmlFor={`s-${key}`} className="text-xs font-medium text-ink">
                          {control.label}
                        </label>
                        <span className="font-mono text-2xs tnum text-ink-muted">
                          {control.step < 1 ? value.toFixed(2) : value}
                        </span>
                      </div>
                      <Slider
                        id={`s-${key}`}
                        min={control.min}
                        max={control.max}
                        step={control.step}
                        value={value}
                        disabled={Boolean(reject)}
                        valueText={`${control.label} ${value}`}
                        onChange={(e) =>
                          setScalars((prev) => ({ ...prev, [key]: Number(e.target.value) }))
                        }
                        className="mt-1.5"
                      />
                      <p className="mt-1.5 max-w-measure text-xs leading-relaxed text-ink-muted">
                        {control.gloss}
                      </p>
                      {reject ? (
                        <p className="mt-1.5 text-xs leading-relaxed text-critical">
                          Rejected by the server (422): {reject} This parameter is not honoured by
                          the engine, so the control is disabled rather than left to look
                          functional.
                        </p>
                      ) : null}
                    </div>
                  );
                })}
              </CardBody>
            </Card>
          ) : null}

          <div className="flex items-center gap-3 text-xs text-ink-muted">
            {setParams.isPending ? <span>Saving parameters…</span> : null}
            {setParams.isSuccess && !setParams.isPending ? <span>Parameters saved.</span> : null}
            {setParams.isError && !(setParams.error instanceof ApiError && setParams.error.status === 422) ? (
              <ErrorState error={setParams.error} />
            ) : null}
          </div>

          {hasControls ? (
            <p className="max-w-measure text-xs leading-relaxed text-ink-faint">
              {INSTITUTION_WARNING}
            </p>
          ) : null}
        </div>

        {/* ---------------- live top 20 ---------------- */}
        <div className="space-y-4">
          <div className="flex flex-wrap items-baseline justify-between gap-3">
            <h2 className="text-sm font-semibold text-ink">Top twenty, live</h2>
            <p className="font-mono text-2xs tnum text-ink-faint">
              {rankings.isFetching ? 're-ranking…' : null}
              {rankings.data && !rankings.isFetching
                ? `computed in ${formatMillis(rankings.data.timing_ms)}`
                : null}
            </p>
          </div>

          {rankings.isError ? (
            <ErrorState error={rankings.error} onRetry={() => void rankings.refetch()} />
          ) : rankings.isLoading ? (
            <LoadingRegion label="Ranking" className="space-y-2">
              {Array.from({ length: 10 }, (_, i) => (
                <Skeleton key={i} className="h-12 w-full" />
              ))}
            </LoadingRegion>
          ) : seeds === 0 ? (
            <NoTrustSetYet what="This ranking" />
          ) : items.length === 0 ? (
            <p className="py-10 text-center text-sm text-ink-muted">
              No papers came back at these parameters.
            </p>
          ) : (
            <>
              <ol
                aria-busy={rankings.isFetching}
                className={cn(
                  'divide-y divide-rule rounded-sm border border-rule bg-surface transition-opacity',
                  rankings.isFetching && 'opacity-70',
                )}
              >
                {runs.map((run) =>
                  run.items.map((paper, indexInRun) => {
                    const currentIndex = run.startIndex + indexInRun;
                    const wasAt = baselineRanks.get(paper.id);
                    const delta = wasAt === undefined ? null : wasAt - currentIndex;
                    return (
                      <li
                        key={paper.id}
                        className={cn(
                          'grid grid-cols-[2.5rem_minmax(0,1fr)_4.5rem_9rem] items-center gap-3 px-3 py-2.5',
                          run.tied && 'border-l-2 border-l-accent/50',
                        )}
                      >
                        <span className="font-mono text-xs tnum text-ink-muted">
                          {run.tied ? `=${run.items[0].rank}` : paper.rank}
                        </span>
                        <Link to={`/paper/${paper.id}`} className="min-w-0">
                          <PaperTitle
                            as="span"
                            title={paper.title}
                            className="block truncate text-sm text-ink hover:text-accent"
                          />
                        </Link>
                        <span className="text-right">
                          {delta === null ? (
                            <Badge tone="accent" title="Not present in the baseline top twenty">
                              new
                            </Badge>
                          ) : delta === 0 ? (
                            <span className="font-mono text-2xs tnum text-ink-faint">—</span>
                          ) : (
                            <span
                              className={cn(
                                'font-mono text-2xs tnum',
                                delta > 0 ? 'text-positive' : 'text-critical',
                              )}
                            >
                              {formatSignedRank(delta)}
                            </span>
                          )}
                        </span>
                        <ScoreBar
                          value={paper.trust}
                          uncertainty={paper.uncertainty}
                          domain={domain}
                          showReadout={false}
                        />
                      </li>
                    );
                  }),
                )}
              </ol>

              <p className="max-w-measure text-xs leading-relaxed text-ink-muted">
                Movement is measured against the ranking as it stood when you opened this screen.
                Papers marked <em>new</em> were outside the baseline top twenty entirely.
              </p>

              <div className="space-y-3 border-t border-rule pt-4">
                <Disclaimer text={rankings.data?.disclaimer} />
                <CoverageNote />
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
