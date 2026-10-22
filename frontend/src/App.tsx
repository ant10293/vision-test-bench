import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import { Shell } from "./components/Shell";
import { DashboardPage } from "./pages/Dashboard";
import { ExperimentPage } from "./pages/Experiment";
import { ComparePage, PromptsPage, ProvidersPage } from "./pages/Research";
import {
  EvaluatePage,
  InspectPage,
  LibraryRedirect,
  LogsPage,
  ObjectsPage,
  PipelinePage,
  RunLayout,
  RunsPage,
  StatesPage,
  StepsPage,
  WorkflowPage,
} from "./pages/RunPages";
import { VideoDetailPage, VideosPage } from "./pages/Videos";

const client = new QueryClient({
  defaultOptions: { queries: { retry: 1, refetchOnWindowFocus: false } },
});

export function App() {
  return (
    <QueryClientProvider client={client}>
      <BrowserRouter>
        <Routes>
          <Route element={<Shell />}>
            <Route index element={<DashboardPage />} />
            <Route path="videos" element={<VideosPage />} />
            <Route path="videos/:videoId" element={<VideoDetailPage />} />
            <Route path="experiments/new" element={<ExperimentPage />} />
            <Route path="runs" element={<RunsPage />} />
            <Route path="library" element={<LibraryRedirect />} />
            <Route path="providers" element={<ProvidersPage />} />
            <Route path="prompts" element={<PromptsPage />} />
            <Route path="compare" element={<ComparePage />} />
            <Route path="runs/:runId" element={<RunLayout />}>
              <Route index element={<InspectPage />} />
              <Route path="objects" element={<ObjectsPage />} />
              <Route path="states" element={<StatesPage />} />
              <Route path="workflow" element={<WorkflowPage />} />
              <Route path="steps" element={<StepsPage />} />
              <Route path="pipeline" element={<PipelinePage />} />
              <Route path="evaluate" element={<EvaluatePage />} />
              <Route path="logs" element={<LogsPage />} />
            </Route>
            <Route path="*" element={<Navigate to="/" replace />} />
          </Route>
        </Routes>
      </BrowserRouter>
    </QueryClientProvider>
  );
}
