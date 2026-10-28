import {
  AbsoluteFill,
  Sequence,
  Track,
  interpolate,
  staticFile,
  useCurrentFrame,
} from "remotion";
import { Audio, Video } from "@remotion/media";
import {
  Background,
  C,
  Enter,
  Eyebrow,
  FontGate,
  Mark,
  Pill,
  Title,
  Body,
  clamp,
  display,
  sans,
} from "./kit";
import { Opening, Correction, Multiverse, Closing } from "./scenes";
import { BasicCaptions } from "./basic-captions";
import { v2 } from "./v2-timeline";
import "./film.css";

export type SubmissionProps = { demoUrl: string; showGuide: boolean };
type SceneId = (typeof v2.scenes)[number]["id"];
const takePaths: Record<string, string> = v2.voice;
const captions: Record<
  string,
  readonly {
    text: string;
    startMs: number;
    endMs: number;
    timestampMs: number;
    confidence: number;
  }[]
> = v2.captions;

const Shot = ({
  file,
  label,
  style,
  trimBefore = 0,
}: {
  file: string;
  label: string;
  style?: React.CSSProperties;
  trimBefore?: number;
}) => (
  <div
    style={{
      position: "absolute",
      overflow: "hidden",
      border: `1px solid ${C.border}`,
      borderRadius: 16,
      background: C.panel,
      ...style,
    }}
  >
    <div
      style={{
        position: "absolute",
        inset: "0 0 auto",
        height: 34,
        padding: "0 18px",
        display: "flex",
        alignItems: "center",
        justifyContent: "space-between",
        background: "#0b121c",
        borderBottom: `1px solid ${C.border}`,
        fontSize: 12,
        letterSpacing: 1.1,
        zIndex: 2,
      }}
    >
      <span>{label}</span>
      <span style={{ color: C.green }}>ACTUAL PRODUCT RECORDING</span>
    </div>
    <div style={{ position: "absolute", inset: "34px 0 0" }}>
      <Video
        name={label}
        src={staticFile(`footage/${file}`)}
        muted
        trimBefore={trimBefore}
        objectFit="contain"
        style={{ width: "100%", height: "100%" }}
      />
    </div>
  </div>
);
const Aside = ({
  kicker,
  title,
  body,
  color = C.gold,
}: {
  kicker: string;
  title: React.ReactNode;
  body: string;
  color?: string;
}) => (
  <Enter style={{ position: "absolute", left: 96, top: 210, width: 530 }}>
    <Eyebrow color={color}>{kicker}</Eyebrow>
    <Title size={92}>{title}</Title>
    <Body style={{ fontSize: 27, maxWidth: 490 }}>{body}</Body>
  </Enter>
);
const Intro = () => (
  <>
    <Aside
      kicker="Meet Matchlens"
      title={
        <>
          THE GAME
          <br />
          BEHIND
          <br />
          <span style={{ color: C.gold }}>THE SCORE.</span>
        </>
      }
      body="Football intelligence for fans, studios and coaches."
    />
    <Shot
      file="02-live.mp4"
      label="LIVE · SYNTHETIC MATCH"
      style={{ left: 670, top: 170, width: 1154, height: 700 }}
    />
    <div
      style={{
        position: "absolute",
        left: 96,
        top: 895,
        display: "flex",
        gap: 20,
      }}
    >
      <Pill color={C.blue}>Synthetic events + tracking</Pill>
      <Pill color={C.green}>Fictional teams + players</Pill>
    </div>
  </>
);

const Evidence = () => {
  const f = useCurrentFrame(),
    replayAt = 276;
  return (
    <>
      {f < replayAt ? (
        <>
          <Aside
            kicker="Explainable by design"
            title={
              <>
                WHY DID
                <br />
                IT CHANGE?
                <br />
                <span style={{ color: C.green }}>SHOW ME.</span>
              </>
            }
            body="Inspect the supporting claims, sources and verification scope."
            color={C.green}
          />
          <Shot
            file="03-evidence-detail.mp4"
            label="OPEN THE EVIDENCE"
            style={{ left: 1020, top: 160, width: 804, height: 770 }}
          />
        </>
      ) : (
        <Sequence from={replayAt} layout="none">
          <Enter style={{ position: "absolute", left: 96, top: 155 }}>
            <Eyebrow>Follow it back to the action</Eyebrow>
            <Title size={84}>From explanation to tactical replay.</Title>
          </Enter>
          <Shot
            file="04-replay.mp4"
            label="REPLAY THE MOMENT"
            style={{ left: 96, top: 310, width: 1728, height: 610 }}
          />
        </Sequence>
      )}
    </>
  );
};

