/** When the chat offers the Accounts card, from what a tool said. */

/** The server and tool of an MCP tool name (Hermes's `mcp__<server>__<tool>`, or the older `mcp_<server>_<tool>`). */
/** The server and tool of any MCP tool name (Foyelle's server names have no underscores). */
export function splitTool(name: string): { server: string; tool: string } | null {
  const m = /^mcp_{1,2}([a-z0-9-]+)_{1,2}(.+)$/i.exec(name);
  return m ? { server: m[1]!.toLowerCase(), tool: m[2]! } : null;
}

/** Whether a tool, by its name, only looks (finds, reads, lists) rather than changes something. */
export function onlyLooks(tool: string): boolean {
  return /^(find|get|list|search|read|fetch|browse|poll|query|show|check|look)(_|$)|(^|_)(status|get|query|today|info|overview|versions?)$/.test(tool);
}

export function mcpTool(name: string, servers: string[]): { server: string; tool: string } | null {
  for (const server of servers) {
    const m = new RegExp(`^mcp_{1,2}${server.replace(/[^a-z0-9_-]/gi, "")}_{1,2}(.+)$`, "i").exec(name);
    if (m) return { server, tool: m[1]! };
  }
  return null;
}

/**
 * A tool that found no account, or the agent checking one: which account to offer, if any.
 * Mail and calendar are offered together; a service (through its bridge) by its name.
 */
export function needsAccount(tool: string, summary: unknown, isError: unknown, services: Record<string, string> = {}): string | null {
  const t = mcpTool(tool, ["email", "calendar", ...Object.keys(services)]);
  if (!t) return null;
  const notConnected = typeof summary === "string" && /isn't connected|"connected":\s*false/i.test(summary);
  if (t.server === "email" || t.server === "calendar") {
    return t.tool === "account_status" || (!!isError && notConnected) ? "mail" : null;
  }
  return notConnected ? services[t.server]! : null;
}
