import { describe, expect, it } from "vitest";
import {
  base64UrlEncode,
  challengeFromVerifier,
  createPkcePair,
  generateCodeVerifier,
  generateState,
} from "../src/pkce.js";

describe("pkce", () => {
  it("base64UrlEncode produces URL-safe output without padding", () => {
    const bytes = new Uint8Array([251, 255, 191, 0, 1, 2]);
    const encoded = base64UrlEncode(bytes);
    expect(encoded).not.toMatch(/[+/=]/);
  });

  it("generates a verifier of adequate length and charset", () => {
    const verifier = generateCodeVerifier();
    expect(verifier.length).toBeGreaterThanOrEqual(43);
    expect(verifier).toMatch(/^[A-Za-z0-9_-]+$/);
  });

  it("generates unique verifiers", () => {
    expect(generateCodeVerifier()).not.toEqual(generateCodeVerifier());
  });

  it("derives a deterministic S256 challenge from a known verifier", async () => {
    // RFC 7636 Appendix B test vector.
    const verifier = "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk";
    const challenge = await challengeFromVerifier(verifier);
    expect(challenge).toBe("E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM");
  });

  it("createPkcePair returns matching verifier/challenge and a state", async () => {
    const { verifier, challenge, state } = await createPkcePair();
    expect(await challengeFromVerifier(verifier)).toBe(challenge);
    expect(state.length).toBeGreaterThan(0);
  });

  it("state values are random", () => {
    expect(generateState()).not.toEqual(generateState());
  });
});