const Agents = () => {
  const f = useCurrentFrame();
  if (f >= 210)
    return (
      <Sequence from={210} layout="none">
        <Enter style={{ position: "absolute", left: 96, top: 150 }}>
          <Eyebrow>Inside the product</Eyebrow>
          <Title size={82}>See the agents behind the explanation.</Title>
        </Enter>
        <Shot
          file="05-agents.mp4"
          label="AGENT ROOM · RECORDED DEMO"
          style={{ left: 96, top: 300, width: 1728, height: 630 }}
        />
      </Sequence>
    );
  const steps = [
    ["Coordinator", "Selects the moment"],
    ["Tactical + Narrator", "Develop the explanation"],
    ["Fact-checker", "Checks structured claims"],
    ["Producer + Personaliser", "Prepare the experiences"],
  ];
  return (
    <>
      <Enter style={{ position: "absolute", left: 96, top: 160 }}>
        <Eyebrow>Agentic orchestration</Eyebrow>
        <Title size={92}>Specialists. Shared evidence.</Title>
      </Enter>
      <div
        style={{
          position: "absolute",
          left: 96,
          top: 400,
          width: 1728,
          display: "flex",
          gap: 28,
        }}
      >
        {steps.map(([title, detail], i) => (
          <div
            key={title}
            style={{
              position: "relative",
              width: 411,
              padding: "38px 26px",
              border: `1px solid ${f > [0, 36, 96, 180][i] ? C.blue : C.border}`,
              borderRadius: 16,
              background: C.panel,
              opacity: interpolate(
                f,
                [[0, 36, 96, 180][i], [0, 36, 96, 180][i] + 16],
                [0.25, 1],
                clamp,
              ),
            }}
          >
            <div
              style={{
                fontFamily: display,
                fontSize: 43,
                color: i === 2 ? C.green : C.text,
                lineHeight: 1.1,
                minHeight: 95,
              }}
            >
              {title}
            </div>
            <div style={{ fontSize: 21, color: C.muted, marginTop: 24 }}>
              {detail}
            </div>
            {i < 3 && (
              <div
                style={{
                  position: "absolute",
                  right: -25,
                  top: 90,
                  fontSize: 32,
                  color: C.blue,
                }}
              >
                →
              </div>
            )}
          </div>
        ))}
      </div>
      <Enter delay={2.5} style={{ position: "absolute", left: 96, top: 790 }}>
        <Pill color={C.green}>
          Deterministic statistics underneath the workflow
        </Pill>
      </Enter>
    </>
  );
};

const Coach = () => {
  const f = useCurrentFrame();
  const step = f < 90 ? 0 : f < 135 ? 1 : f < 255 ? 2 : f < 330 ? 3 : 4;
  const labels = [
    "EVIDENCE + TRADE-OFFS",
    "FREEZE A MOMENT · BUILD IDEAS",
    "COMPARE WITH THE BASELINE",
    "RISK + UNCERTAINTY",
    "THE VERDICT · NO CLEAR WINNER",
  ];
  const files = [
    "11-coach-advice-detail.mp4",
    "12-coach-build.mp4",
    "13-coach-compare-detail.mp4",
    "14-coach-risk-detail.mp4",
    "15-coach-verdict-detail.mp4",
  ];
  const starts = [0, 90, 135, 255, 330];
  return (
    <>
      <Enter style={{ position: "absolute", left: 96, top: 145 }}>
        <Eyebrow color={C.green}>For coaches · explore the next move</Eyebrow>
        <Title size={79}>
          {step < 2
            ? "FROM A MATCH MOMENT TO TACTICAL IDEAS."
            : "A TEN-SECOND HORIZON. REAL TRADE-OFFS."}
        </Title>
      </Enter>
      <Sequence from={starts[step]} layout="none">
        <Shot
          file={files[step]}
          label={labels[step]}
          style={{ left: 96, top: 290, width: 1728, height: 625 }}
        />
      </Sequence>
      <div
        style={{
          position: "absolute",
          left: 96,
          top: 925,
          fontSize: 18,
          color: C.muted,
          display: "flex",
          gap: 32,
        }}
      >
        <span style={{ color: C.green }}>Same frozen situation</span>
        <span>Simulated outcomes · not predictions</span>
        {step >= 2 && <span>Evaluation wait omitted between shots</span>}
      </div>
    </>
  );
};

