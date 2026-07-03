import { describe, expect, it } from "vitest";
import {
  buildAuthorizeUrl,
  buildCodeExchangeBody,
  buildRefreshBody,
  normalizeTokenResponse,
  parseRedirectUrl,
} from "../src/auth.js";

describe("auth (pure helpers)", () => {
  it("builds a correct /authorize URL with PKCE params", () => {
    const url = buildAuthorizeUrl({
      tenantId: "tid",
      clientId: "cid",
      redirectUri: "https://abc.chromiumapp.org/",
      scopes: "api://x/MCP.Access",
      challenge: "chal",
      state: "st",
    });
    const parsed = new URL(url);
    expect(parsed.origin + parsed.pathname).toBe(
      "https://login.microsoftonline.com/tid/oauth2/v2.0/authorize",
    );
    expect(parsed.searchParams.get("client_id")).toBe("cid");
    expect(parsed.searchParams.get("response_type")).toBe("code");
    expect(parsed.searchParams.get("code_challenge")).toBe("chal");
    expect(parsed.searchParams.get("code_challenge_method")).toBe("S256");
    expect(parsed.searchParams.get("state")).toBe("st");
    expect(parsed.searchParams.get("scope")).toContain("offline_access");
    expect(parsed.searchParams.get("scope")).toContain("api://x/MCP.Access");
  });

  it("parses a successful redirect URL", () => {
    const result = parseRedirectUrl("https://abc.chromiumapp.org/?code=AUTH&state=st");
    expect(result.code).toBe("AUTH");
    expect(result.state).toBe("st");
    expect(result.error).toBeUndefined();
  });

  it("parses an error redirect URL", () => {
    const result = parseRedirectUrl(
      "https://abc.chromiumapp.org/?error=access_denied&error_description=nope",
    );
    expect(result.error).toBe("access_denied");
    expect(result.error_description).toBe("nope");
    expect(result.code).toBeUndefined();
  });

  it("builds a code-exchange body with the verifier", () => {
    const body = buildCodeExchangeBody({
      clientId: "cid",
      code: "AUTH",
      redirectUri: "https://abc.chromiumapp.org/",
      verifier: "ver",
      scopes: "api://x/MCP.Access",
    });
    expect(body.get("grant_type")).toBe("authorization_code");
    expect(body.get("code")).toBe("AUTH");
    expect(body.get("code_verifier")).toBe("ver");
    expect(body.get("client_id")).toBe("cid");
  });

  it("builds a refresh body", () => {
    const body = buildRefreshBody({ clientId: "cid", refreshToken: "rt", scopes: "s" });
    expect(body.get("grant_type")).toBe("refresh_token");
    expect(body.get("refresh_token")).toBe("rt");
  });

  it("normalizes a token response with an early-refresh expiry", () => {
    const now = 1_000_000;
    const tokens = normalizeTokenResponse(
      { access_token: "at", refresh_token: "rt", expires_in: 3600, scope: "s" },
      now,
    );
    expect(tokens.accessToken).toBe("at");
    expect(tokens.refreshToken).toBe("rt");
    // 3600s minus the 60s safety margin.
    expect(tokens.expiresAt).toBe(now + 3600_000 - 60_000);
  });

  it("defaults expires_in when missing", () => {
    const tokens = normalizeTokenResponse({ access_token: "at" }, 0);
    expect(tokens.expiresAt).toBe(3600_000 - 60_000);
  });
});
