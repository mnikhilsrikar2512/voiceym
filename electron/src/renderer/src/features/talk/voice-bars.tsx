import { useEffect, useRef } from 'react';
import type { AgentMode } from './agent-client';

/** Rest lengths of the seven bars: together they draw the V of the Voiceym mark. */
const REST = [0.3, 0.42, 0.33, 0.4, 0.33, 0.42, 0.3];
/** Vertical centre of each bar, as a share of the stage height (the V's slope). */
const CENTRE = [0.15, 0.36, 0.575, 0.8, 0.575, 0.36, 0.15];
/** The mark's gradient, teal through indigo to violet, one stop per bar. */
const COLOURS = ['#5EEAD4', '#67C9E2', '#71ABEF', '#818CF8', '#A486F9', '#C680FA', '#E879F9'];

const lerp = (a: number, b: number, t: number) => a + (b - a) * t;

/**
 * The agent's presence: the Voiceym mark's seven sound bars. Motion only
 * carries meaning: a slow breath at rest, bars that follow your voice while
 * listening, a wave running across while thinking, and bars that follow its
 * own voice while speaking. `level` is read every frame (0..1).
 */
export function VoiceBars({ mode, level }: { mode: AgentMode; level: () => number }) {
  const barsRef = useRef<(HTMLSpanElement | null)[]>([]);
  const modeRef = useRef(mode);
  modeRef.current = mode;
  const levelRef = useRef(level);
  levelRef.current = level;

  useEffect(() => {
    const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)');
    let smoothed = 0;
    let frame = 0;
    const tick = (now: number) => {
      const t = now / 1000;
      const current = modeRef.current;
      const live = current === 'listening' || current === 'speaking';
      smoothed = lerp(smoothed, live ? levelRef.current() : 0, 0.18);
      barsRef.current.forEach((bar, i) => {
        if (!bar) return;
        let scale = 1;
        if (!reduceMotion.matches) {
          // Bars only ever grow from their rest length, so the V keeps its
          // shape: shrinking below rest read as the mark contracting.
          if (current === 'thinking' || current === 'connecting') {
            scale = 1 + 0.18 * Math.max(0, Math.sin(t * 5 - i * 0.7));
          } else if (live) {
            // Neighbouring bars move a little out of step, like a waveform.
            const jitter = 0.55 + 0.45 * Math.sin(t * 9 + i * 1.9);
            scale = Math.min(1.45, 1 + smoothed * 0.9 * jitter);
          } else {
            scale = 0.94 + 0.06 * Math.sin(t * 1.2 + i * 0.5);
          }
        }
        bar.style.transform = `translateY(-50%) scaleY(${scale.toFixed(3)})`;
      });
      frame = requestAnimationFrame(tick);
    };
    frame = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(frame);
  }, []);

  return (
    <div className="talk-bars" data-mode={mode} aria-hidden="true">
      {REST.map((length, i) => (
        <span
          key={i}
          ref={(el) => {
            barsRef.current[i] = el;
          }}
          className="talk-bar"
          style={{
            height: `${length * 100}%`,
            top: `${CENTRE[i]! * 100}%`,
            left: `${i * 14.9}%`,
            background: COLOURS[i],
          }}
        />
      ))}
    </div>
  );
}
