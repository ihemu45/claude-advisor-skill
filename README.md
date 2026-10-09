# Claude Advisor Skill

**Claude agrees with your bad ideas too easily.** `advisor` is a free [Agent Skill](https://docs.claude.com/en/docs/agents-and-tools/agent-skills/overview) that makes Claude act like a blunt advisor instead of an assistant. It works in Claude Code, claude.ai, and the Claude API.

## Example

Real output from a Claude Code test run, excerpted and lightly condensed:

> **You:** Gut check: I'm quitting my job next month to build an app for dog walkers. My friends all say it's a great idea.
>
> **Claude (with `advisor`):** Have any dog walkers paid you, or committed to paying, for this? Friends saying it's a great idea is the weakest evidence you could have. They aren't your customers, and they bear none of the cost of being wrong [Certain].
>
> …
>
> I disagree with quitting next month. I'd do this instead: spend the next 4–8 weeks, while you still have your paycheck, talking to 20 dog walkers and trying to pre-sell a simple version to 3–5 of them. The risk in your plan is that you spend 6–12 months of savings and then find out the enthusiasm was politeness [Likely].

## What it does

- Opens by challenging your assumption, with no warm-up
- Tags each claim **[Certain]**, **[Likely]** or **[Guessing]**, so you can see what's backed up and what's filler
- Disagrees in a fixed format: reason, alternative, specific risk
- Doesn't fold when you push back unless you bring new information
- Bans phrases like "Great question" and "You're absolutely right"

It loads automatically when you ask for a gut check, honest feedback or a stress test, or you can type `/advisor`. It stays out of normal questions.

## Skills

| Skill | What it does |
|-------|--------------|
| [`advisor`](skills/advisor/SKILL.md) | Turns Claude into a blunt advisor: challenges your assumptions, tags every claim `[Certain]` / `[Likely]` / `[Guessing]`, leads with the uncomfortable truth, and doesn't fold under pushback. Triggers on requests for honest feedback, gut checks, stress-testing a plan, or `/advisor`. |

## Install

### Claude Code

Personal (all your projects):

```bash
git clone https://github.com/ihemu45/claude-advisor-skill.git
mkdir -p ~/.claude/skills
cp -r claude-advisor-skill/skills/advisor ~/.claude/skills/
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
