import { describe, expect, it } from "vitest";
import { isHiddenByView } from "../src/sidepanel_ui.js";

describe("isHiddenByView", () => {
  it("never hides anything in the 'all' view", () => {
    expect(isHiddenByView("all", "chat")).toBe(false);
    expect(isHiddenByView("all", "alerts")).toBe(false);
    expect(isHiddenByView("all", "files")).toBe(false);
    expect(isHiddenByView("all", undefined)).toBe(false);
  });

  it("defaults an undefined view to 'all'", () => {
    expect(isHiddenByView(undefined, "chat")).toBe(false);
    expect(isHiddenByView("", "chat")).toBe(false);
  });

  it("hides chat messages while an alerts/files filter is active (the disappearing-chat bug)", () => {
    expect(isHiddenByView("alerts", "chat")).toBe(true);
    expect(isHiddenByView("files", "chat")).toBe(true);
    // Untagged messages default to "chat" and are therefore hidden too.
    expect(isHiddenByView("alerts", undefined)).toBe(true);
    expect(isHiddenByView("files", undefined)).toBe(true);
  });

  it("shows only the matching kind under a filtered view", () => {
    expect(isHiddenByView("alerts", "alerts")).toBe(false);
    expect(isHiddenByView("alerts", "files")).toBe(true);
    expect(isHiddenByView("files", "files")).toBe(false);
    expect(isHiddenByView("files", "alerts")).toBe(true);
  });
});
