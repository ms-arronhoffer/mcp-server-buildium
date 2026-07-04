import { describe, expect, it } from "vitest";
import { ChatStateMachine, CHAT_STATES } from "../src/chatState.js";

describe("ChatStateMachine", () => {
  it("accepts valid transitions", () => {
    const machine = new ChatStateMachine();
    machine.transition(CHAT_STATES.SENDING);
    machine.transition(CHAT_STATES.STREAMING);
    machine.transition(CHAT_STATES.COMPLETE);
    machine.transition(CHAT_STATES.IDLE);
    expect(machine.state).toBe(CHAT_STATES.IDLE);
  });

  it("rejects invalid transitions", () => {
    const machine = new ChatStateMachine();
    expect(() => machine.transition(CHAT_STATES.COMPLETE)).toThrow(/Invalid chat state transition/);
  });
});
