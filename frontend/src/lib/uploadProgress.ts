/** Each step fills the bar on its own, from 0 to 100. Analysis starts over after the upload. */

export function uploadPercent(ratio: number): number {
  const clamped = Math.max(0, Math.min(1, ratio));
  return Math.round(clamped * 100);
}

export function analysisPercent(serverProgress: number): number {
  const clamped = Math.max(0, Math.min(100, serverProgress));
  return Math.round(clamped);
}

export function videoReady(video: { ingest_status?: string }): boolean {
  return !video.ingest_status || video.ingest_status === "ready";
}
