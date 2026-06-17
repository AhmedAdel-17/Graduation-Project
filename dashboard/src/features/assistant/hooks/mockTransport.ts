// Mock transport for the Portfolio Assistant chat — replays fixture-backed
// ServerEvent sequences so the UI runs end-to-end with zero backend (roadmap P6
// "build against fixtures + a mock WS first"). Keyword-routed, not an LLM; just
// enough to drive a scripted demo conversation.

import type {
  AssistantMessageEvent,
  ExtractionEvent,
  PolicyUpdateEvent,
  ServerEvent,
} from "../../../services/api/portfolioTypes";
import proposalFixture from "../fixtures/sample_proposal_blocks.json";
import scenarioFixture from "../fixtures/sample_scenario_compare.json";
import extractionFixture from "../fixtures/sample_extraction.json";
import policyFixture from "../fixtures/sample_policy.json";

const extraction = extractionFixture as ExtractionEvent;
const proposal = proposalFixture as AssistantMessageEvent;
const scenario = scenarioFixture as AssistantMessageEvent;
const policy = policyFixture as PolicyUpdateEvent;

function has(text: string, en: RegExp, ar: string[]): boolean {
  return en.test(text.toLowerCase()) || ar.some((t) => text.includes(t));
}

/** Map a user message to a scripted server-event sequence (status + body + done). */
export function mockTurn(text: string): ServerEvent[] {
  const events: ServerEvent[] = [];

  if (has(text, /\b(what if|suppose|sell all|sell my|add cash|scenario)\b/, ["لو", "سيناريو", "بعت", "أبيع", "ضيف"])) {
    events.push({ type: "status", stage: "optimizing", detail: "Forking a scenario…" });
    events.push({ type: "scenario_created", scenario: { scenario_id: 7, label: scenario.text ?? "Scenario", status: "active" } });
    events.push(scenario);
  } else if (has(text, /\b(optimi|rebalanc|allocat|what should i (buy|hold))/, ["وزع", "اعادة توازن", "حسّن", "حسن", "اقترح"])) {
    events.push({ type: "status", stage: "signals", detail: "Resolving signals…" });
    events.push({ type: "status", stage: "optimizing", detail: "Solving the allocation…" });
    events.push(proposal);
  } else if (has(text, /\b(safe|safer|safest|risk|income|growth|horizon|objective|month|year)\b/, ["آمن", "امن", "مخاطرة", "دخل", "نمو", "شهور", "سنة", "هدف"])) {
    events.push(policy);
  } else if (has(text, /\b(adopt|accept|promote|let'?s do|go ahead)\b/, ["اعتمد", "وافق", "موافق", "خليه"])) {
    events.push({ type: "assistant_message", text: "Adopted the scenario as your new baseline (v2).", scenario_id: null, blocks: [] });
  } else if (has(text, /\b(own|hold|have|portfolio|shares?|stock|cash|egp|%)\b/, ["عندي", "محفظ", "سهم", "كاش", "جنيه", "أملك", "املك"])) {
    events.push({ type: "status", stage: "extracting", detail: "Reading your portfolio…" });
    events.push(extraction);
    events.push({ type: "clarification", question: "I couldn't match “بنك مصر” to a listed ticker — which company did you mean?", missing: ["ticker:بنك مصر"] });
  } else {
    events.push({
      type: "assistant_message",
      text: "I'm a decision-support copilot for your EGX portfolio — tell me what you hold, set an objective, or ask for a rebalancing proposal.",
      scenario_id: null,
      blocks: [],
    });
  }

  events.push({ type: "done", proposal_id: null });
  return events;
}
