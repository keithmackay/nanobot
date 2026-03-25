---
name: obsidian-tagging
description: "MANDATORY: Apply Obsidian frontmatter before creating or writing any note in KeithVault. ALWAYS invoke this skill when any personality adds or edits an Obsidian note — no exceptions. Reads Atlas/context.md for the living taxonomy. Required fields on every note: context, type, subtype, tags, interpreter (personality name, e.g. Mac, Archie), created ([[yyyy-mm-dd]] wikilink format). Also use when re-tagging an existing note or checking/fixing missing frontmatter."
---

# Obsidian Tagging

## Taxonomy Source

Before tagging any note, read the living taxonomy document:

```
/Users/keithmackay1/KeithVault/Atlas/context.md
```

This file is the canonical source for valid values of `context`, `type`, and `subtype`. It evolves over time — always read it fresh rather than relying on memory.

## Required Frontmatter Fields

Every note **must** have all of these:

```yaml
---
context:        # string or list — top-level domain (EY, ideas, personal, system)
type:           # string — note category within context
subtype:        # string or blank — refinement of type; leave empty string if none fits
tags:           # list — free-form kebab-case tags; minimum 1
created:        # string — "[[yyyy-mm-dd]]" format (wikilink, quoted for YAML safety)
---
```

When Mac (AI) creates or edits the note, also add:

```yaml
interpreter:    # string — personality name active in this session (e.g., "Mac", "Archie")
```

## Workflow

1. **Read taxonomy**: `cat "/Users/keithmackay1/KeithVault/Atlas/context.md"` — identify the correct context/type/subtype for the note's content.
2. **Determine interpreter**: Use the personality name the user is calling Mac in this session. When in doubt, use "Mac".
3. **Draft frontmatter**: Fill all required fields. Use blank string (`""` or leave value empty) for subtype when none applies, not `null`.
4. **Write/edit the note**: Prepend or replace the frontmatter block. For new notes, use `obsidian-cli create` or direct file write. For existing notes, edit the YAML block in place.
5. **Verify**: Confirm the note has all required fields before finishing.

## Field Rules

- **context**: Match exactly to taxonomy entries (EY, ideas, personal, system). Can be a list if a note spans multiple contexts.
- **type**: Use exact values from taxonomy tables.
- **subtype**: Use exact values. If the note is at the type level only, leave blank (not null).
- **tags**: Kebab-case array. Include descriptive terms useful for search. Always at least one tag.
- **created**: Always `"[[yyyy-mm-dd]]"` — the wikilink format with outer quotes for YAML validity.
- **interpreter**: Only include when Mac added/edited the note. Use the session personality name.

## Example Output

```yaml
---
context: personal
type: infra
subtype: power
interpreter: Archie
created: "[[2026-03-01]]"
tags:
  - solar
  - solaredge
  - monitoring
  - api
---
```

## Updating the Taxonomy

If the note's content doesn't fit any existing context/type/subtype combo cleanly:
1. Use the closest match and add a descriptive tag.
2. If a new category is clearly needed, propose the addition to the user.
3. If approved, update `Atlas/context.md` directly and add a note at the bottom: `_Updated: yyyy-mm-dd by {interpreter} — added {new entry}_`

## obsidian-cli Reference

```bash
# Create a new note
obsidian-cli create "Folder/Note Title" --content "..." 

# Edit an existing note (prefer direct file edit for frontmatter changes)
# Read vault path
obsidian-cli print-default --path-only

# Then edit the .md file directly at that path
```

For frontmatter edits on existing notes, direct file edit is more reliable than obsidian-cli create.
