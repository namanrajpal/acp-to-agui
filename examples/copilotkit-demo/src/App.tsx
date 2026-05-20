/**
 * CopilotKit + ACP Agent Demo
 *
 * This shows how easy it is to get a full-featured AI chat UI
 * on top of any ACP agent using CopilotKit + our bridge.
 *
 * The bridge (running at localhost:8000) exposes a standard AG-UI
 * endpoint at POST /ag-ui that CopilotKit natively understands.
 *
 * Total frontend code: ~20 lines of actual logic.
 */

import { CopilotKit } from "@copilotkit/react-core";
import { CopilotChat } from "@copilotkit/react-ui";
import "@copilotkit/react-ui/styles.css";

export default function App() {
  return (
    <div style={{ height: "100vh", display: "flex", flexDirection: "column" }}>
      <header style={{ padding: "12px 24px", borderBottom: "1px solid #e5e7eb", background: "#f9fafb" }}>
        <h1 style={{ margin: 0, fontSize: "18px", fontWeight: 600 }}>
          ACP Agent → CopilotKit
        </h1>
        <p style={{ margin: "4px 0 0", fontSize: "13px", color: "#6b7280" }}>
          Any ACP coding agent gets a full CopilotKit UI via our AG-UI bridge. Zero custom code.
        </p>
      </header>

      <div style={{ flex: 1 }}>
        <CopilotKit
          runtimeUrl="http://localhost:8000/ag-ui"
          properties={{ cwd: "." }}
        >
          <CopilotChat
            labels={{
              title: "ACP Agent",
              initial: "Connected to your ACP agent via the AG-UI bridge. Ask anything!",
            }}
          />
        </CopilotKit>
      </div>
    </div>
  );
}
