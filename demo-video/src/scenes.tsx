import {
  AbsoluteFill,
  Sequence,
  interpolate,
  staticFile,
  useCurrentFrame,
  useVideoConfig,
} from "remotion";
import { Video } from "@remotion/media";
import {
  C,
  Body,
  Enter,
  Eyebrow,
  FilmWindow,
  Mark,
  Pill,
  SceneBase,
  ShotLabel,
  Title,
  clamp,
  display,
  sans,
} from "./kit";
import { footage } from "./media-manifest";

export const Opening = () => {
  const f = useCurrentFrame();
  const { fps } = useVideoConfig();
  return (
    <SceneBase>
      {footage.opening && (
        <AbsoluteFill style={{ opacity: 0.18 }}>
          <Video
            name="Opening match ambience"
            src={staticFile(footage.opening)}
            muted
            loop
            style={{ width: "100%", height: "100%" }}
          />
        </AbsoluteFill>
      )}
      <Enter style={{ position: "absolute", left: 96, top: 205, width: 860 }}>
        <Eyebrow>60 minutes. Everything to play for.</Eyebrow>
        <Title size={156}>
          TWO DOWN.
          <br />
          <span style={{ color: C.gold }}>STILL IN IT.</span>
        </Title>
        <Body style={{ fontSize: 31, maxWidth: 710 }}>
          The score tells you what happened.
          <br />
          The game has a deeper story.
        </Body>
      </Enter>
      <Enter
        delay={0.25}
        style={{ position: "absolute", left: 1090, top: 230, width: 640 }}
      >
        <div
          style={{
            display: "flex",
            justifyContent: "space-between",
            borderBottom: `1px solid ${C.border}`,
            paddingBottom: 26,
            fontSize: 18,
            color: C.muted,
            letterSpacing: 2,
          }}
        >
          <span>NORTHBRIDGE</span>
          <span>HARBOUR CITY</span>
        </div>
        <div
          style={{
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            fontFamily: display,
            fontSize: 256,
            lineHeight: 1.3,
          }}
        >
          <span style={{ color: C.blue }}>0</span>
          <span style={{ fontSize: 80, color: C.border }}>:</span>
          <span style={{ color: C.gold }}>2</span>
        </div>
        <div
          style={{
            height: 100,
            overflow: "hidden",
            borderTop: `1px solid ${C.border}`,
            paddingTop: 22,
          }}
        >
          <svg viewBox="0 0 640 80" fill="none" width="640" height="80">
            <path
              d="M0 64L38 66L75 49L110 55L154 60L192 47L230 50L268 40L306 45L344 29L382 37L420 23L458 26L496 14L534 19L572 6L610 10L640 2"
              stroke={C.blue}
              strokeWidth="4"
              strokeDasharray="800"
              strokeDashoffset={interpolate(f, [fps, 5 * fps], [800, 0], clamp)}
            />
          </svg>
        </div>
        <div style={{ marginTop: 18, fontSize: 18, color: C.muted }}>
          The pressure is shifting.
        </div>
        <div
          style={{
            marginTop: 45,
            fontSize: 12,
            letterSpacing: 1.4,
            color: "#718292",
          }}
        >
          SYNTHETIC SCENARIO · OPENING ANIMATION
        </div>
      </Enter>
      <Enter delay={1.2} style={{ position: "absolute", left: 96, top: 836 }}>
        <Pill>See the game behind the score</Pill>
      </Enter>
    </SceneBase>
  );
};

