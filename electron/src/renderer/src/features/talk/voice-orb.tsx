import { useEffect, useRef } from 'react';
import {
  IcosahedronGeometry,
  Mesh,
  PerspectiveCamera,
  Scene,
  ShaderMaterial,
  Vector3,
  WebGLRenderer,
} from 'three';
import type { AgentMode } from './agent-client';

/** Colours per state: amber/rose at rest and while speaking, sky/violet while listening. */
const PALETTE: Record<AgentMode, [number[], number[]]> = {
  idle: [
    [1.0, 0.7, 0.37],
    [1.0, 0.44, 0.56],
  ],
  connecting: [
    [0.8, 0.6, 1.0],
    [1.0, 0.44, 0.56],
  ],
  listening: [
    [0.5, 0.83, 1.0],
    [0.62, 0.55, 1.0],
  ],
  thinking: [
    [0.8, 0.6, 1.0],
    [1.0, 0.44, 0.56],
  ],
  speaking: [
    [1.0, 0.7, 0.37],
    [1.0, 0.44, 0.56],
  ],
};

// Ashima/Stefan Gustavson 3D simplex noise (MIT).
const NOISE = `
vec3 mod289(vec3 x){return x-floor(x*(1./289.))*289.;}vec4 mod289(vec4 x){return x-floor(x*(1./289.))*289.;}
vec4 permute(vec4 x){return mod289(((x*34.)+1.)*x);}vec4 taylorInvSqrt(vec4 r){return 1.79284291400159-.85373472095314*r;}
float snoise(vec3 v){const vec2 C=vec2(1./6.,1./3.);const vec4 D=vec4(0.,.5,1.,2.);vec3 i=floor(v+dot(v,C.yyy));vec3 x0=v-i+dot(i,C.xxx);
vec3 g=step(x0.yzx,x0.xyz);vec3 l=1.-g;vec3 i1=min(g.xyz,l.zxy);vec3 i2=max(g.xyz,l.zxy);vec3 x1=x0-i1+C.xxx;vec3 x2=x0-i2+C.yyy;vec3 x3=x0-D.yyy;
i=mod289(i);vec4 p=permute(permute(permute(i.z+vec4(0.,i1.z,i2.z,1.))+i.y+vec4(0.,i1.y,i2.y,1.))+i.x+vec4(0.,i1.x,i2.x,1.));
float n_=.142857142857;vec3 ns=n_*D.wyz-D.xzx;vec4 j=p-49.*floor(p*ns.z*ns.z);vec4 x_=floor(j*ns.z);vec4 y_=floor(j-7.*x_);
vec4 x=x_*ns.x+ns.yyyy;vec4 y=y_*ns.x+ns.yyyy;vec4 h=1.-abs(x)-abs(y);vec4 b0=vec4(x.xy,y.xy);vec4 b1=vec4(x.zw,y.zw);
vec4 s0=floor(b0)*2.+1.;vec4 s1=floor(b1)*2.+1.;vec4 sh=-step(h,vec4(0.));vec4 a0=b0.xzyw+s0.xzyw*sh.xxyy;vec4 a1=b1.xzyw+s1.xzyw*sh.zzww;
vec3 p0=vec3(a0.xy,h.x);vec3 p1=vec3(a0.zw,h.y);vec3 p2=vec3(a1.xy,h.z);vec3 p3=vec3(a1.zw,h.w);
vec4 norm=taylorInvSqrt(vec4(dot(p0,p0),dot(p1,p1),dot(p2,p2),dot(p3,p3)));p0*=norm.x;p1*=norm.y;p2*=norm.z;p3*=norm.w;
vec4 m=max(.6-vec4(dot(x0,x0),dot(x1,x1),dot(x2,x2),dot(x3,x3)),0.);m=m*m;
return 42.*dot(m*m,vec4(dot(p0,x0),dot(p1,x1),dot(p2,x2),dot(p3,x3)));}`;

const VERTEX = `${NOISE}
uniform float uTime, uAmp, uSpin;
varying vec3 vN; varying float vD; varying vec3 vP;
void main() {
  vec3 p = position;
  float n = snoise(p * 1.4 + vec3(0., 0., uTime * uSpin));
  float n2 = snoise(p * 3.2 - uTime * .6) * .35;
  float d = (n + n2) * uAmp;
  p += normal * d;
  vD = d;
  vN = normalize(normalMatrix * normal);
  vP = (modelViewMatrix * vec4(p, 1.)).xyz;
  gl_Position = projectionMatrix * modelViewMatrix * vec4(p, 1.);
}`;

