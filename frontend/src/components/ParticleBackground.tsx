import { Canvas, useFrame, useThree } from "@react-three/fiber";
import { useEffect, useMemo, useRef, useState } from "react";
import * as THREE from "three";

/* Field and motion settings, in world units (the camera sits at z = CAMERA_Z looking at the origin). */
const FIELD = { x: 26, y: 16, z: 14 };
const CAMERA_Z = 18;
const COUNT_DESKTOP = 2000;
const COUNT_MOBILE = 600;
const MOBILE_BREAKPOINT = 768;
const RISE_SPEED = [0.12, 0.45]; // upward drift, units per second
const SIDE_SPEED = 0.08; // max sideways drift
const BOB_AMPLITUDE = 0.35;
const REPEL_RADIUS = 3.2;
const REPEL_STRENGTH = 1.8;
const EASE = 0.06; // lerp factor for the repel offset, so particles flow away and settle back
const PARALLAX = { x: 1.6, y: 1.0, ease: 0.03 };
const LINK_COUNT = 140; // particles checked for "molecule" links (O(n²), kept small)
const LINK_DISTANCE = 2.2;
const LINK_OPACITY = 0.12;
const PALETTE = ["#0EA5E9", "#38BDF8", "#14B8A6", "#F8FBFF"];

const vertexShader = /* glsl */ `
  attribute float aSize;
  attribute float aAlpha;
  attribute vec3 aColor;
  uniform float uPixelRatio;
  varying float vAlpha;
  varying vec3 vColor;
  void main() {
    vec4 mv = modelViewMatrix * vec4(position, 1.0);
    gl_Position = projectionMatrix * mv;
    // Size in CSS px, scaled by DPR and a mild perspective so near dots read larger.
    gl_PointSize = aSize * uPixelRatio * (${CAMERA_Z.toFixed(1)} / -mv.z) * 2.2;
    vAlpha = aAlpha;
    vColor = aColor;
  }
`;

// Soft circular sprite: bright core with a gaussian-like falloff, so each dot glows.
const fragmentShader = /* glsl */ `
  varying float vAlpha;
  varying vec3 vColor;
  void main() {
    float d = length(gl_PointCoord - 0.5) * 2.0;
    if (d > 1.0) discard;
    float glow = exp(-d * d * 3.5);
    gl_FragColor = vec4(vColor, glow * vAlpha);
  }
`;

interface Field {
  base: Float32Array; // drifting home positions
  offset: Float32Array; // eased repel offset per particle
  pos: Float32Array; // rendered positions (base + bob + offset)
  rise: Float32Array;
  side: Float32Array;
  phase: Float32Array; // random phase and speed per axis for the bobbing
  freq: Float32Array;
}

const rand = (min: number, max: number) => min + Math.random() * (max - min);

function makeField(count: number) {
  const f: Field = {
    base: new Float32Array(count * 3),
    offset: new Float32Array(count * 3),
    pos: new Float32Array(count * 3),
    rise: new Float32Array(count),
    side: new Float32Array(count),
    phase: new Float32Array(count * 3),
    freq: new Float32Array(count * 3),
  };
  const size = new Float32Array(count);
  const alpha = new Float32Array(count);
  const color = new Float32Array(count * 3);
  const c = new THREE.Color();
  for (let i = 0; i < count; i++) {
    f.base[i * 3] = rand(-FIELD.x, FIELD.x);
    f.base[i * 3 + 1] = rand(-FIELD.y, FIELD.y);
    f.base[i * 3 + 2] = rand(-FIELD.z, FIELD.z * 0.5);
    f.rise[i] = rand(RISE_SPEED[0]!, RISE_SPEED[1]!);
    f.side[i] = rand(-SIDE_SPEED, SIDE_SPEED);
    for (let a = 0; a < 3; a++) {
      f.phase[i * 3 + a] = rand(0, Math.PI * 2);
      f.freq[i * 3 + a] = rand(0.2, 0.7);
    }
    size[i] = rand(1, 3);
    alpha[i] = rand(0.3, 0.9);
    c.set(PALETTE[Math.floor(Math.random() * PALETTE.length)]!);
    color.set([c.r, c.g, c.b], i * 3);
  }
  f.pos.set(f.base);
  return { f, size, alpha, color };
}