export const Introduction = () => (
  <SceneBase>
    <Enter style={{ position: "absolute", left: 96, top: 190, width: 520 }}>
      <Eyebrow>Live match intelligence</Eyebrow>
      <Title size={118}>
        THIS IS
        <br />
        <span style={{ color: C.gold }}>MATCHLENS.</span>
      </Title>
      <Body>
        One match, explained for
        <br />
        every audience.
      </Body>
      <div
        style={{ display: "flex", flexWrap: "wrap", gap: 12, marginTop: 38 }}
      >
        <Pill color={C.blue}>Synthetic events</Pill>
        <Pill color={C.green}>Explainable insights</Pill>
      </div>
    </Enter>
    <Enter
      delay={0.25}
      style={{
        position: "absolute",
        left: 680,
        top: 170,
        width: 1144,
        height: 677,
      }}
    >
      <FilmWindow
        id="introduction"
        label="The Live experience"
        style={{ width: "100%", height: "100%" }}
      />
    </Enter>
    <Enter
      delay={1.1}
      style={{
        position: "absolute",
        left: 96,
        top: 896,
        display: "flex",
        gap: 65,
      }}
    >
      <ShotLabel number="01">Football-realistic events</ShotLabel>
      <ShotLabel number="02">Deterministic statistics</ShotLabel>
      <ShotLabel number="03">Audience experiences</ShotLabel>
    </Enter>
  </SceneBase>
);

export const Evidence = () => {
  const f = useCurrentFrame();
  const { fps } = useVideoConfig();
  const replay = f >= 9.9 * fps;
  if (!replay)
    return (
      <SceneBase>
        <Enter style={{ position: "absolute", left: 96, top: 200, width: 800 }}>
          <Eyebrow>Explainable by design</Eyebrow>
          <Title size={104}>
            WHAT CHANGED?
            <br />
            <span style={{ color: C.green }}>
              SHOW THE
              <br />
              EVIDENCE.
            </span>
          </Title>
          <Body style={{ maxWidth: 650 }}>
            Inspect the claims, their sources,
            <br />
            and what the checks actually cover.
          </Body>
          <div style={{ marginTop: 40 }}>
            <Pill color={C.green}>Follow the evidence</Pill>
          </div>
        </Enter>
        <FilmWindow
          id="evidence"
          label="Inspect the evidence"
          style={{
            position: "absolute",
            left: 1020,
            top: 160,
            width: 804,
            height: 770,
          }}
        />
      </SceneBase>
    );
  return (
    <SceneBase>
      <Enter style={{ position: "absolute", left: 96, top: 156 }}>
        <Eyebrow>Explainable by design</Eyebrow>
        <Title size={78}>A reason behind every moment.</Title>
      </Enter>
      <div
        style={{
          position: "absolute",
          left: 96,
          top: 310,
          width: 1372,
          height: 570,
        }}
      >
        {replay ? (
          <Sequence from={9.9 * fps} layout="none">
            <FilmWindow
              id="replay"
              label="Replay the moment"
              style={{ width: "100%", height: "100%" }}
            />
          </Sequence>
        ) : (
          <FilmWindow
            id="evidence"
            label="Inspect the evidence"
            style={{ width: "100%", height: "100%" }}
          />
        )}
      </div>
      <div style={{ position: "absolute", left: 1525, top: 340, width: 290 }}>
        <Enter delay={0.6}>
          <div style={{ fontFamily: display, fontSize: 56, color: C.gold }}>
            EXPLAIN.
          </div>
          <Body style={{ fontSize: 22, marginTop: 8 }}>
            What changed, and why it matters.
          </Body>
        </Enter>
        <Enter delay={0.6}>
          <div
            style={{
              fontFamily: display,
              fontSize: 56,
              color: C.green,
              marginTop: 30,
            }}
          >
            INSPECT.
          </div>
          <Body style={{ fontSize: 22, marginTop: 8 }}>
            Claims connected to match data.
          </Body>
        </Enter>
        <Enter delay={0.8}>
          <div
            style={{
              fontFamily: display,
              fontSize: 56,
              color: C.blue,
              marginTop: 30,
            }}
          >
            REPLAY.
          </div>
          <Body style={{ fontSize: 22, marginTop: 8 }}>
            Follow the story back to the action.
          </Body>
        </Enter>
      </div>
    </SceneBase>
  );
};

