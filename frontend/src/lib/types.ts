export type RunStatus = "pending" | "running" | "completed" | "failed" | "canceled";

export interface RunSummary {
  id: string;
  project_id: string;
  video_id: string;
  name: string;
  status: RunStatus;
  research_mode: string;
  viewpoint: string;
  task_constraint: string;
  provider_id: string;
  model_id: string;
  modality: string;
  sampling_preset: string;
  analysis_pass: string;
  segmentation_mode: string;
  fixture: boolean;
  fixture_label: string;
  cost_usd: number;
  input_tokens: number;
  output_tokens: number;
  error: string;
  object_count: number;
  action_count: number;
  duration_sec: number | null;
  created_at: string;
  thumbnail_url: string;
  video_filename: string;
}

export interface VideoInfo {
  id: string;
  project_id: string;
  filename: string;
  checksum: string;
  duration_sec: number;
  width: number;
  height: number;
  fps: number;
  avg_fps: number;
  variable_frame_rate: boolean;
  codec: string;
  container: string;
  frame_count: number;
  thumbnail_url: string;
  playback_url: string;
  contact_sheet_url: string;
  frames?: FrameInfo[];
  capture_time_raw?: string;
  capture_time_utc?: string;
  timestamp_reliability?: string;
  timestamp_source?: string;
  file_mtime?: string;
  timestamp_note?: string;
  has_audio?: boolean;
  audio_probed?: boolean;
  audio_codec?: string;
}

export interface FrameInfo {
  id: string;
  timestamp: number;
  reason: string;
  url: string;
  motion_score?: number;
  scene_change?: boolean;
}

export interface BBox {
  x: number;
  y: number;
  w: number;
  h: number;
}

export interface LabObject {
  id: string;
  local_id: string;
  label: string;
  description: string;
  visual_characteristics: string;
  initial_state: string;
  final_state: string;
  uncertain_identity: boolean;
  manipulated: boolean;
  confidence: number | null;
  color: string;
  observation_count: number;
  annotation_status: string;
  primary_image_url: string;
  images: { id: string; kind: string; timestamp: number; url: string; score: number }[];
}

export interface Observation {
  id: string;
  object_local_id: string;
  timestamp: number;
  bbox: BBox | null;
  visibility: string;
  description: string;
  evidence_class: string;
  provenance: string;
  evidence_note: string;
  continuity: string;
  coordinate_frame: string;
  camera_compensated: boolean;
  frame_id: string | null;
}

export interface LabAction {
  id: string;
  local_id: string;
  action_type: string;
  description: string;
  start: number;
  end: number;
  actor: string;
  object_local_ids: string[];
  initial_state: string;
  final_state: string;
  outcome: string;
  uncertainty: number;
  evidence_class: string;
  provenance: string;
  evidence_note: string;
  evidence_timestamps: number[];
}

export interface Step {
  id: string;
  local_id: string;
  number: number;
  name: string;
  required_objects: string[];
  preconditions: string[];
  expected_action: string;
  completion_condition: string;
  observed_result: string;
  action_local_ids: string[];
  time_start: number | null;
  time_end: number | null;
  uncertainty: number;
  evidence_class: string;
  provenance: string;
  parallel_group: string;
}

export interface Dependency {
  id: string;
  from_step: string;
  to_step: string;
  category: string;
  rationale: string;
  evidence_class: string;
  provenance: string;
}

export interface Ambiguity {
  id: string;
  local_id: string;
  kind: string;
  description: string;
  time_start: number | null;
  time_end: number | null;
  object_local_ids: string[];
  action_local_ids: string[];
  alternative: string;
  evidence_class: string;
  provenance: string;
}

export interface StageInfo {
  stage: string;
  status: string;
  progress_current: number;
  progress_total: number;
  attempt: number;
  error: string;
  checkpoint: Record<string, unknown>;
  started_at: string | null;
  finished_at: string | null;
}

export interface Bundle {
  run: RunSummary;
  video: VideoInfo | null;
  revision: { id: string; layer: string; version: number; note: string } | null;
  coverage: {
    modality?: string;
    frames_extracted?: number;
    frames_submitted?: number;
    temporal_coverage_note?: string;
    native_video_submitted?: boolean;
    selection_reasons?: Record<string, number>;
  };
  config: Record<string, unknown>;
  stages: StageInfo[];
  frames: FrameInfo[];
  objects: LabObject[];
  observations: Observation[];
  states: {
    id: string;
    object_local_id: string;
    time_start: number;
    time_end: number;
    state_label: string;
    description: string;
    evidence_class: string;
    provenance: string;
  }[];
  transitions: {
    id: string;
    object_local_id: string;
    timestamp: number;
    from_state: string;
    to_state: string;
    description: string;
    evidence_class: string;
    certainty: number | null;
    provenance: string;
  }[];
  relationships: {
    id: string;
    subject_local_id: string;
    other_local_id: string | null;
    kind: string;
    time_start: number;
    time_end: number;
    description: string;
    evidence_class: string;
    provenance: string;
  }[];
  actions: LabAction[];
  steps: Step[];
  dependencies: Dependency[];
  ambiguities: Ambiguity[];
  validation: { severity: string; code: string; message: string; entity_id?: string }[];
  summary: string;
  camera: {
    viewpoint?: string;
    task_constraint?: string;
    coordinate_frame?: string;
    world_coordinates_supported?: boolean;
    camera_motion_compensated?: boolean;
    compensation_model?: string;
    notes?: string;
  } | null;
  transcripts: TranscriptSegment[];
  alignments: AudioAlignment[];
}

export interface TranscriptSegment {
  id: string;
  video_id: string;
  local_id: string;
  start: number;
  end: number;
  text: string;
  text_model: string;
  language: string;
  language_probability: number | null;
  confidence: number | null;
  words: { start: number; end: number; word: string; probability?: number }[];
  model_name: string;
  device: string;
  corrected: boolean;
  provenance: string;
}

export interface AudioAlignment {
  id: string;
  video_id: string;
  segment_id: string;
  classification: string;
  local_classification: string;
  explanation: string;
  action_local_ids: string[];
  object_local_ids: string[];
  step_local_ids: string[];
  frame_timestamps: number[];
  time_offset_sec: number | null;
  confidence: number | null;
  evidence_class: string;
  provenance: string;
  corrected: boolean;
  speech_is_not_completion: boolean;
}

export interface AudioModelStatus {
  installed: boolean;
  detail: string;
  models: { model: string; downloaded: boolean; repo: string; default: boolean }[];
  acceleration: { device: string; compute_type: string; backend: string; note?: string };
  supported_models: string[];
  default_model: string;
  audio_bytes_leave_machine: boolean;
}

export interface ProviderInfo {
  id: string;
  configured: boolean;
  secret_source: string;
  hint: string;
  validated: boolean;
  last_error: string;
  base_url: string;
  models: Capability[];
}

export interface Capability {
  model_id: string;
  provider_id: string;
  text: boolean;
  image: boolean;
  native_video: boolean;
  structured_output: boolean;
  max_images: number;
  supports_temperature: boolean;
  reasoning_efforts: string[];
  price_known: boolean;
  notes: string;
}

export interface Dashboard {
  videos: number;
  runs_completed: number;
  runs_active: number;
  objects: number;
  actions: number;
  workflows: number;
  ambiguities: number;
  providers_connected: string[];
  input_tokens: number;
  output_tokens: number;
  cost_usd: number;
  recent_runs: RunSummary[];
}