function Particles({ count, animate }: { count: number; animate: boolean }) {
  const { camera, gl } = useThree();
  const { f, size, alpha, color } = useMemo(() => makeField(count), [count]);
  const pointsRef = useRef<THREE.Points>(null);
  const linksRef = useRef<THREE.LineSegments>(null);
  const mouse = useRef({ x: 0, y: 0, active: false });
  const linkPositions = useMemo(() => new Float32Array(LINK_COUNT * LINK_COUNT * 3), []);

  // The canvas has pointer-events: none, so the pointer is tracked on window.
  useEffect(() => {
    const move = (e: PointerEvent) => {
      mouse.current = {
        x: (e.clientX / window.innerWidth) * 2 - 1,
        y: -(e.clientY / window.innerHeight) * 2 + 1,
        active: true,
      };
    };
    const leave = () => (mouse.current.active = false);
    window.addEventListener("pointermove", move, { passive: true });
    document.addEventListener("pointerleave", leave);
    return () => {
      window.removeEventListener("pointermove", move);
      document.removeEventListener("pointerleave", leave);
    };
  }, []);

  const uniforms = useMemo(() => ({ uPixelRatio: { value: Math.min(gl.getPixelRatio(), 1.5) } }), [gl]);

  useFrame(({ clock }, delta) => {
    if (!animate) return;
    const dt = Math.min(delta, 0.05);
    const t = clock.elapsedTime;
    const cam = camera as THREE.PerspectiveCamera;

    // Mouse in world space on the z = 0 plane.
    const halfH = Math.tan(THREE.MathUtils.degToRad(cam.fov / 2)) * CAMERA_Z;
    const mx = mouse.current.x * halfH * cam.aspect;
    const my = mouse.current.y * halfH;

    for (let i = 0; i < count; i++) {
      const ix = i * 3;
      // Antigravity drift: up and slightly sideways, wrapping at the field edges.
      f.base[ix + 1]! += f.rise[i]! * dt;
      f.base[ix]! += f.side[i]! * dt;
      if (f.base[ix + 1]! > FIELD.y) f.base[ix + 1] = -FIELD.y;
      if (f.base[ix]! > FIELD.x) f.base[ix] = -FIELD.x;
      else if (f.base[ix]! < -FIELD.x) f.base[ix] = FIELD.x;

      // Weightless bob: an independent sine per axis.
      const bx = Math.sin(t * f.freq[ix]! + f.phase[ix]!) * BOB_AMPLITUDE;
      const by = Math.sin(t * f.freq[ix + 1]! + f.phase[ix + 1]!) * BOB_AMPLITUDE;
      const bz = Math.sin(t * f.freq[ix + 2]! + f.phase[ix + 2]!) * BOB_AMPLITUDE;

      // Repel: push away from the cursor inside REPEL_RADIUS, then ease the offset toward that target.
      let tx = 0;
      let ty = 0;
      if (mouse.current.active) {
        const dx = f.base[ix]! + bx - mx;
        const dy = f.base[ix + 1]! + by - my;
        const d = Math.hypot(dx, dy);
        if (d < REPEL_RADIUS && d > 0.0001) {
          const push = (1 - d / REPEL_RADIUS) * REPEL_STRENGTH;
          tx = (dx / d) * push;
          ty = (dy / d) * push;
        }
      }
      f.offset[ix]! += (tx - f.offset[ix]!) * EASE;
      f.offset[ix + 1]! += (ty - f.offset[ix + 1]!) * EASE;

      f.pos[ix] = f.base[ix]! + bx + f.offset[ix]!;
      f.pos[ix + 1] = f.base[ix + 1]! + by + f.offset[ix + 1]!;
      f.pos[ix + 2] = f.base[ix + 2]! + bz;
    }
    pointsRef.current!.geometry.attributes.position!.needsUpdate = true;

    // Faint links between nearby particles in a small subset (a molecular / neural mesh).
    let n = 0;
    const max2 = LINK_DISTANCE * LINK_DISTANCE;
    for (let a = 0; a < LINK_COUNT; a++) {
      for (let b = a + 1; b < LINK_COUNT; b++) {
        const dx = f.pos[a * 3]! - f.pos[b * 3]!;
        const dy = f.pos[a * 3 + 1]! - f.pos[b * 3 + 1]!;
        const dz = f.pos[a * 3 + 2]! - f.pos[b * 3 + 2]!;
        if (dx * dx + dy * dy + dz * dz < max2) {
          linkPositions.set([f.pos[a * 3]!, f.pos[a * 3 + 1]!, f.pos[a * 3 + 2]!], n * 3);
          linkPositions.set([f.pos[b * 3]!, f.pos[b * 3 + 1]!, f.pos[b * 3 + 2]!], n * 3 + 3);
          n += 2;
        }
      }
    }
    const lg = linksRef.current!.geometry;
    lg.setDrawRange(0, n);
    lg.attributes.position!.needsUpdate = true;

    // Camera parallax: tilt gently toward the pointer.
    cam.position.x += (mouse.current.x * PARALLAX.x - cam.position.x) * PARALLAX.ease;
    cam.position.y += (mouse.current.y * PARALLAX.y - cam.position.y) * PARALLAX.ease;
    cam.lookAt(0, 0, 0);
  });

  return (
    <>
      <points ref={pointsRef} frustumCulled={false}>
        <bufferGeometry>
          <bufferAttribute attach="attributes-position" args={[f.pos, 3]} usage={THREE.DynamicDrawUsage} />
          <bufferAttribute attach="attributes-aSize" args={[size, 1]} />
          <bufferAttribute attach="attributes-aAlpha" args={[alpha, 1]} />
          <bufferAttribute attach="attributes-aColor" args={[color, 3]} />
        </bufferGeometry>
        {/* Normal blending, not additive: additive washes out to white on this light background. */}
        <shaderMaterial
          vertexShader={vertexShader}
          fragmentShader={fragmentShader}
          uniforms={uniforms}
          transparent
          depthWrite={false}
        />
      </points>
      <lineSegments ref={linksRef} frustumCulled={false}>
        <bufferGeometry>
          <bufferAttribute
            attach="attributes-position"
            args={[linkPositions, 3]}
            usage={THREE.DynamicDrawUsage}
          />
        </bufferGeometry>
        <lineBasicMaterial color="#0EA5E9" transparent opacity={LINK_OPACITY} depthWrite={false} />
      </lineSegments>
    </>
  );
}