const FlowNode = ({
  x,
  y,
  label,
  sub,
  at,
  color = C.blue,
}: {
  x: number;
  y: number;
  label: string;
  sub: string;
  at: number;
  color?: string;
}) => {
  const f = useCurrentFrame();
  const { fps } = useVideoConfig();
  const active = f > at * fps;
  return (
    <div
      style={{
        position: "absolute",
        left: x,
        top: y,
        width: 254,
        height: 108,
        padding: "21px 25px",
        border: `1px solid ${active ? color : C.border}`,
        borderRadius: 12,
        background: active ? `${color}12` : C.panel,
        opacity: interpolate(
          f,
          [at * fps - 10, at * fps + 12],
          [0.26, 1],
          clamp,
        ),
      }}
    >
      <div style={{ fontFamily: display, fontSize: 34, color: C.text }}>
        {label}
      </div>
      <div
        style={{ fontFamily: sans, fontSize: 16, color: C.muted, marginTop: 4 }}
      >
        {sub}
      </div>
    </div>
  );
};

export const Agents = () => {
  const f = useCurrentFrame();
  const { fps } = useVideoConfig();
  if (f >= 15 * fps && footage.agents)
    return (
      <Sequence from={15 * fps} layout="none">
        <SceneBase>
          <Enter style={{ position: "absolute", left: 96, top: 156 }}>
            <Eyebrow>Inside the functioning application</Eyebrow>
            <Title size={78}>Inspect the workflow in Agent Room.</Title>
          </Enter>
          <FilmWindow
            id="agents"
            label="Agent Room · recorded demo"
            style={{
              position: "absolute",
              left: 96,
              top: 295,
              width: 1728,
              height: 645,
            }}
          />
        </SceneBase>
      </Sequence>
    );
  return (
    <SceneBase>
      <Enter style={{ position: "absolute", left: 96, top: 160 }}>
        <Eyebrow>Agentic orchestration</Eyebrow>
        <Title size={82}>A team behind every insight.</Title>
      </Enter>
      <div
        style={{
          position: "absolute",
          left: 96,
          top: 330,
          width: 1728,
          height: 370,
        }}
      >
        <svg
          width="1728"
          height="360"
          style={{ position: "absolute", inset: 0 }}
          fill="none"
        >
          {[
            "M254 180H300V82H350",
            "M254 180H300V278H350",
            "M604 82H680V180H754",
            "M604 278H680V180H754",
            "M1008 180H1080V82H1150",
            "M1008 180H1080V278H1150",
            "M1404 82H1490V180H1550",
            "M1404 278H1490V180H1550",
          ].map((d, i) => (
            <path
              key={d}
              d={d}
              stroke={i < 4 ? C.blue : C.green}
              strokeWidth="2"
              opacity={0.6}
              strokeDasharray="380"
              strokeDashoffset={interpolate(
                f,
                [i * 0.5 * fps, (i * 0.5 + 2) * fps],
                [380, 0],
                clamp,
              )}
            />
          ))}
        </svg>
        <FlowNode
          x={0}
          y={126}
          label="Coordinator"
          sub="Selects and routes"
          at={0.3}
        />
        <FlowNode
          x={350}
          y={28}
          label="Tactical"
          sub="Explains the shift"
          at={5.2}
        />
        <FlowNode
          x={350}
          y={224}
          label="Narrator"
          sub="Shapes the story"
          at={5.6}
        />
        <FlowNode
          x={754}
          y={126}
          label="Fact-checker"
          sub="Checks structured claims"
          at={8.7}
          color={C.green}
        />
        <FlowNode
          x={1150}
          y={28}
          label="Producer"
          sub="Plans broadcast overlays"
          at={12.5}
          color={C.gold}
        />
        <FlowNode
          x={1150}
          y={224}
          label="Personaliser"
          sub="Adapts the experience"
          at={13.6}
          color={C.gold}
        />
        <div
          style={{
            position: "absolute",
            left: 1550,
            top: 135,
            fontFamily: display,
            fontSize: 43,
            color: C.gold,
          }}
        >
          PUBLISH
          <br />
          <span style={{ fontFamily: sans, fontSize: 16, color: C.muted }}>
            Fans + studios
          </span>
        </div>
      </div>
      <Enter
        delay={9.5}
        style={{
          position: "absolute",
          left: 570,
          top: 735,
          width: 780,
          padding: "22px 28px",
          borderTop: `1px solid ${C.green}55`,
          textAlign: "center",
        }}
      >
        <div style={{ fontSize: 22, color: C.green }}>
          Deterministic statistics engine
        </div>
        <div style={{ fontSize: 17, color: C.muted, marginTop: 8 }}>
          The evidence layer underneath the agent workflow
        </div>
      </Enter>
      <div
        style={{
          position: "absolute",
          left: 96,
          top: 900,
          fontSize: 15,
          color: C.muted,
          letterSpacing: 1.2,
        }}
      >
        WORKFLOW EXPLANATION · MICROSOFT AGENT FRAMEWORK
      </div>
    </SceneBase>
  );
};

