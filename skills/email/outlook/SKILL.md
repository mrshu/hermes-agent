---
name: outlook
description: "Use when reading, searching, sending, drafting, replying to, or organizing Microsoft Outlook / Office 365 mail from Hermes via the `outlook` CLI. Prefer JSON output for retrieval, drafts before sends for risky messages, and explicit account selection when multiple profiles exist."
version: 1.0.0
author: Hermes Agent
license: MIT
platforms: [macos, linux]
metadata:
  hermes:
    tags: [Email, Outlook, Office365, Microsoft365, CLI, Calendar]
    related_skills: [himalaya]
prerequisites:
  commands: [outlook]
---

# Outlook 365 CLI Email

## Overview

The `outlook` binary is a terminal client for Outlook / Microsoft 365 mail, calendar, contacts, and message management. It is the preferred path when the user asks Hermes to interact with their Outlook mailbox from the terminal.

Use the CLI directly through the terminal tool. It supports JSON output for most read/list operations, non-interactive body input via `--body-file -`, account profiles, confirmations for destructive/send actions, and a global `--dry-run` mode for previewing changes.

## Prerequisites

1. Verify the binary exists:

```bash
command -v outlook
outlook --version
```

2. Verify authentication/profile state:

```bash
outlook account list --json
outlook whoami --json
```

3. If not logged in, authenticate:

```bash
outlook login
```

For named profiles:

```bash
outlook account add work
outlook account list --json
outlook account switch work
```

The CLI stores config and cache under:

- Config: `~/.config/outlook-cli/config.yaml`
- Account metadata: `~/.config/outlook-cli/accounts.json` and `~/.config/outlook-cli/accounts/`
- Cache/tokens/display-ID map: `~/.cache/outlook-cli/`

These can be overridden with `OUTLOOK_CLI_CONFIG` and `OUTLOOK_CLI_CACHE`.

## Safety Rules for Hermes

- Prefer `--json` for list/read/search/calendar/contact operations so output is parseable.
- Do not use `-y` for sends, deletes, or calendar changes unless the user explicitly requested the action and the recipient/subject/body/scope are clear.
- For uncertain outbound mail, create a draft first with `outlook draft ... --json` or `outlook reply-draft ... --json`, then ask/return the draft details.
- Use global `--dry-run` to preview sends or destructive changes when scope is ambiguous:

```bash
outlook --dry-run send alice@example.com "Subject" --body-file -
```

- Use global `--no-input` for automation when you want the command to fail instead of prompting.
- Message and event numbers are display numbers from recent list/search/calendar outputs; if stale or ambiguous, re-list first.
- `outlook read` automatically marks an unread message as read. Use `outlook thread` or `outlook search --json` first if you need to inspect without changing read state.
- Treat `delete`, `move`, `category-delete`, `category-clear`, `event-delete`, `schedule-cancel`, and `draft-send` as side-effecting operations.

## Account Selection

Most commands accept `--account TEXT`. If multiple profiles exist, either use the current profile shown by:

```bash
outlook account current --json
```

or pass the intended profile explicitly:

```bash
outlook --no-input inbox --json --account work
```

Account commands:

```bash
outlook account list --json
outlook account current --json
outlook account switch work
outlook account remove old-profile --yes
```

## Reading and Searching Mail

### Inbox dashboard

```bash
outlook summary --json
outlook inbox -n 20 --json
outlook inbox --unread -n 20 --json
```

### Filter inbox or folders

```bash
outlook inbox --from alice@example.com --json
outlook inbox --subject "contract" --json
outlook inbox --after 2026-05-01 --before 2026-05-20 --json
outlook inbox --has-attachments --json
outlook inbox --category "Action Required" --json
outlook inbox --no-category --json

outlook folders --json
outlook folder "Sent Items" -n 20 --json
```

### Search mailbox

```bash
outlook search "from:alice@example.com quarterly review" -n 20 --json
outlook search "subject:invoice" --json
```

### Read, inspect, and open messages

```bash
outlook read 7 --json
outlook read 7 --raw
outlook thread 7 --json
outlook open 7 --print-url
```

Use `thread` when replying or summarizing a conversation so you have context beyond a single message.

## Attachments

List attachments:

```bash
outlook attachments 7 --json
```

Download all attachments:

```bash
outlook attachments 7 --download --save-to /tmp/outlook-attachments
```

When attaching outbound files, verify the paths exist first. Files under 3 MB are inlined by the CLI; larger files use an upload session.

## Composing Mail

Prefer stdin for message bodies to avoid shell quoting issues. Use terminal heredocs or a temp file.

### Send a simple email

```bash
outlook send bob@example.com "Status update" --body-file - --json
```

Then provide the body on stdin. To bypass confirmation only after explicit user approval:

```bash
outlook send bob@example.com "Status update" --body-file - --json -y
```

### Multiple recipients, CC, HTML, signatures, attachments

```bash
outlook send "alice@example.com,bob@example.com" "Launch notes" \
  --cc manager@example.com \
  --body-file - \
  --html \
  --signature work \
  --attach /path/to/file.pdf \
  --json
```

Saved signatures:

```bash
outlook signature-list
outlook signature-show work
outlook signature-pull --name work
outlook signature-delete old --yes
```

## Draft-First Workflows

Use drafts when the user asks for help composing, when recipients are sensitive, or when the body needs review.

```bash
outlook draft jane@example.com "Follow-up" --body-file - --json
outlook reply-draft 12 --all --body-file - --json
```

Send an existing draft only after the user confirms:

