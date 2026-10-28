import { useEffect, useState, type CSSProperties, type ReactNode } from "react";
import {
  AbsoluteFill,
  Easing,
  continueRender,
  delayRender,
  interpolate,
  staticFile,
  useCurrentFrame,
  useVideoConfig,
} from "remotion";
import { Video } from "@remotion/media";
import { footage } from "./media-manifest";
import { scenes, type ShotId } from "./storyboard";

export const C = {
  bg: "#080D14",
  panel: "#101924",
  border: "#263443",
  text: "#F4F1E8",
  muted: "#9FAFBD",
  gold: "#F2C16D",
  blue: "#70A7FF",
  green: "#8CE0BA",
  red: "#E88B80",
};
export const display = '"Matchlens Display", "Arial Narrow", sans-serif';
export const sans = '"Matchlens Text", Arial, sans-serif';
export const clamp = {
  extrapolateLeft: "clamp",
  extrapolateRight: "clamp",
} as const;

export const FontGate = () => {
  const [handle] = useState(() => delayRender("Load local Matchlens fonts"));
  useEffect(() => {
    Promise.all([
      document.fonts.load('700 80px "Matchlens Display"'),
      document.fonts.load('400 32px "Matchlens Text"'),
    ])
      .then(() => continueRender(handle))
      .catch(() => continueRender(handle));
  }, [handle]);
  return null;
};

export const Mark = ({ size = 42 }: { size?: number }) => (
  <svg width={size} height={size} viewBox="0 0 64 64" fill="none">
    <path
      d="M8 48V16H17L32 36L47 16H56V48H45V33L32 50L19 33V48H8Z"
      fill={C.text}
    />
    <path d="M24 11H40" stroke={C.gold} strokeWidth="4" />
  </svg>
);

export const Background = () => {
  const frame = useCurrentFrame();
  return (
    <AbsoluteFill style={{ background: C.bg, overflow: "hidden" }}>
      <AbsoluteFill
        style={{
          background:
            "radial-gradient(ellipse at 82% 32%, #19334b66 0%, transparent 54%), radial-gradient(ellipse at 3% 95%, #2a201566 0%, transparent 48%)",
        }}
      />
      <AbsoluteFill
        style={{
          opacity: 0.13,
          backgroundImage:
            "linear-gradient(#71849a25 1px,transparent 1px),linear-gradient(90deg,#71849a25 1px,transparent 1px)",
          backgroundSize: "80px 80px",
          translate: `${-frame / 45}px 0px`,
          width: 2080,
        }}
      />
      <div
        style={{
          position: "absolute",
          left: 96,
          top: 118,
          width: 1728,
          height: 1,
          background: C.border,
        }}
      />
    </AbsoluteFill>
  );
};

export const Chrome = ({ guide }: { guide: boolean }) => {
  const frame = useCurrentFrame();
  const { fps, durationInFrames } = useVideoConfig();
  const scene =
    scenes.find((s) => frame >= s.start * fps && frame < s.end * fps) ??
    scenes[0];
  return (
    <AbsoluteFill
      style={{ pointerEvents: "none", fontFamily: sans, color: C.text }}
    >
      <div
        style={{
          position: "absolute",
          top: 40,
          left: 96,
          display: "flex",
          alignItems: "center",
          gap: 14,
        }}
      >
        <Mark />
        <span style={{ fontFamily: display, fontSize: 38, letterSpacing: 1 }}>
          MATCHLENS
        </span>
        <span
          style={{
            width: 1,
            height: 25,
            background: C.border,
            margin: "0 12px",
          }}
        />
        <span style={{ fontSize: 17, letterSpacing: 3, color: C.muted }}>
          INSIDE THE GAME
        </span>
      </div>
      <div
        style={{
          position: "absolute",
          top: 53,
          right: 96,
          display: "flex",
          gap: 20,
          alignItems: "center",
          fontSize: 17,
          color: C.muted,
        }}
      >
        <span style={{ color: C.gold }}>
          {String(scenes.indexOf(scene) + 1).padStart(2, "0")}
        </span>
        <span>{scene.title}</span>
        <span style={{ color: C.border }}>/ 09</span>
      </div>
      <div
        style={{
          position: "absolute",
          bottom: 40,
          left: 96,
          fontSize: 15,
          letterSpacing: 1.4,
          color: C.muted,
        }}
      >
        SYNTHETIC MATCH · FICTIONAL TEAMS & PLAYERS
      </div>
      <div
        style={{
          position: "absolute",
          bottom: 40,
          right: 96,
          fontSize: 15,
          letterSpacing: 1.4,
          color: guide ? C.gold : C.muted,
        }}
      >
        {guide
          ? "WORKING CUT · VOICE PENDING"
          : "MATCHLENS · DEVELOPER HACKATHON"}
      </div>
      <div
        style={{
          position: "absolute",
          left: 96,
          right: 96,
          bottom: 23,
          height: 2,
          background: C.border,
        }}
      >
        <div
          style={{
            height: 2,
            width: `${(frame / (durationInFrames - 1)) * 100}%`,
            background: C.gold,
          }}
        />
      </div>
    </AbsoluteFill>
  );
};