export const Correction = () => {
  const f = useCurrentFrame();
  const { fps } = useVideoConfig();
  const corrected = f > 3.2 * fps;
  return (
    <SceneBase>
      <Enter style={{ position: "absolute", left: 96, top: 195, width: 540 }}>
        <Eyebrow>Live data can change</Eyebrow>
        <Title size={95}>
          THE FACTS
          <br />
          CHANGE.
          <br />
          <span style={{ color: C.green }}>
            THE STORY
            <br />
            FOLLOWS.
          </span>
        </Title>
        <div
          style={{
            marginTop: 32,
            display: "flex",
            gap: 22,
            alignItems: "center",
            fontFamily: display,
            fontSize: 56,
          }}
        >
          <span
            style={{
              color: C.muted,
              textDecoration: corrected ? "line-through" : "none",
            }}
          >
            #10
          </span>
          <span style={{ color: C.border }}>→</span>
          <span style={{ color: C.green, opacity: corrected ? 1 : 0.15 }}>
            #9
          </span>
        </div>
        <div style={{ fontSize: 16, color: C.muted, marginTop: 10 }}>
          Scorer attribution · synthetic feed correction
        </div>
      </Enter>
      <Enter
        delay={0.25}
        style={{
          position: "absolute",
          left: 700,
          top: 180,
          width: 1124,
          height: 660,
        }}
      >
        <FilmWindow
          id="correction"
          label="A correction in the application"
          style={{ width: "100%", height: "100%" }}
        />
      </Enter>
      <Enter
        delay={4.5}
        style={{
          position: "absolute",
          left: 700,
          top: 880,
          display: "flex",
          gap: 18,
        }}
      >
        <Pill color={C.green}>Update statistics</Pill>
        <Pill color={C.green}>Revise insights</Pill>
        <Pill color={C.green}>Retract stale claims</Pill>
      </Enter>
    </SceneBase>
  );
};

