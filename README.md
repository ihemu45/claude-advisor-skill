# Claude Skills

Shareable [Agent Skills](https://docs.claude.com/en/docs/agents-and-tools/agent-skills/overview) that work in Claude Code, claude.ai, and the Claude API.

## Skills

| Skill | What it does |
|-------|--------------|
| [`advisor`](skills/advisor/SKILL.md) | Turns Claude into a blunt advisor: challenges your assumptions, tags every claim `[Certain]` / `[Likely]` / `[Guessing]`, leads with the uncomfortable truth, and doesn't fold under pushback. Triggers on requests for honest feedback, gut checks, stress-testing a plan, or `/advisor`. |

## Install

### Claude Code

Personal (all your projects):

```bash
git clone https://github.com/ihemu45/CLAUDE-CLOUD-SESSIONS.git
mkdir -p ~/.claude/skills
cp -r CLAUDE-CLOUD-SESSIONS/skills/advisor ~/.claude/skills/
```

Per project (shared with your team via git): copy `skills/advisor` into `.claude/skills/` in the repo.

Then ask for honest feedback on something, or type `/advisor`.

### claude.ai / Claude Desktop

1. Download [`dist/advisor.zip`](dist/advisor.zip).
2. Go to **Settings → Capabilities → Skills** and upload the zip.
3. Make sure the skill is toggled on.

### Claude API

Upload the `skills/advisor` folder with the Skills API and reference it in the `container` of your Messages request. See the [Skills API docs](https://docs.claude.com/en/docs/agents-and-tools/agent-skills/overview).

## Contributing a skill

1. Add `skills/<name>/SKILL.md` with `name` and `description` frontmatter. The description decides when Claude loads the skill, so name concrete triggers.
2. Run `./scripts/package.sh` to rebuild `dist/<name>.zip`.
3. Add a row to the table above.