export const Enter = ({
  children,
  delay = 0,
  style,
}: {
  children: ReactNode;
  delay?: number;
  style?: CSSProperties;
}) => {
  const f = useCurrentFrame();
  const { fps } = useVideoConfig();
  return (
    <div
      style={{
        ...style,
        opacity: interpolate(
          f,
          [delay * fps, delay * fps + 0.6 * fps],
          [0, 1],
          clamp,
        ),
        translate: interpolate(
          f,
          [delay * fps, delay * fps + 0.8 * fps],
          ["0px 28px", "0px 0px"],
          { ...clamp, easing: Easing.bezier(0.16, 1, 0.3, 1) },
        ),
      }}
    >
      {children}
    </div>
  );
};
export const Eyebrow = ({
  children,
  color = C.gold,
}: {
  children: ReactNode;
  color?: string;
}) => (
  <div
    style={{
      fontFamily: sans,
      fontSize: 19,
      fontWeight: 500,
      letterSpacing: 3.6,
      textTransform: "uppercase",
      color,
      marginBottom: 18,
    }}
  >
    {children}
  </div>
);
export const Title = ({
  children,
  size = 86,
  style,
}: {
  children: ReactNode;
  size?: number;
  style?: CSSProperties;
}) => (
  <h1
    style={{
      fontFamily: display,
      fontWeight: 700,
      fontSize: size,
      lineHeight: 0.98,
      letterSpacing: -0.6,
      color: C.text,
      margin: 0,
      ...style,
    }}
  >
    {children}
  </h1>
);
export const Body = ({
  children,
  style,
}: {
  children: ReactNode;
  style?: CSSProperties;
}) => (
  <p
    style={{
      fontFamily: sans,
      fontSize: 27,
      lineHeight: 1.45,
      color: C.muted,
      margin: "20px 0 0",
      ...style,
    }}
  >
    {children}
  </p>
);
export const Pill = ({
  children,
  color = C.gold,
}: {
  children: ReactNode;
  color?: string;
}) => (
  <span
    style={{
      display: "inline-flex",
      alignItems: "center",
      gap: 10,
      padding: "11px 18px",
      border: `1px solid ${color}55`,
      borderRadius: 30,
      color,
      fontFamily: sans,
      fontSize: 20,
      background: `${color}0B`,
    }}
  >
    <span style={{ width: 6, height: 6, borderRadius: 5, background: color }} />
    {children}
  </span>
);

