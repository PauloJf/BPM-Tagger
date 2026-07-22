import { useEffect, useRef, useState, type RefObject } from "react";
import { api } from "../lib/api";
import type { Waveform } from "../lib/types";

// Resolve a CSS custom property to a concrete canvas-compatible color string.
function resolveColor(varExpr: string): string {
  const el = document.createElement("b");
  el.style.cssText = "display:none;";
  el.style.color = varExpr;
  document.body.appendChild(el);
  const c = getComputedStyle(el).color;
  document.body.removeChild(el);
  return c;
}

/** Canvas waveform with playhead + scrubbing over an <audio> element.
 *  Ported verbatim from the track.html waveform logic. */
export function useWaveform(
  canvasRef: RefObject<HTMLCanvasElement>,
  audioRef: RefObject<HTMLAudioElement>,
  filePath: string,
  // Flips true once the <canvas>/<audio> are actually mounted (i.e. the track
  // has loaded). Without it the effect would run before the canvas exists and
  // never re-run, so the peaks would never be fetched.
  enabled: boolean,
  // True when the shared audio element is currently playing THIS track. When
  // false the waveform is drawn static (no playhead) and no audio/scrub
  // listeners are wired — so it doesn't seek/read another track's playback.
  isActive: boolean,
) {
  const [loading, setLoading] = useState(true);
  const peaksRef = useRef<number[] | null>(null);

  useEffect(() => {
    if (!enabled || !filePath) return;
    const canvas = canvasRef.current;
    const audio = audioRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    // Concrete colors resolved from CSS vars for canvas use. Re-resolved when
    // the accent/theme changes (the "bpm:appearance" event) since the canvas
    // can't read the live CSS variable.
    let WF_ACCENT = resolveColor("var(--accent)");
    let WF_WAVE = resolveColor("var(--wave)");

    let logicalW = 0;
    let logicalH = 88;
    let rafId: number | null = null;
    let isDragging = false;

    function initCanvas() {
      const dpr = window.devicePixelRatio || 1;
      logicalW = canvas!.offsetWidth;
      logicalH = canvas!.offsetHeight || 88;
      canvas!.width = Math.round(logicalW * dpr);
      canvas!.height = Math.round(logicalH * dpr);
      ctx!.setTransform(dpr, 0, 0, dpr, 0, 0);
    }

    function render(currentTime: number, duration: number) {
      ctx!.clearRect(0, 0, logicalW, logicalH);
      const peaks = peaksRef.current;
      if (!peaks) return;
      const n = peaks.length;
      const barW = logicalW / n;
      const progress = duration > 0 ? Math.min(1, currentTime / duration) : 0;
      const midY = logicalH / 2;
      for (let i = 0; i < n; i++) {
        const h = Math.max(2, peaks[i] * logicalH * 0.85);
        const y = midY - h / 2;
        const x = i * barW;
        const bw = Math.max(1, barW - 1.5);
        ctx!.fillStyle = i / n < progress ? WF_ACCENT : WF_WAVE;
        ctx!.beginPath();
        // roundRect is widely supported but guard for older engines (the
        // original vanilla code did the same).
        if ((ctx as { roundRect?: unknown }).roundRect) ctx!.roundRect(x + 0.5, y, bw, h, Math.min(1, bw * 0.3));
        else ctx!.rect(x + 0.5, y, bw, h);
        ctx!.fill();
      }
      if (duration > 0) {
        const headX = progress * logicalW;
        ctx!.fillStyle = "rgba(255,255,255,0.75)";
        ctx!.fillRect(headX - 0.75, 0, 1.5, logicalH);
      }
    }

    function renderNow() {
      if (isActive && audio) render(audio.currentTime, audio.duration || 0);
      else render(0, 0); // static: peaks only, no playhead
    }

    function startRaf() {
      if (rafId) return;
      const tick = () => {
        if (audio && !audio.paused && peaksRef.current) {
          render(audio.currentTime, audio.duration || 0);
          rafId = requestAnimationFrame(tick);
        } else {
          rafId = null;
        }
      };
      rafId = requestAnimationFrame(tick);
    }

    function onTimeUpdate() {
      if (audio && audio.paused) render(audio.currentTime, audio.duration || 0);
    }
    function onEnded() {
      if (audio) render(audio.duration || 0, audio.duration || 0);
    }

    function seekFromX(clientX: number) {
      const rect = canvas!.getBoundingClientRect();
      const ratio = Math.max(0, Math.min(1, (clientX - rect.left) / rect.width));
      if (audio && audio.duration) {
        audio.currentTime = ratio * audio.duration;
        render(audio.currentTime, audio.duration);
      }
    }

    const onMouseDown = (e: MouseEvent) => {
      isDragging = true;
      seekFromX(e.clientX);
    };
    const onMouseMove = (e: MouseEvent) => {
      if (isDragging) seekFromX(e.clientX);
    };
    const onMouseUp = () => {
      isDragging = false;
    };
    const onTouchStart = (e: TouchEvent) => {
      e.preventDefault();
      isDragging = true;
      seekFromX(e.touches[0].clientX);
    };
    const onTouchMove = (e: TouchEvent) => {
      e.preventDefault();
      if (isDragging) seekFromX(e.touches[0].clientX);
    };
    const onTouchEnd = () => {
      isDragging = false;
    };
    const onResize = () => {
      initCanvas();
      renderNow();
    };
    // Accent or theme changed → re-resolve the canvas colors and repaint.
    const onAppearance = () => {
      WF_ACCENT = resolveColor("var(--accent)");
      WF_WAVE = resolveColor("var(--wave)");
      renderNow();
    };

    window.addEventListener("resize", onResize);
    window.addEventListener("bpm:appearance", onAppearance);
    if (isActive) {
      canvas.addEventListener("mousedown", onMouseDown);
      document.addEventListener("mousemove", onMouseMove);
      document.addEventListener("mouseup", onMouseUp);
      canvas.addEventListener("touchstart", onTouchStart, { passive: false });
      canvas.addEventListener("touchmove", onTouchMove, { passive: false });
      canvas.addEventListener("touchend", onTouchEnd);
      if (audio) {
        audio.addEventListener("timeupdate", onTimeUpdate);
        audio.addEventListener("play", startRaf);
        audio.addEventListener("ended", onEnded);
        if (!audio.paused) startRaf();  // already playing when we mounted/activated
      }
    }

    initCanvas();

    let cancelled = false;
    setLoading(true);
    api
      .get<Waveform>(`/api/waveform?path=${encodeURIComponent(filePath)}`)
      .then((d) => {
        if (cancelled) return;
        setLoading(false);
        if (d.error) {
          console.warn("Waveform:", d.error);
          return;
        }
        peaksRef.current = d.peaks;
        renderNow();
      })
      .catch((e) => {
        if (cancelled) return;
        setLoading(false);
        console.warn("Waveform fetch failed:", e);
      });

    return () => {
      cancelled = true;
      if (rafId) cancelAnimationFrame(rafId);
      canvas.removeEventListener("mousedown", onMouseDown);
      document.removeEventListener("mousemove", onMouseMove);
      document.removeEventListener("mouseup", onMouseUp);
      canvas.removeEventListener("touchstart", onTouchStart);
      canvas.removeEventListener("touchmove", onTouchMove);
      canvas.removeEventListener("touchend", onTouchEnd);
      window.removeEventListener("resize", onResize);
      window.removeEventListener("bpm:appearance", onAppearance);
      if (audio) {
        audio.removeEventListener("timeupdate", onTimeUpdate);
        audio.removeEventListener("play", startRaf);
        audio.removeEventListener("ended", onEnded);
      }
    };
  }, [canvasRef, audioRef, filePath, enabled, isActive]);

  return { loading };
}
