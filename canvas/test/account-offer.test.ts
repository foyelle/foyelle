import { describe, expect, it } from "vitest";
import { mcpTool, needsAccount, onlyLooks, splitTool } from "../src/renderer/account-offer";

const services = { todoist: "Todoist" };

describe("offering the Accounts card", () => {
  it("offers mail and calendar when the agent checks them or finds no account", () => {
    expect(needsAccount("mcp__email__account_status", "{}", false, services)).toBe("mail");
    expect(needsAccount("mcp__calendar__list_events", "Calendar isn't connected.", true, services)).toBe("mail");
    expect(needsAccount("mcp__email__search", "3 messages", false, services)).toBeNull();
  });

  it("offers a service by its name when it isn't connected", () => {
    expect(needsAccount("mcp__todoist__find_tasks", "Todoist isn't connected. It can be connected on the Accounts card.", true, services)).toBe("Todoist");
    expect(needsAccount("mcp__todoist__account_status", '{"service": "Todoist", "connected": false}', false, services)).toBe("Todoist");
    expect(needsAccount("mcp__todoist__account_status", '{"connected": true}', false, services)).toBeNull();
    expect(needsAccount("mcp__kiwi__search_flight", "isn't connected", true, services)).toBeNull(); // not a service with an account
  });

  it("reads Hermes's tool names, old and new", () => {
    expect(mcpTool("mcp__shell__open_accounts", ["shell"])).toEqual({ server: "shell", tool: "open_accounts" });
    expect(mcpTool("mcp_shell_open_accounts", ["shell"])).toEqual({ server: "shell", tool: "open_accounts" });
    expect(mcpTool("mcp__shellfish__x", ["shell"])).toBeNull();
  });
});

describe("which tool calls change a card's data", () => {
  it("reads the server from the tool name", () => {
    expect(splitTool("mcp__todoist__add_tasks")).toEqual({ server: "todoist", tool: "add_tasks" });
    expect(splitTool("mcp_calendar_create_event")).toEqual({ server: "calendar", tool: "create_event" });
    expect(splitTool("terminal")).toBeNull();
  });

  it("refreshes after changes, not after looking", () => {
    for (const t of ["add_tasks", "complete_tasks", "update_tasks", "reschedule_tasks", "create_event", "delete_event", "write", "move", "trash"]) {
      expect(onlyLooks(t), t).toBe(false);
    }
    for (const t of ["find_tasks", "find_tasks_by_date", "get_overview", "list_events", "get_event", "search", "read", "account_status",
      "settings_get", "audit_query", "privacy_today", "browse", "versions", "search_flight", "get_system_info", "poll_system_stats"]) {
      expect(onlyLooks(t), t).toBe(true);
    }
  });
});
