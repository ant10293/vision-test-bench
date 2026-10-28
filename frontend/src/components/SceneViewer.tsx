import { OrbitControls } from "@react-three/drei";
import { Canvas, useThree } from "@react-three/fiber";
import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import * as THREE from "three";
import { photoUV, toThree } from "../lib/sceneCoords";
import type { SimPiece, SimPose, SimSceneObject } from "../lib/simulation";

const BASIS = new THREE.Matrix4().set(1, 0, 0, 0, 0, 0, 1, 0, 0, -1, 0, 0, 0, 0, 0, 1);

function pieceSize(piece: SimPiece): [number, number, number] {
  if (piece.kind === "cylinder") return [piece.size[0] * 2, piece.size[1], piece.size[0] * 2];
  return [piece.size[0], piece.size[2], piece.size[1]];
}

function usePhotoTexture(url: string | undefined): THREE.Texture | null {
  const [texture, setTexture] = useState<THREE.Texture | null>(null);
  useEffect(() => {
    if (!url) {
      setTexture(null);
      return;
    }
    let alive = true;
    let loaded: THREE.Texture | null = null;
    const loader = new THREE.TextureLoader();
    loader.load(
      url,
      (next) => {
        next.colorSpace = THREE.SRGBColorSpace;
        next.anisotropy = 8;
        if (!alive) {
          next.dispose();
          return;
        }
        loaded = next;
        setTexture(next);
      },
      undefined,
      () => {
        if (alive) setTexture(null);
      },
    );
    return () => {
      alive = false;
      loaded?.dispose();
      setTexture(null);
    };
  }, [url]);
  return texture;
}

function SelectedEdges({ width, height, depth }: { width: number; height: number; depth: number }) {
  const geometry = useMemo(() => {
    const box = new THREE.BoxGeometry(width, height, depth);
    const edges = new THREE.EdgesGeometry(box);
    box.dispose();
    return edges;
  }, [depth, height, width]);
  useEffect(() => () => geometry.dispose(), [geometry]);
  return (
    <lineSegments geometry={geometry}>
      <lineBasicMaterial color="#3ddc97" />
    </lineSegments>
  );
}

function PhotoBox({ width, height, depth }: { width: number; height: number; depth: number }) {
  const geometry = useMemo(() => {
    const next = new THREE.BoxGeometry(width, height, depth);
    const position = next.getAttribute("position");
    const uv = next.getAttribute("uv");
    for (let index = 0; index < position.count; index += 1) {
      const [u, v] = photoUV(position.getX(index), position.getY(index), width, height);
      uv.setXY(index, u, v);
    }
    return next;
  }, [depth, height, width]);
  useEffect(() => () => geometry.dispose(), [geometry]);
  return <primitive object={geometry} attach="geometry" />;
}

function InstanceMesh({
  object,
  pose,
  textureUrl,
  selected,
  onSelect,
}: {
  object: SimSceneObject;
  pose: SimPose;
  textureUrl?: string;
  selected: boolean;
  onSelect: (id: string) => void;
}) {
  const texture = usePhotoTexture(textureUrl);
  const group = useRef<THREE.Group>(null);
  const inverse = useMemo(() => BASIS.clone().invert(), []);
  useLayoutEffect(() => {
    const local = new THREE.Matrix4().set(
      pose.matrix[0], pose.matrix[1], pose.matrix[2], pose.matrix[3],
      pose.matrix[4], pose.matrix[5], pose.matrix[6], pose.matrix[7],
      pose.matrix[8], pose.matrix[9], pose.matrix[10], pose.matrix[11],
      pose.matrix[12], pose.matrix[13], pose.matrix[14], pose.matrix[15],
    );
    const world = BASIS.clone().multiply(local).multiply(inverse);
    if (!group.current) return;
    group.current.matrix.copy(world);
    group.current.matrixAutoUpdate = false;
    group.current.matrixWorldNeedsUpdate = true;
  }, [inverse, pose.matrix]);
  const color = `rgb(${object.material.color.map((channel) => Math.round(channel * 255)).join(",")})`;
  return (
    <group ref={group} onClick={(event) => { event.stopPropagation(); onSelect(object.instance_id); }}>
      {object.pieces.map((piece) => {
        const position = toThree(piece.local_position);
        const [x, y, z] = pieceSize(piece);
        const photographed = texture !== null && piece.kind === "box";
        return (
          <mesh key={piece.name} position={position}>
            {photographed ? (
              <PhotoBox width={x} height={y} depth={z} />
            ) : piece.kind === "cylinder" ? (
              <cylinderGeometry args={[piece.size[0], piece.size[0], piece.size[1], 20]} />
            ) : (
              <boxGeometry args={[x, y, z]} />
            )}
            {photographed ? (
              <meshBasicMaterial map={texture} transparent alphaTest={0.5} side={THREE.DoubleSide} toneMapped={false} />
            ) : (
              <meshStandardMaterial color={color} emissive={selected ? "#3ddc97" : "#000000"} emissiveIntensity={selected ? 0.35 : 0} />
            )}
            {photographed && selected && <SelectedEdges width={x} height={y} depth={z} />}
          </mesh>
        );
      })}
    </group>
  );
}

function RecordingCamera({ matrix, fov }: { matrix: number[]; fov: number }) {
  const camera = useThree((state) => state.camera);
  useLayoutEffect(() => {
    const local = new THREE.Matrix4().set(
      matrix[0], matrix[1], matrix[2], matrix[3],
      matrix[4], matrix[5], matrix[6], matrix[7],
      matrix[8], matrix[9], matrix[10], matrix[11],
      matrix[12], matrix[13], matrix[14], matrix[15],
    );
    // Both cameras look along local -Z. Convert the world basis only.
    camera.matrix.copy(BASIS.clone().multiply(local));
    camera.matrix.decompose(camera.position, camera.quaternion, camera.scale);
    camera.matrixAutoUpdate = true;
    if (camera instanceof THREE.PerspectiveCamera) {
      camera.fov = fov;
      camera.near = 0.01;
      camera.far = 20;
      camera.updateProjectionMatrix();
    }
  }, [camera, fov, matrix]);
  return null;
}

export function SceneViewer({
  objects,
  poses,
  cameraMatrix,
  fovY,
  textureUrls,
  selected,
  view,
  onSelect,
}: {
  objects: SimSceneObject[];
  poses: SimPose[];
  cameraMatrix: number[];
  fovY: number;
  textureUrls?: Record<string, string>;
  selected: string;
  view: "orbit" | "recording";
  onSelect: (id: string) => void;
}) {
  const byId = new Map(poses.map((pose) => [pose.instance_id, pose]));
  return (
    <Canvas camera={{ position: [0.45, 0.38, 0.72], fov: 42, near: 0.01, far: 20 }} style={{ height: 360, background: "#0e1117" }}>
      <color attach="background" args={["#0e1117"]} />
      <ambientLight intensity={0.7} />
      <directionalLight position={[1, 2, 1]} intensity={1.1} />
      <gridHelper args={[1, 10, "#2a3140", "#1c2430"]} />
      {objects.map((object) => {
        const pose = byId.get(object.instance_id);
        if (!pose?.present) return null;
        return (
          <InstanceMesh
            key={object.instance_id}
            object={object}
            pose={pose}
            textureUrl={textureUrls?.[object.instance_id]}
            selected={selected === object.instance_id}
            onSelect={onSelect}
          />
        );
      })}
      {view === "recording" ? <RecordingCamera matrix={cameraMatrix} fov={fovY} /> : <OrbitControls target={[0, 0.04, 0]} />}
    </Canvas>
  );
}
