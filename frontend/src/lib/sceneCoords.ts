/** Blender is Z-up. The viewer is Y-up. This conversion is for display only. */

export function toThree(position: readonly number[]): [number, number, number] {
  return [position[0], position[2], -position[1]];
}

export function verticalFov(width: number, height: number, fovXDeg: number): number {
  const fx = width / 2 / Math.tan((fovXDeg * Math.PI) / 360);
  return (2 * Math.atan(height / 2 / fx) * 180) / Math.PI;
}