function usePrefersReducedMotion() {
  const query = "(prefers-reduced-motion: reduce)";
  const [reduced, setReduced] = useState(() => window.matchMedia?.(query).matches ?? false);
  useEffect(() => {
    const mq = window.matchMedia?.(query);
    if (!mq) return;
    const onChange = () => setReduced(mq.matches);
    mq.addEventListener?.("change", onChange);
    return () => mq.removeEventListener?.("change", onChange);
  }, []);
  return reduced;
}

const hasWebGL = () => typeof window !== "undefined" && "WebGLRenderingContext" in window;

/** Fixed, full-screen decorative layer: blurred colour orbs plus a floating 3D particle field. */
export default function ParticleBackground() {
  const reduced = usePrefersReducedMotion();
  const count = window.innerWidth < MOBILE_BREAKPOINT ? COUNT_MOBILE : COUNT_DESKTOP;

  return (
    <div aria-hidden="true" className="pointer-events-none fixed inset-0 -z-10 overflow-hidden">
      <div className="absolute -left-[10vw] -top-[15vh] h-[60vh] w-[60vh] rounded-full bg-sky-300/15 blur-3xl motion-safe:animate-orb" />
      <div className="absolute -right-[12vw] top-[25vh] h-[70vh] w-[70vh] rounded-full bg-teal-300/15 blur-3xl motion-safe:animate-orb-slow" />
      <div className="absolute bottom-[-20vh] left-[30vw] h-[55vh] w-[55vh] rounded-full bg-sky-200/15 blur-3xl motion-safe:animate-orb" />
      {hasWebGL() && (
        <Canvas
          style={{ pointerEvents: "none" }}
          dpr={[1, 1.5]}
          frameloop={reduced ? "demand" : "always"}
          camera={{ position: [0, 0, CAMERA_Z], fov: 55, near: 0.1, far: 100 }}
          gl={{ antialias: false, alpha: true, powerPreference: "low-power" }}
        >
          <Particles count={count} animate={!reduced} />
        </Canvas>
      )}
    </div>
  );
}