export const FilmWindow = ({
  id,
  label,
  style,
  zoom = 1,
  origin = "50% 50%",
  trimBefore = 0,
}: {
  id: ShotId;
  label: string;
  style?: CSSProperties;
  zoom?: number;
  origin?: string;
  trimBefore?: number;
}) => {
  const path = footage[id];
  return (
    <div
      style={{
        position: "relative",
        overflow: "hidden",
        border: `1px solid ${C.border}`,
        borderRadius: 14,
        background: C.panel,
        boxShadow: "0 30px 100px #0007",
        ...style,
      }}
    >
      <div
        style={{
          height: 37,
          display: "flex",
          alignItems: "center",
          padding: "0 18px",
          gap: 7,
          borderBottom: `1px solid ${C.border}`,
          background: "#0B121C",
          fontFamily: sans,
        }}
      >
        <span
          style={{ width: 6, height: 6, borderRadius: 6, background: C.red }}
        />
        <span
          style={{ width: 6, height: 6, borderRadius: 6, background: C.gold }}
        />
        <span
          style={{ width: 6, height: 6, borderRadius: 6, background: C.green }}
        />
        <span
          style={{
            fontSize: 12,
            letterSpacing: 1.3,
            color: C.muted,
            marginLeft: 12,
          }}
        >
          MATCHLENS / {label.toUpperCase()}
        </span>
        <span
          style={{
            marginLeft: "auto",
            fontSize: 11,
            letterSpacing: 1.2,
            color: path ? C.green : C.gold,
          }}
        >
          {path ? "PRODUCT RECORDING" : "CAPTURE PENDING"}
        </span>
      </div>
      <div
        style={{ position: "absolute", inset: "37px 0 0", overflow: "hidden" }}
      >
        {path ? (
          <Video
            name={`${label} recording`}
            src={staticFile(path)}
            muted
            loop
            trimBefore={trimBefore}
            objectFit="contain"
            style={{
              width: "100%",
              height: "100%",
              transformOrigin: origin,
              scale: zoom,
            }}
          />
        ) : (
          <AbsoluteFill
            style={{
              alignItems: "center",
              justifyContent: "center",
              background:
                "radial-gradient(ellipse at center,#1D2D3E55,transparent 70%)",
            }}
          >
            <svg
              width="100%"
              height="100%"
              style={{ position: "absolute", inset: 0, opacity: 0.11 }}
            >
              <defs>
                <pattern
                  id={`hatch-${id}`}
                  width="28"
                  height="28"
                  patternUnits="userSpaceOnUse"
                >
                  <path d="M0 28L28 0" stroke={C.muted} />
                </pattern>
              </defs>
              <rect width="100%" height="100%" fill={`url(#hatch-${id})`} />
            </svg>
            <div
              style={{
                width: 78,
                height: 58,
                border: `1px solid ${C.gold}88`,
                borderRadius: 10,
                display: "grid",
                placeItems: "center",
                marginBottom: 24,
              }}
            >
              <svg width="24" height="26" viewBox="0 0 24 26">
                <path d="M2 1L23 13L2 25Z" fill={C.gold} />
              </svg>
            </div>
            <div style={{ fontFamily: display, fontSize: 45, color: C.text }}>
              {label}
            </div>
            <div
              style={{
                fontFamily: sans,
                fontSize: 18,
                color: C.muted,
                marginTop: 12,
              }}
            >
              Recording from ui-redesign-codex
            </div>
          </AbsoluteFill>
        )}
      </div>
    </div>
  );
};
export const SceneBase = ({ children }: { children: ReactNode }) => {
  const f = useCurrentFrame();
  return (
    <AbsoluteFill
      style={{
        fontFamily: sans,
        color: C.text,
        opacity: interpolate(f, [0, 7], [0, 1], clamp),
      }}
    >
      {children}
    </AbsoluteFill>
  );
};
export const ShotLabel = ({
  number,
  children,
}: {
  number: string;
  children: ReactNode;
}) => (
  <div
    style={{
      display: "flex",
      alignItems: "center",
      gap: 18,
      fontFamily: sans,
      fontSize: 22,
      color: C.muted,
    }}
  >
    <span style={{ fontFamily: display, fontSize: 30, color: C.gold }}>
      {number}
    </span>
    {children}
  </div>
);
