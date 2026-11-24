# dealgrep + Claude Advisor Skill

This repository holds two things:

1. **[dealgrep](#dealgrep)** — a deals website (`site/`) refreshed every hour by a research agent (`agent/`) that runs in GitHub Actions and deploys to GitHub Pages.
2. **[Claude Advisor Skill](#claude-advisor-skill)** — an Agent Skill that makes Claude act like a blunt advisor (`skills/advisor`).

---

## dealgrep

**Hourly deal radar.** An automated agent pulls hot deals, discounts, coupon codes and Black Friday offers from public feeds (Slickdeals, deal subreddits, the Best Buy API) and, with an API key, asks Claude to research live deals at Amazon, Best Buy, Target, Walmart and more via web search. Everything is normalised into JSON that a static, terminal-styled site renders: browse by category, search an item, compare the lowest price per store, copy coupon codes.

- Site: `https://ihemu45.github.io/claude-advisor-skill/` once GitHub Pages is enabled (see setup).
- Pages: home, deals browser, stores, docs, changelog, about.
- Agent: `python -m agent` (Python 3.12, `requests` + `anthropic`), 55 offline tests.
- Workflow: `.github/workflows/refresh-deals.yml` runs hourly, on manual dispatch, and on pushes to `main`.

### Setup (one time)

1. Merge to `main`; scheduled workflows only run from the default branch.
2. Settings → Pages → Build and deployment → Source: **GitHub Actions** (the workflow also tries to enable this itself).
3. Settings → Secrets and variables → Actions → add `ANTHROPIC_API_KEY` (optional, enables Claude research) and `BESTBUY_API_KEY` (optional, enables the Best Buy API).
4. Actions → *refresh deals and deploy* → **Run workflow** to replace the sample data. The cron takes over after that.

Research cost is the one thing to watch: three Claude requests per hour on `claude-opus-5-5` is roughly $15–25/day. Tune with repository variables `RESEARCH_PER_RUN`, `RESEARCH_HOT`, `RESEARCH_MAX_SEARCHES`, `RESEARCH_MODEL`; leave the key unset to run feeds only for free. Full details in [`site/docs/`](site/docs/index.html).

### Run locally

```bash
pip install -r agent/requirements.txt
python -m unittest discover -s agent/tests -t .
python -m agent --sample --out site/data      # offline fixtures, marks output as sample
python -m http.server -d site 8000            # http://localhost:8000/
```

### Layout

```
agent/            hourly research agent (config, categorize, sources, research, pipeline, __main__)
agent/sources.json feed registry: add any RSS/Atom/Reddit feed here
agent/tests/      unittest suite + synthetic fixtures (also seed the sample data)
site/             static site served by GitHub Pages; site/data/*.json is rewritten each run
.github/workflows/refresh-deals.yml
```

---

## Claude Advisor Skill

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
