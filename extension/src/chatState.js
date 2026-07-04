export const CHAT_STATES = {
  IDLE: "idle",
  SENDING: "sending",
  STREAMING: "streaming",
  COMPLETE: "complete",
  ERROR: "error",
};

const ALLOWED = {
  [CHAT_STATES.IDLE]: new Set([CHAT_STATES.SENDING]),
  [CHAT_STATES.SENDING]: new Set([CHAT_STATES.STREAMING, CHAT_STATES.COMPLETE, CHAT_STATES.ERROR]),
  [CHAT_STATES.STREAMING]: new Set([CHAT_STATES.COMPLETE, CHAT_STATES.ERROR]),
  [CHAT_STATES.COMPLETE]: new Set([CHAT_STATES.IDLE, CHAT_STATES.SENDING]),
  [CHAT_STATES.ERROR]: new Set([CHAT_STATES.IDLE, CHAT_STATES.SENDING]),
};

export class ChatStateMachine {
  constructor(initial = CHAT_STATES.IDLE) {
    this.state = initial;
  }

  transition(next) {
    if (this.state === next) return this.state;
    if (!ALLOWED[this.state]?.has(next)) {
      throw new Error(`Invalid chat state transition: ${this.state} -> ${next}`);
    }
    this.state = next;
    return this.state;
  }
}
