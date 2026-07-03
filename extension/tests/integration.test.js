/**
 * Optional end-to-end smoke test for the MCP client against a real, running
 * HTTP-mode Buildium MCP server (Part A) + mock API.
 *
 * This test is SKIPPED unless `MCP_TEST_URL` is set, so it never blocks CI (which
 * has no live server). To run it locally against the docker-compose stack:
 *
 *   docker compose up --build mockapi mcp-server-http    # in the repo root
 *   MCP_TEST_URL=http://localhost:8000/mcp \
 *   MCP_TEST_TOKEN=dev-token npm test -- integration
 *
 * Configure the server with BUILDIUM_MCP_AUTH_TOKEN=dev-token (static-token dev
 * path) so no real Entra tenant is required.
 */

import { describe, expect, it } from "vitest";
import { McpClient } from "../src/mcpClient.js";

const url = process.env.MCP_TEST_URL;
const token = process.env.MCP_TEST_TOKEN || "";

describe.skipIf(!url)("MCP integration (live server)", () => {
  it("lists tools and calls health_check", async () => {
    const client = new McpClient(url, async () => token);
    const tools = await client.listTools();
    expect(Array.isArray(tools)).toBe(true);
    expect(tools.some((t) => t.name === "health_check")).toBe(true);

    const result = await client.callTool("health_check", {});
    const structured = result.structuredContent;
    expect(structured?.data?.status ?? structured?.status).toBeDefined();
  });
});
