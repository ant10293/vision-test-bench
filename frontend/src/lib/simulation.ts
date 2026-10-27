export interface SimulationJob {
  id: string;
  status: string;
  stage: string;
  frame_current: number;
  frame_total: number;
  error: string;
  cancel_requested: boolean;
}

export interface SimulationSummary {
  id: string;
  name: string;
  preset: string;
  variant: string;
  camera_mode: string;
  research_mode: string;
  seed: number;
  quality: string;
  width: number;
  height: number;
  fps: number;
  duration: number;
  randomize: boolean;
  pair_key: string;
  status: string;
  error: string;
  video_id: string | null;
  analysis_run_id: string;
  blender_version: string;
  engine: string;
  frame_count: number;
  job: SimulationJob | null;
}

export interface SimulationPresets {
  presets: string[];
  variants: string[];
  camera_modes: string[];
  qualities: string[];
  blender_available: boolean;
  kinematic: boolean;
  rigid_body: boolean;
  note: string;
}

export interface SimPiece {
  name: string;
  kind: "box" | "cylinder";
  size: number[];
  local_position: number[];
}

export interface SimSceneObject {
  instance_id: string;
  label: string;
  omitted: boolean;
  material: { color: number[] };
  pieces: SimPiece[];
}

export interface SimBox {
  x: number;
  y: number;
  w: number;
  h: number;
}

export interface SimInstance {
  instance_id: string;
  visibility: string;
  visible_pixels: number;
  bbox: SimBox | null;
  present?: boolean;
}

export interface SimFrame {
  index: number;
  timestamp: number;
  instances: SimInstance[];
}

export interface SimCondition {
  constraint_id: string;
  relation: string;
  satisfied: boolean;
  observability: string;
  justified_verdict: string;
  subject_visibility: string;
  host_visibility: string;
  detail: string;
  required: boolean;
}

export interface SimulationTruth {
  label: string;
  scene: {
    objects: SimSceneObject[];
    width: number;
    height: number;
    fps: number;
    camera: { fov_x_deg: number; mode: string };
  };
  frames: { frames: SimFrame[] };
  events: { events: { id: string; kind: string; start: number; end: number; outcome: string; object_id: string }[] };
  observability: { note: string; conditions: SimCondition[] };
}

export interface SimPose {
  instance_id: string;
  label: string;
  present: boolean;
  matrix: number[];
  position: number[];
}

export interface SimulationState {
  timestamp: number;
  kinematic: boolean;
  rigid_body: boolean;
  objects: SimPose[];
  relations: { constraint_id: string; relation: string; satisfied: boolean; unresolved: boolean; detail: string }[];
  active_operations: string[];
  active_step_id: string | null;
  camera: {
    mode: string;
    matrix: number[];
    position: number[];
    world_coordinates_are_simulator_truth: boolean;
    intrinsics: { fx: number; fy: number; width: number; height: number };
  };
}

export interface SimulationEvaluation {
  id: string;
  run_id: string;
  perception_preset: string;
  created_at: string;
  result: {
    is_a_detection_claim?: boolean;
    detection?: { evaluated?: boolean; status?: string; reason?: string; precision?: number; recall?: number; mean_iou?: number | null };
    identity?: { evaluated?: boolean; reason?: string; idf1?: number; identity_switches?: number; false_merges?: number };
    camera_pose?: { status?: string; evaluated?: boolean; reason?: string };
    masks?: { status?: string; evaluated?: boolean; reason?: string };
    outcomes?: { note?: string; unsupported_claims?: { constraint_id?: string; reason?: string; start?: number }[] };
    actions?: { errors?: { timestamp?: number; kind?: string }[] };
  };
}
