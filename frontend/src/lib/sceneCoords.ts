/** Blender is Z-up. The viewer is Y-up. This conversion is for display only. */

export function toThree(position: readonly number[]): [number, number, number] {
  return [position[0], position[2], -position[1]];
}

/** Photograph on a centered viewer box. X runs left to right. Y runs bottom to top, which is Blender +Z. */
export function photoUV(x: number, y: number, width: number, height: number): [number, number] {
  return [(x + width / 2) / Math.max(width, 1e-6), (y + height / 2) / Math.max(height, 1e-6)];
}

export function verticalFov(width: number, height: number, fovXDeg: number): number {
  const fx = width / 2 / Math.tan((fovXDeg * Math.PI) / 360);
  return (2 * Math.atan(height / 2 / fx) * 180) / Math.PI;
}