```bash
outlook draft-send 4
outlook draft-send 4 -y
```

## Replying and Forwarding

Read the thread first, then reply. Use `--body-file -` for bodies and `--all` only when explicitly requested.

```bash
outlook thread 12 --json
outlook reply 12 --body-file -
outlook reply 12 --all --body-file -
outlook reply-draft 12 --body-file - --json
```

Forward with an optional comment:

```bash
outlook forward 12 carol@example.com --comment "FYI — see below."
```

## Scheduling Mail

Schedule new mail:

```bash
outlook schedule alice@example.com "Reminder" --body-file - "tomorrow 09:00" --json
outlook schedule alice@example.com "Reminder" --body-file - +2h30m --json
```

Manage scheduled mail:

```bash
outlook schedule-list --json
outlook schedule-cancel 2
outlook schedule-cancel 2 -y
```

Schedule an existing draft:

```bash
outlook schedule-draft 4 "2026-05-21T09:00" --json
```

## Organizing Mail

List folders and categories:

```bash
outlook folders --json
outlook categories --json
```

Move/copy/delete/mark messages:

```bash
outlook move 3 4 "Archive"
outlook copy 3 "Projects"
outlook mark-read 3 4
outlook mark-read 3 --unread
outlook delete 9
```

Flags and pins:

```bash
outlook flag 3
outlook flag 3 --due tomorrow
outlook flag 3 --complete
outlook flag 3 --clear
outlook pin 3
outlook pin 3 --unpin
```

Categories:

```bash
outlook categorize 3 4 "Action Required"
outlook uncategorize 3 "Action Required"
outlook category-create "Action Required" --color 4
outlook category-rename "Old" "New"
outlook category-clear "Old" --folder Inbox -n 100
outlook category-delete "Old"
```

## Calendar and People Adjacent Operations

Although this is primarily an email skill, Outlook mail tasks often need calendar and contact context.

```bash
outlook calendars --json
outlook calendar --days 7 --json
outlook calendar --days -7 --json
outlook calendar --days 7 --timezone Asia/Shanghai --json
outlook event 3 --json
outlook open 3 --print-url
```

Create/update/delete/respond to events:

```bash
outlook event-create "Project Sync" "tomorrow 09:00" "tomorrow 09:30" \
  --attendee alice@example.com \
  --location "Teams" \
  --teams \
  --json

outlook event-update 5 --start "tomorrow 10:00" --end "tomorrow 10:30" --json
outlook event-respond 8 accept --comment "Thanks"
outlook event-delete 5
```

Find availability and people:

```bash
outlook free-busy "alice@example.com,bob@example.com" tomorrow --duration 30 --json
outlook contacts -n 20 --json
outlook people-search "Alice" --json
```

## JSON Handling Patterns

Typical read commands emit an envelope like:

```json
{
  "ok": true,
  "schema_version": "1",
  "data": [...]
}
```

Use Python or `jq` only if needed to reduce large results before returning them to the user. If output is large, save with `-o` where available:

```bash
outlook inbox --json -o /tmp/outlook-inbox.json
outlook search "quarterly" --json -o /tmp/outlook-search.json
outlook calendar --days 30 --json -o /tmp/outlook-calendar.json
```

## One-Shot Recipes

### Summarize unread inbox

```bash
outlook inbox --unread -n 25 --json
```

Then group by sender, age, subject, category, and attachments. Avoid reading individual unread messages unless the user accepts that they may be marked read.

### Draft a reply for review

```bash
outlook thread 12 --json
outlook reply-draft 12 --body-file - --json
```

Return the draft number/subject/recipients and ask the user to approve before `draft-send`.

### Clean up a category safely

```bash
outlook inbox --category "Old Label" -n 100 --json
outlook --dry-run category-clear "Old Label" --folder Inbox -n 100
outlook category-clear "Old Label" --folder Inbox -n 100
```

### Download attachments from likely matching messages

```bash
outlook search "subject:invoice hasattachments:true" -n 10 --json
outlook attachments 6 --json
outlook attachments 6 --download --save-to /tmp/invoices
```

## Common Pitfalls

1. **Display numbers are not permanent IDs.** They come from the CLI's recent listing cache. Re-run the relevant `inbox`, `search`, `folder`, or `calendar` command before acting if the number could be stale.

2. **`read` marks unread messages as read.** For triage, use list/search JSON first; read only when the side effect is acceptable.

3. **Confirmations can hang non-interactive runs.** Use `--dry-run` for previews, `--no-input` to fail instead of prompting, or `-y` only after explicit approval.

4. **Shell quoting can mangle message bodies.** Prefer `--body-file -` or a temporary file over putting long bodies in positional arguments.

5. **Multiple accounts can silently use the wrong mailbox.** Check `outlook account current --json` or pass `--account` explicitly.

6. **Forward/reply/send are side effects.** When in doubt, create drafts rather than sending immediately.

7. **Large JSON can flood context.** Use `-n`, filters, `-o`, or a small Python reducer.

## Verification Checklist

- [ ] `outlook --version` succeeds.
- [ ] `outlook account list --json` shows the intended bound/current account.
- [ ] Read/list operations used `--json` when the result needed parsing.
- [ ] Message/event display numbers were refreshed before side-effecting operations.
- [ ] Outbound mail recipients, subject, body, attachments, account, and send-vs-draft choice were verified.
- [ ] Destructive operations were previewed or scoped, and confirmation behavior was intentional.
- [ ] For calendar-related mail tasks, timezone was explicit when ambiguity mattered.
