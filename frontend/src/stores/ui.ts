import { create } from "zustand";

interface InspectorState {
  time: number;
  playing: boolean;
  speed: number;
  overlay: boolean;
  layers: {
    tracks: boolean;
    trajectory: boolean;
    uncertain: boolean;
    confidence: boolean;
    conflicts: boolean;
  };
  seedMode: boolean;
  selectedObject: string | null;
  selectedAction: string | null;
  selectedStep: string | null;
  loop: { start: number; end: number } | null;
  setTime: (time: number) => void;
  setPlaying: (playing: boolean) => void;
  setSpeed: (speed: number) => void;
  setOverlay: (overlay: boolean) => void;
  setLayer: (key: keyof InspectorState["layers"], value: boolean) => void;
  setSeedMode: (seedMode: boolean) => void;
  selectObject: (id: string | null) => void;
  selectAction: (id: string | null) => void;
  selectStep: (id: string | null) => void;
  setLoop: (loop: { start: number; end: number } | null) => void;
}

export const useInspector = create<InspectorState>((set) => ({
  time: 0,
  playing: false,
  speed: 1,
  overlay: true,
  layers: {
    tracks: false,
    trajectory: false,
    uncertain: false,
    confidence: true,
    conflicts: false,
  },
  seedMode: false,
  selectedObject: null,
  selectedAction: null,
  selectedStep: null,
  loop: null,
  setTime: (time) => set({ time }),
  setPlaying: (playing) => set({ playing }),
  setSpeed: (speed) => set({ speed }),
  setOverlay: (overlay) => set({ overlay }),
  setLayer: (key, value) => set((state) => ({ layers: { ...state.layers, [key]: value } })),
  setSeedMode: (seedMode) => set({ seedMode }),
  selectObject: (selectedObject) => set({ selectedObject }),
  selectAction: (selectedAction) => set({ selectedAction }),
  selectStep: (selectedStep) => set({ selectedStep }),
  setLoop: (loop) => set({ loop }),
}));