const Questions = () => {
  const f = useCurrentFrame(),
    cut = 252;
  return (
    <>
      <Aside
        kicker={f < cut ? "Ask the pundit" : "Missed the action?"}
        title={
          f < cut ? (
            <>
              ASK WHY.
              <br />
              <span style={{ color: C.blue }}>
                SEE THE
                <br />
                EVIDENCE.
              </span>
            </>
          ) : (
            <>
              GET BACK
              <br />
              IN THE
              <br />
              <span style={{ color: C.gold }}>GAME.</span>
            </>
          )
        }
        body={
          f < cut
            ? "Answers connected to numbers and match moments."
            : "A catch-up built from the story so far."
        }
      />
      {f < cut ? (
        <Shot
          file="08-pundit-detail.mp4"
          label="ASK THE PUNDIT"
          style={{ left: 1020, top: 180, width: 804, height: 740 }}
        />
      ) : (
        <Sequence from={cut} layout="none">
          <Shot
            file="09-catchup-detail.mp4"
            label="WHAT DID I MISS?"
            style={{ left: 1020, top: 180, width: 804, height: 740 }}
          />
        </Sequence>
      )}
    </>
  );
};

const Platform = () => {
  const f = useCurrentFrame();
  const tech = [
    ["Microsoft Agent Framework", "Agent orchestration"],
    ["Azure OpenAI in Microsoft Foundry", "Grounded explanations"],
    ["Azure Container Apps", "Application hosting"],
    ["Azure Cosmos DB", "Persisted match history"],
  ];
  return (
    <>
      <Aside
        kicker="Built with Microsoft and Azure"
        title={
          <>
            ONE PIPELINE.
            <br />
            <span style={{ color: C.blue }}>
              MANY
              <br />
              EXPERIENCES.
            </span>
          </>
        }
        body="From synthetic match events to explainable intelligence."
      />
      {f < 165 ? (
        <div
          style={{
            position: "absolute",
            left: 800,
            top: 185,
            width: 1024,
            display: "grid",
            gap: 18,
          }}
        >
          {tech.map(([name, detail], i) => (
            <Enter
              key={name}
              delay={i * 0.8}
              style={{
                padding: "24px 30px",
                border: `1px solid ${C.border}`,
                borderLeft: `3px solid ${i < 2 ? C.blue : C.green}`,
                borderRadius: 12,
                background: C.panel,
              }}
            >
              <div style={{ fontFamily: display, fontSize: 40 }}>{name}</div>
              <div style={{ fontSize: 22, color: C.muted, marginTop: 8 }}>
                {detail}
              </div>
            </Enter>
          ))}
        </div>
      ) : (
        <Sequence from={165} layout="none">
          <Shot
            file="10-winner.mp4"
            trimBefore={60}
            label="THE COMEBACK · 3–2"
            style={{ left: 800, top: 185, width: 1024, height: 690 }}
          />
        </Sequence>
      )}
    </>
  );
};

const Scene = ({ id, demoUrl }: { id: SceneId; demoUrl: string }) => {
  switch (id) {
    case "opening":
      return <Opening />;
    case "introduction":
      return <Intro />;
    case "evidence":
      return <Evidence />;
    case "agents":
      return <Agents />;
    case "correction":
      return <Correction />;
    case "coach":
      return <Coach />;
    case "multiverse":
      return <Multiverse />;
    case "pundit":
      return <Questions />;
    case "platform":
      return <Platform />;
    case "closing":
      return <Closing demoUrl={demoUrl} />;
  }
};

