import { afterEach, describe, expect, it, vi } from "vitest";
import { ManagementClient, buildManageUrl } from "../src/management.js";

const config = { mcpServerUrl: "https://host/mcp" };
const getToken = async () => "tok-123";

function jsonResponse(body, { ok = true, status = 200 } = {}) {
  return { ok, status, json: async () => body };
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe("buildManageUrl", () => {
  it("derives /manage/* endpoints from the MCP URL", () => {
    expect(buildManageUrl("https://host/mcp", "users")).toBe("https://host/manage/users");
    expect(buildManageUrl("https://host/mcp", "capabilities")).toBe(
      "https://host/manage/capabilities",
    );
    expect(buildManageUrl("https://host/mcp")).toBe("https://host/manage");
  });
});

describe("ManagementClient", () => {
  it("lists users with a bearer token", async () => {
    const fetchMock = vi.fn(async () => jsonResponse({ users: [{ user_id: "u1", role: "admin" }] }));
    vi.stubGlobal("fetch", fetchMock);

    const client = new ManagementClient(config, getToken);
    const users = await client.listUsers();

    expect(users).toEqual([{ user_id: "u1", role: "admin" }]);
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("https://host/manage/users");
    expect(init.headers.Authorization).toBe("Bearer " + "tok-123");
  });

  it("invites a user with email and role in the body", async () => {
    const fetchMock = vi.fn(async () => jsonResponse({ user: { id: "u9" } }, { status: 201 }));
    vi.stubGlobal("fetch", fetchMock);

    const client = new ManagementClient(config, getToken);
    const user = await client.inviteUser("guest@example.com", "operator");

    expect(user).toEqual({ id: "u9" });
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("https://host/manage/users");
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body)).toEqual({ email: "guest@example.com", role: "operator" });
  });

  it("PATCHes a user role at the encoded id path", async () => {
    const fetchMock = vi.fn(async () => jsonResponse({ user: { id: "u 1", role: "admin" } }));
    vi.stubGlobal("fetch", fetchMock);

    const client = new ManagementClient(config, getToken);
    await client.setUserRole("u 1", "admin");

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("https://host/manage/users/u%201/role");
    expect(init.method).toBe("PATCH");
    expect(JSON.parse(init.body)).toEqual({ role: "admin" });
  });

  it("throws a helpful error on a non-ok response", async () => {
    const fetchMock = vi.fn(async () => jsonResponse({ error: "forbidden" }, { ok: false, status: 403 }));
    vi.stubGlobal("fetch", fetchMock);

    const client = new ManagementClient(config, getToken);
    await expect(client.listUsers()).rejects.toThrow("forbidden");
  });

  it("downloads the extension as a blob for a browser", async () => {
    const blob = new Blob(["zip"]);
    const fetchMock = vi.fn(async () => ({ ok: true, status: 200, blob: async () => blob }));
    vi.stubGlobal("fetch", fetchMock);

    const client = new ManagementClient(config, getToken);
    const result = await client.downloadExtension("chrome");

    expect(result).toBe(blob);
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("https://host/manage/extension?browser=chrome");
    expect(init.headers.Authorization).toBe("Bearer " + "tok-123");
  });
});