export const Multiverse = () => {
  const f = useCurrentFrame();
  const { fps } = useVideoConfig();
  const personas = [
    { name: "Casual fan", language: "Hindi", color: C.gold },
    { name: "Analyst", language: "English", color: C.blue },
    { name: "Player focus", language: "Spanish", color: C.green },
    { name: "Coach", language: "Tactical brief", color: "#CAA1E9" },
  ];
  const current = f / fps < 5.6 ? 0 : f / fps < 7.0 ? 1 : f / fps < 9.0 ? 2 : 3;
  const tiles = personas.every((_, i) => footage[`multiverse${i + 1}`]);
  return (
    <SceneBase>
      <Enter style={{ position: "absolute", left: 96, top: 151 }}>
        <Eyebrow>Personalisation, made visible</Eyebrow>
        <Title size={86}>
          ONE MATCH. <span style={{ color: C.gold }}>FOUR PERSPECTIVES.</span>
        </Title>
      </Enter>
      <Enter
        delay={0.25}
        style={{
          position: "absolute",
          left: 96,
          top: 315,
          width: 1728,
          height: 570,
        }}
      >
        {tiles ? (
          <div
            style={{
              display: "grid",
              gridTemplateColumns: "repeat(4,1fr)",
              gap: 18,
              height: "100%",
            }}
          >
            {personas.map((p, i) => (
              <div
                key={p.name}
                style={{
                  position: "relative",
                  overflow: "hidden",
                  borderRadius: 12,
                  border: `1px solid ${current === i ? p.color : C.border}`,
                  boxShadow: current === i ? `0 0 40px ${p.color}14` : "none",
                }}
              >
                <Video
                  name={`${p.name} actual product view`}
                  src={staticFile(footage[`multiverse${i + 1}`])}
                  muted
                  objectFit="contain"
                  style={{ width: "100%", height: "100%" }}
                />
                <div
                  style={{
                    position: "absolute",
                    inset: 0,
                    background: current === i ? "transparent" : "#080D1425",
                    pointerEvents: "none",
                  }}
                />
              </div>
            ))}
          </div>
        ) : (
          <FilmWindow
            id="multiverse"
            label="Multiverse"
            style={{ width: "100%", height: "100%" }}
          />
        )}
      </Enter>
      <div
        style={{
          position: "absolute",
          left: 96,
          top: 894,
          width: 1728,
          display: "grid",
          gridTemplateColumns: "repeat(4,1fr)",
          gap: 24,
        }}
      >
        {personas.map((p, i) => (
          <div
            key={p.name}
            style={{
              borderTop: `2px solid ${i === current ? p.color : C.border}`,
              paddingTop: 14,
              display: "flex",
              gap: 14,
              alignItems: "baseline",
              opacity: i === current ? 1 : 0.55,
            }}
          >
            <span style={{ fontFamily: display, fontSize: 31, color: p.color }}>
              {p.name}
            </span>
            <span style={{ fontSize: 17, color: C.muted }}>{p.language}</span>
          </div>
        ))}
      </div>
    </SceneBase>
  );
};

export const Pundit = () => {
  const f = useCurrentFrame();
  const { fps } = useVideoConfig();
  const catchup = f >= 9 * fps;
  return (
    <SceneBase>
      <Enter style={{ position: "absolute", left: 96, top: 195, width: 570 }}>
        <Eyebrow>{catchup ? "Missed the action?" : "Ask the pundit"}</Eyebrow>
        <Title size={95}>
          {catchup ? (
            <>
              BACK IN
              <br />
              THE GAME.
              <br />
              <span style={{ color: C.gold }}>IN SECONDS.</span>
            </>
          ) : (
            <>
              “WHY DID
              <br />
              THE MOMENTUM
              <br />
              <span style={{ color: C.blue }}>CHANGE?”</span>
            </>
          )}
        </Title>
        <Body style={{ fontSize: 26 }}>
          {catchup
            ? "A catch-up built from the match so far."
            : "An answer with numbers—and moments you can revisit."}
        </Body>
      </Enter>
      <div
        style={{
          position: "absolute",
          left: 1020,
          top: 180,
          width: 804,
          height: 740,
        }}
      >
        {catchup ? (
          <Sequence from={9 * fps} layout="none">
            <FilmWindow
              id="catchup"
              label="What did I miss?"
              style={{ width: "100%", height: "100%" }}
            />
          </Sequence>
        ) : (
          <FilmWindow
            id="pundit"
            label="Ask the pundit"
            style={{ width: "100%", height: "100%" }}
          />
        )}
      </div>
      <div style={{ position: "absolute", left: 96, top: 820 }}>
        <Pill color={catchup ? C.gold : C.blue}>
          {catchup
            ? "Context when you return"
            : "From a question to its evidence"}
        </Pill>
      </div>
    </SceneBase>
  );
};