export const MatchlensSubmission = ({
  demoUrl,
  showGuide,
}: SubmissionProps) => {
  const frame = useCurrentFrame();
  const current =
    v2.scenes.find(
      (s) => frame >= s.startFrame && frame < s.startFrame + s.durationInFrames,
    ) ?? v2.scenes[0];
  const pending = v2.pending.length > 0;
  return (
    <AbsoluteFill style={{ background: C.bg, fontFamily: sans, color: C.text }}>
      <FontGate />
      <Background />
      <Track name="Updated product story">
        <Sequence
          name="The score is only the beginning"
          from={v2.scenes[0].startFrame}
          durationInFrames={v2.scenes[0].durationInFrames}
        >
          <Scene id="opening" demoUrl={demoUrl} />
        </Sequence>
        <Sequence
          name="Live football intelligence"
          from={v2.scenes[1].startFrame}
          durationInFrames={v2.scenes[1].durationInFrames}
        >
          <Scene id="introduction" demoUrl={demoUrl} />
        </Sequence>
        <Sequence
          name="Explain. Inspect. Replay."
          from={v2.scenes[2].startFrame}
          durationInFrames={v2.scenes[2].durationInFrames}
        >
          <Scene id="evidence" demoUrl={demoUrl} />
        </Sequence>
        <Sequence
          name="Specialists, grounded in evidence"
          from={v2.scenes[3].startFrame}
          durationInFrames={v2.scenes[3].durationInFrames}
        >
          <Scene id="agents" demoUrl={demoUrl} />
        </Sequence>
        <Sequence
          name="Trust when the feed changes"
          from={v2.scenes[4].startFrame}
          durationInFrames={v2.scenes[4].durationInFrames}
        >
          <Scene id="correction" demoUrl={demoUrl} />
        </Sequence>
        <Sequence
          name="Explore the next move"
          from={v2.scenes[5].startFrame}
          durationInFrames={v2.scenes[5].durationInFrames}
        >
          <Scene id="coach" demoUrl={demoUrl} />
        </Sequence>
        <Sequence
          name="One match. Every audience."
          from={v2.scenes[6].startFrame}
          durationInFrames={v2.scenes[6].durationInFrames}
        >
          <Scene id="multiverse" demoUrl={demoUrl} />
        </Sequence>
        <Sequence
          name="Ask. Catch up. Stay in the game."
          from={v2.scenes[7].startFrame}
          durationInFrames={v2.scenes[7].durationInFrames}
        >
          <Scene id="pundit" demoUrl={demoUrl} />
        </Sequence>
        <Sequence
          name="Built with Microsoft and Azure"
          from={v2.scenes[8].startFrame}
          durationInFrames={v2.scenes[8].durationInFrames}
        >
          <Scene id="platform" demoUrl={demoUrl} />
        </Sequence>
        <Sequence
          name="Every moment, understood"
          from={v2.scenes[9].startFrame}
          durationInFrames={v2.scenes[9].durationInFrames}
        >
          <Scene id="closing" demoUrl={demoUrl} />
        </Sequence>
      </Track>
      <Track name="Your voice">
        {takePaths.opening && (
          <Audio
            name="opening human narration"
            src={staticFile(takePaths.opening)}
            from={v2.scenes[0].startFrame}
            durationInFrames={v2.scenes[0].durationInFrames}
          />
        )}
        {takePaths.introduction && (
          <Audio
            name="introduction human narration"
            src={staticFile(takePaths.introduction)}
            from={v2.scenes[1].startFrame}
            durationInFrames={v2.scenes[1].durationInFrames}
          />
        )}
        {takePaths.evidence && (
          <Audio
            name="evidence human narration"
            src={staticFile(takePaths.evidence)}
            from={v2.scenes[2].startFrame}
            durationInFrames={v2.scenes[2].durationInFrames}
          />
        )}
        {takePaths.agents && (
          <Audio
            name="agents human narration"
            src={staticFile(takePaths.agents)}
            from={v2.scenes[3].startFrame}
            durationInFrames={v2.scenes[3].durationInFrames}
          />
        )}
        {takePaths.correction && (
          <Audio
            name="correction human narration"
            src={staticFile(takePaths.correction)}
            from={v2.scenes[4].startFrame}
            durationInFrames={v2.scenes[4].durationInFrames}
          />
        )}
        {takePaths.coach && (
          <Audio
            name="coach human narration"
            src={staticFile(takePaths.coach)}
            from={v2.scenes[5].startFrame}
            durationInFrames={v2.scenes[5].durationInFrames}
          />
        )}
        {takePaths.multiverse && (
          <Audio
            name="multiverse human narration"
            src={staticFile(takePaths.multiverse)}
            from={v2.scenes[6].startFrame}
            durationInFrames={v2.scenes[6].durationInFrames}
          />
        )}
        {takePaths.pundit && (
          <Audio
            name="pundit human narration"
            src={staticFile(takePaths.pundit)}
            from={v2.scenes[7].startFrame}
            durationInFrames={v2.scenes[7].durationInFrames}
          />
        )}
        {takePaths.platform && (
          <Audio
            name="platform human narration"
            src={staticFile(takePaths.platform)}
            from={v2.scenes[8].startFrame}
            durationInFrames={v2.scenes[8].durationInFrames}
          />
        )}
        {takePaths.closing && (
          <Audio
            name="closing human narration"
            src={staticFile(takePaths.closing)}
            from={v2.scenes[9].startFrame}
            durationInFrames={v2.scenes[9].durationInFrames}
          />
        )}
      </Track>
      <Track name="Synchronized captions">
        <Sequence
          name="opening subtitles"
          from={v2.scenes[0].startFrame}
          durationInFrames={v2.scenes[0].durationInFrames}
        >
          <BasicCaptions
            captions={[...captions.opening]}
            width={3000}
            style={{
              position: "absolute",
              bottom: 78,
              left: 210,
              scale: 0.5,
              transformOrigin: "bottom left",
            }}
          />
        </Sequence>
        <Sequence
          name="introduction subtitles"
          from={v2.scenes[1].startFrame}
          durationInFrames={v2.scenes[1].durationInFrames}
        >
          <BasicCaptions
            captions={[...captions.introduction]}
            width={3000}
            style={{
              position: "absolute",
              bottom: 78,
              left: 210,
              scale: 0.5,
              transformOrigin: "bottom left",
            }}
          />
        </Sequence>
        <Sequence
          name="evidence subtitles"
          from={v2.scenes[2].startFrame}
          durationInFrames={v2.scenes[2].durationInFrames}
        >
          <BasicCaptions
            captions={[...captions.evidence]}
            width={3000}
            style={{
              position: "absolute",
              bottom: 78,
              left: 210,
              scale: 0.5,
              transformOrigin: "bottom left",
            }}
          />
        </Sequence>
        <Sequence
          name="agents subtitles"
          from={v2.scenes[3].startFrame}
          durationInFrames={v2.scenes[3].durationInFrames}
        >
          <BasicCaptions
            captions={[...captions.agents]}
            width={3000}
            style={{
              position: "absolute",
              bottom: 78,
              left: 210,
              scale: 0.5,
              transformOrigin: "bottom left",
            }}
          />
        </Sequence>
        <Sequence
          name="correction subtitles"
          from={v2.scenes[4].startFrame}
          durationInFrames={v2.scenes[4].durationInFrames}
        >
          <BasicCaptions
            captions={[...captions.correction]}
            width={3000}
            style={{
              position: "absolute",
              bottom: 78,
              left: 210,
              scale: 0.5,
              transformOrigin: "bottom left",
            }}
          />
        </Sequence>
        <Sequence
          name="coach subtitles"
          from={v2.scenes[5].startFrame}
          durationInFrames={v2.scenes[5].durationInFrames}
        >
          <BasicCaptions
            captions={[...captions.coach]}
            width={3000}
            style={{
              position: "absolute",
              bottom: 78,
              left: 210,
              scale: 0.5,
              transformOrigin: "bottom left",
            }}
          />
        </Sequence>
        <Sequence
          name="multiverse subtitles"
          from={v2.scenes[6].startFrame}
          durationInFrames={v2.scenes[6].durationInFrames}
        >
          <BasicCaptions
            captions={[...captions.multiverse]}
            width={3000}
            style={{
              position: "absolute",
              bottom: 78,
              left: 210,
              scale: 0.5,
              transformOrigin: "bottom left",
            }}
          />
        </Sequence>
        <Sequence
          name="pundit subtitles"
          from={v2.scenes[7].startFrame}
          durationInFrames={v2.scenes[7].durationInFrames}
        >
          <BasicCaptions
            captions={[...captions.pundit]}
            width={3000}
            style={{
              position: "absolute",
              bottom: 78,
              left: 210,
              scale: 0.5,
              transformOrigin: "bottom left",
            }}
          />
        </Sequence>
        <Sequence
          name="platform subtitles"
          from={v2.scenes[8].startFrame}
          durationInFrames={v2.scenes[8].durationInFrames}
        >
          <BasicCaptions
            captions={[...captions.platform]}
            width={3000}
            style={{
              position: "absolute",
              bottom: 78,
              left: 210,
              scale: 0.5,
              transformOrigin: "bottom left",
            }}
          />
        </Sequence>
        <Sequence
          name="closing subtitles"
          from={v2.scenes[9].startFrame}
          durationInFrames={v2.scenes[9].durationInFrames}
        >
          <BasicCaptions
            captions={[...captions.closing]}
            width={3000}
            style={{
              position: "absolute",
              bottom: 78,
              left: 210,
              scale: 0.5,
              transformOrigin: "bottom left",
            }}
          />
        </Sequence>
      </Track>
      {pending && v2.pending.some((id) => id === current.id) && (
        <div
          style={{
            position: "absolute",
            bottom: 78,
            left: 210,
            width: 1500,
            padding: "12px 20px",
            background: "#1a2637ef",
            border: `1px solid ${C.gold}66`,
            borderRadius: 8,
            textAlign: "center",
          }}
        >
          <div
            style={{
              fontSize: 18,
              color: C.gold,
              letterSpacing: 2,
            }}
          >
            NEW VOICE TAKE PENDING · {current.id.toUpperCase()}
          </div>
        </div>
      )}
      <div
        style={{
          position: "absolute",
          top: 36,
          left: 96,
          right: 96,
          height: 56,
          display: "flex",
          alignItems: "center",
          gap: 15,
        }}
      >
        <Mark />
        <span style={{ fontFamily: display, fontSize: 38 }}>MATCHLENS</span>
        <span
          style={{
            marginLeft: 18,
            fontSize: 16,
            color: C.muted,
            letterSpacing: 2,
          }}
        >
          INSIDE THE GAME
        </span>
        <span style={{ marginLeft: "auto", fontSize: 16, color: C.gold }}>
          {String(v2.scenes.indexOf(current) + 1).padStart(2, "0")}{" "}
          <span style={{ color: C.muted, marginLeft: 16 }}>
            {current.title}
          </span>
        </span>
      </div>
      <div
        style={{
          position: "absolute",
          left: 96,
          bottom: 40,
          fontSize: 15,
          color: C.muted,
          letterSpacing: 1.4,
        }}
      >
        SYNTHETIC MATCH · FICTIONAL TEAMS & PLAYERS
      </div>
      <div
        style={{
          position: "absolute",
          right: 96,
          bottom: 40,
          fontSize: 15,
          color: pending || showGuide ? C.gold : C.muted,
          letterSpacing: 1.4,
        }}
      >
        {pending
          ? `REVIEW CUT · ${v2.pending.length} VOICE TAKES PENDING`
          : showGuide
            ? "REVIEW CUT"
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
            width: `${(frame / (v2.frames - 1)) * 100}%`,
            background: C.gold,
          }}
        />
      </div>
    </AbsoluteFill>
  );
};
