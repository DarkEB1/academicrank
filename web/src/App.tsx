import { Suspense, lazy } from 'react';
import { Navigate, Route, Routes } from 'react-router-dom';
import { AppShell } from './components/AppShell';
import { RankingsScreen } from './routes/Rankings';
import { TrustSetScreen } from './routes/TrustSet';
import { RecommendationsScreen } from './routes/Recommendations';
import { PaperScreen } from './routes/PaperView';
import { ParamsScreen } from './routes/Params';
import { SessionGate } from './routes/SessionGate';
import { LoadingRegion, Skeleton } from './components/ui/Skeleton';

// The WebGL renderer and its layout code are a large chunk; keep it off the
// critical path for people who never open the graph.
const GraphScreen = lazy(() =>
  import('./routes/GraphExplorer').then((m) => ({ default: m.GraphExplorerScreen })),
);

function ScreenFallback(): JSX.Element {
  return (
    <LoadingRegion label="Loading the graph renderer" className="space-y-4">
      <Skeleton className="h-8 w-64" />
      <Skeleton className="h-[60vh] w-full" />
    </LoadingRegion>
  );
}

export function App(): JSX.Element {
  return (
    <Routes>
      <Route element={<AppShell />}>
        <Route
          index
          element={
            <SessionGate>
              <RankingsScreen />
            </SessionGate>
          }
        />
        <Route
          path="trust"
          element={
            <SessionGate>
              <TrustSetScreen />
            </SessionGate>
          }
        />
        <Route
          path="recommendations"
          element={
            <SessionGate>
              <RecommendationsScreen />
            </SessionGate>
          }
        />
        <Route
          path="paper/:id"
          element={
            <SessionGate>
              <PaperScreen />
            </SessionGate>
          }
        />
        <Route
          path="graph"
          element={
            <SessionGate>
              <Suspense fallback={<ScreenFallback />}>
                <GraphScreen />
              </Suspense>
            </SessionGate>
          }
        />
        <Route
          path="params"
          element={
            <SessionGate>
              <ParamsScreen />
            </SessionGate>
          }
        />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
  );
}