const Tech = ({
  label,
  detail,
  color,
}: {
  label: string;
  detail: string;
  color: string;
}) => (
  <div
    style={{
      padding: "26px 32px",
      border: `1px solid ${C.border}`,
      borderLeft: `3px solid ${color}`,
      borderRadius: 12,
      background: C.panel,
    }}
  >
    <div style={{ fontFamily: display, fontSize: 37, color: C.text }}>
      {label}
    </div>
    <div style={{ fontSize: 19, color: C.muted, marginTop: 9 }}>{detail}</div>
  </div>
);
export const Platform = () => {
  const f = useCurrentFrame();
  const { fps } = useVideoConfig();
  const winner = f >= 13.5 * fps;
  return (
    <SceneBase>
      <Enter style={{ position: "absolute", left: 96, top: 190, width: 650 }}>
        <Eyebrow>From data to experience</Eyebrow>
        <Title size={96}>
          BUILT WITH
          <br />
          <span style={{ color: C.blue }}>MICROSOFT.</span>
          <br />
          MADE FOR
          <br />
          AZURE.
        </Title>
        <Body style={{ fontSize: 23, maxWidth: 540 }}>
          A documented deployment architecture for live match intelligence.
        </Body>
      </Enter>
      <div
        style={{
          position: "absolute",
          left: 820,
          top: 180,
          width: 1004,
          height: 680,
        }}
      >
        {winner ? (
          <Sequence from={13.5 * fps} layout="none">
            <FilmWindow
              id="winner"
              label="The comeback"
              trimBefore={2 * fps}
              style={{ width: "100%", height: "100%" }}
            />
          </Sequence>
        ) : (
          <div style={{ display: "flex", flexDirection: "column", gap: 18 }}>
            <Enter delay={0.2}>
              <Tech
                label="Microsoft Agent Framework"
                detail="Coordinates the specialist agents"
                color={C.blue}
              />
            </Enter>
            <Enter delay={1.8}>
              <Tech
                label="Azure OpenAI in Microsoft Foundry"
                detail="Generates the grounded explanations"
                color={C.blue}
              />
            </Enter>
            <Enter delay={4}>
              <Tech
                label="Azure Container Apps"
                detail="Frontend and backend deployment"
                color={C.gold}
              />
            </Enter>
            <Enter delay={5.6}>
              <Tech
                label="Azure Cosmos DB"
                detail="Persists match history and outputs"
                color={C.green}
              />
            </Enter>
          </div>
        )}
      </div>
    </SceneBase>
  );
};
export const Closing = ({ demoUrl }: { demoUrl: string }) => (
  <SceneBase>
    <Enter
      style={{
        position: "absolute",
        left: 96,
        top: 195,
        width: 1728,
        textAlign: "center",
      }}
    >
      <div
        style={{
          display: "flex",
          justifyContent: "center",
          alignItems: "center",
          gap: 22,
        }}
      >
        <Mark size={94} />
        <div style={{ fontFamily: display, fontSize: 120, letterSpacing: 2 }}>
          MATCHLENS
        </div>
      </div>
      <div
        style={{
          width: 92,
          height: 3,
          background: C.gold,
          margin: "45px auto",
        }}
      />
      <Title size={120}>
        EVERY MOMENT,
        <br />
        <span style={{ color: C.gold }}>UNDERSTOOD.</span>
      </Title>
      <Body style={{ fontSize: 30, marginTop: 30 }}>Beyond the scoreline.</Body>
    </Enter>
    <Enter
      delay={1.5}
      style={{
        position: "absolute",
        left: 96,
        top: 840,
        width: 1728,
        textAlign: "center",
      }}
    >
      <span
        style={{
          display: "inline-block",
          padding: "16px 30px",
          border: `1px solid ${C.border}`,
          borderRadius: 40,
          fontFamily: sans,
          fontSize: 23,
          color: demoUrl ? C.text : C.muted,
        }}
      >
        {demoUrl || "Explore Matchlens · public demo URL to be added"}
      </span>
    </Enter>
  </SceneBase>
);
