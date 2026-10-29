/** Plain words for what tools do, shared by the chat and the built-in views. */

export type Said = { label: string; detail?: string };

/** What a tool call means, in plain words, for people who never need to see tool names. */
export function describeTool(name: string, args: unknown): Said {
  const m = /^mcp__([a-z0-9-]+)__(.+)$/i.exec(name) ?? /^mcp_([a-z0-9-]+)_(.+)$/i.exec(name);
  const server = m ? m[1]!.toLowerCase() : "";
  const tool = (m ? m[2]! : name).toLowerCase();
  const a = (args && typeof args === "object" ? args : {}) as Record<string, unknown>;
  const str = (k: string) => (typeof a[k] === "string" && a[k] ? String(a[k]) : undefined);
  const where = (k = "path") => str(k)?.replace(/^\/+/, "");
  const key = `${server}.${tool}`;
  const words: Record<string, Said> = {
    "files.list": { label: "Looking in", detail: where() ?? "your files" },
    "files.browse": { label: "Looking in", detail: where() ?? "your files" },
    "files.read": { label: "Reading", detail: where() },
    "files.write": { label: "Saving", detail: where() },
    "files.search": { label: "Searching your files", detail: str("query") ?? str("content") },
    "files.move": { label: "Moving", detail: where("source") },
    "files.trash": { label: "Moving to the trash", detail: where() },
    "files.undo": { label: "Undoing the last change" },
    "files.versions": { label: "Looking at earlier versions of", detail: where() },
    "files.read_version": { label: "Reading an earlier version of", detail: where() },
    "files.restore": { label: "Bringing back an earlier version of", detail: where() },
    "files.read_image": { label: "Opening a picture", detail: where() },
    "shell.status": { label: "Checking how the computer is doing" },
    "shell.memory_read": { label: "Checking what I remember" },
    "shell.memory_write": { label: "Updating what I remember" },
    "shell.memory_export": { label: "Exporting what I remember" },
    "shell.open_accounts": { label: "Opening Accounts" },
    // Services through their bridges.
    "kiwi.search_flight": { label: "Looking for flights" },
    "todoist.account_status": { label: "Checking Todoist" },
    "todoist.find_tasks": { label: "Looking at your tasks" },
    "todoist.find_tasks_by_date": { label: "Looking at your tasks" },
    "todoist.find_completed_tasks": { label: "Looking at what you've done" },
    "todoist.find_projects": { label: "Looking at your projects" },
    "todoist.find_reminders": { label: "Looking at your reminders" },
    "todoist.get_overview": { label: "Looking at your tasks" },
    "todoist.add_tasks": { label: "Adding to your tasks" },
    "todoist.update_tasks": { label: "Changing a task" },
    "todoist.reschedule_tasks": { label: "Moving a task" },
    "todoist.complete_tasks": { label: "Ticking off a task" },
    "todoist.uncomplete_tasks": { label: "Putting a task back" },
    "todoist.add_reminders": { label: "Setting a reminder" },
    "shell.settings_get": { label: "Opening your settings" },
    "shell.settings_set": { label: "Changing a setting", detail: str("key") },
    "shell.theme_list": { label: "Opening Themes" },
    "shell.theme_get": { label: "Looking at a theme", detail: str("theme_id") },
    "shell.theme_use": { label: "Changing the theme", detail: str("theme_id") },
    "shell.theme_create": { label: "Making a theme", detail: str("name") },
    "shell.theme_delete": { label: "Deleting a theme", detail: str("theme_id") },
    "shell.audit_query": { label: "Looking at what I've done" },
    "shell.privacy_today": { label: "Looking at what left your home today" },
    "shell.model_key_save": { label: "Saving your key" },
    "shell.model_key_clear": { label: "Removing your key" },
    "system.status": { label: "Checking for changes to your computer" },
    "system.propose": { label: "Preparing a change to your computer", detail: str("title") },
    "system.build": { label: "Getting the change ready" },
    "system.diff": { label: "Looking at the change" },
    "system.apply": { label: "Applying the change" },
    "system.reject": { label: "Setting the change aside" },
    "system.rollback": { label: "Going back to an earlier version of your computer" },
    "system.disk_status": { label: "Checking your disk lock" },
    "system.disk_passphrase": { label: "Opening the disk key prompt" },
    "system.recovery_password": { label: "Opening the recovery key prompt" },
    "system.howto": { label: "Reading how to do this" },
    "web.search": { label: "Searching the web for", detail: str("query") },
    "web.fetch": { label: "Reading a web page", detail: hostOf(str("url")) },
    "email.search": { label: "Looking through your mail", detail: str("query") },
    "email.read": { label: "Reading an email" },
    "email.draft": { label: "Writing an email draft" },
    "email.send": { label: "Sending an email", detail: str("to") },
    "calendar.list_events": { label: "Looking at your calendar" },
    "calendar.create_event": { label: "Adding to your calendar", detail: str("title") },
    "calendar.update_event": { label: "Changing a calendar event" },
    "calendar.delete_event": { label: "Removing a calendar event" },
    "calendar.respond_invite": { label: "Answering an invitation" },
    "workshop.save": { label: "Making a card", detail: str("title") },
    "workshop.show": { label: "Opening a card", detail: str("name") },
    "workshop.list": { label: "Looking at your cards" },
    "workshop.read": { label: "Looking at a card", detail: str("name") },
    "workshop.remove": { label: "Putting a card away", detail: str("name") },
    "sysmon.get_system_info": { label: "Checking the computer's health" },
    "sysmon.poll_system_stats": { label: "Checking how busy the computer is" },
    ".memory": { label: "Updating what I remember" },
    ".session_search": { label: "Looking back at our conversations" },
    ".skill_view": { label: "Reading one of my skills" },
    ".skills_list": { label: "Looking at my skills" },
    ".skill_manage": { label: "Learning a new skill" },
    ".todo": { label: "Keeping a to-do list" },
    ".clarify": { label: "Asking you something" },
  };
  if (/^(list_resources|read_resource|list_prompts|get_prompt)$/.test(tool)) return { label: "Getting ready" };
  return words[key] ?? { label: server ? `Using ${server}` : "Working" };
}

function hostOf(url: string | undefined): string | undefined {
  try {
    return url ? new URL(url).host : undefined;
  } catch {
    return url;
  }
}
