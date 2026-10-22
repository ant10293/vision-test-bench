import { createContext, useContext } from "react";
import type { Bundle } from "./types";

export const BundleContext = createContext<Bundle | null>(null);

export function useBundle() {
  const bundle = useContext(BundleContext);
  if (!bundle) throw new Error("Analysis is not loaded");
  return bundle;
}
