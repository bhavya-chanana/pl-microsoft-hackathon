import { Composition } from "remotion";
import { MatchlensSubmission } from "./Submission";
import { v2 } from "./v2-timeline";
export const RemotionRoot = () => (
  <Composition
    id="Matchlens-Submission"
    component={MatchlensSubmission}
    durationInFrames={v2.frames}
    fps={30}
    width={1920}
    height={1080}
    defaultProps={{
      demoUrl:
        "https://ca-frontend-7npbct7a7fan6.grayfield-727b5fb5.centralindia.azurecontainerapps.io",
      showGuide: false,
    }}
  />
);