const FRAGMENT = `
uniform vec3 uA, uB;
varying vec3 vN; varying float vD; varying vec3 vP;
void main() {
  vec3 n = normalize(vN);
  vec3 v = normalize(-vP);
  float fr = pow(1. - max(dot(n, v), 0.), 2.4);
  vec3 L = normalize(vec3(-.5, .8, .7));
  float diff = max(dot(n, L), 0.);
  float spec = pow(max(dot(reflect(-L, n), v), 0.), 40.);
  vec3 base = mix(uA, uB, smoothstep(-.5, .6, -n.y + vD * 1.5));
  vec3 col = base * (.18 + .62 * diff) + spec * .55 + fr * mix(base, vec3(1.), .4) * .9;
  gl_FragColor = vec4(col, 1.);
}`;

const lerp = (a: number, b: number, t: number) => a + (b - a) * t;

/**
 * The agent's presence. Motion only carries meaning: still at rest, ripples
 * with your voice while listening, turns while thinking, pulses with its own
 * voice while speaking. `level` is read every frame (0..1).
 */
export function VoiceOrb({ mode, level }: { mode: AgentMode; level: () => number }) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const modeRef = useRef(mode);
  modeRef.current = mode;
  const levelRef = useRef(level);
  levelRef.current = level;

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    let renderer: WebGLRenderer;
    try {
      renderer = new WebGLRenderer({ canvas, antialias: true, alpha: true });
    } catch {
      canvas.dataset.fallback = 'true';
      return;
    }
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    const scene = new Scene();
    const camera = new PerspectiveCamera(38, 1, 0.1, 100);
    camera.position.z = 6.4;
    const [a, b] = PALETTE.idle;
    const uniforms = {
      uTime: { value: 0 },
      uAmp: { value: 0.06 },
      uSpin: { value: 0.05 },
      uA: { value: new Vector3(...a) },
      uB: { value: new Vector3(...b) },
    };
    const geometry = new IcosahedronGeometry(1.2, 64);
    const material = new ShaderMaterial({ uniforms, vertexShader: VERTEX, fragmentShader: FRAGMENT });
    const mesh = new Mesh(geometry, material);
    scene.add(mesh);

    const resize = () => {
      const { width, height } = canvas.getBoundingClientRect();
      if (!width || !height) return;
      renderer.setSize(width, height, false);
      camera.aspect = width / height;
      camera.updateProjectionMatrix();
    };
    const observer = new ResizeObserver(resize);
    observer.observe(canvas);
    resize();

    const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)');
    const targetA = new Vector3();
    const targetB = new Vector3();
    let smoothed = 0;
    let last = performance.now();
    let frame = 0;
    const tick = (now: number) => {
      const dt = Math.min((now - last) / 1000, 0.05);
      last = now;
      const current = modeRef.current;
      const raw = current === 'thinking' ? 0.18 : current === 'idle' ? 0.03 : levelRef.current();
      smoothed = lerp(smoothed, raw, 0.12);
      const [nextA, nextB] = PALETTE[current];
      uniforms.uA.value.lerp(targetA.set(nextA[0]!, nextA[1]!, nextA[2]!), 0.04);
      uniforms.uB.value.lerp(targetB.set(nextB[0]!, nextB[1]!, nextB[2]!), 0.04);
      if (!reduceMotion.matches) {
        uniforms.uTime.value += dt;
        uniforms.uAmp.value = lerp(uniforms.uAmp.value, 0.05 + smoothed * 0.26, 0.1);
        uniforms.uSpin.value = current === 'thinking' ? 1.4 : current === 'idle' ? 0.05 : 0.3;
        if (current === 'thinking' || current === 'connecting') mesh.rotation.y += dt * 0.9;
        mesh.scale.setScalar(lerp(mesh.scale.x, 1 + smoothed * 0.12, 0.15));
      }
      renderer.render(scene, camera);
      frame = requestAnimationFrame(tick);
    };
    frame = requestAnimationFrame(tick);

    return () => {
      cancelAnimationFrame(frame);
      observer.disconnect();
      geometry.dispose();
      material.dispose();
      renderer.dispose();
    };
  }, []);

  return <canvas ref={canvasRef} className="talk-orb" aria-hidden="true" />;
}
