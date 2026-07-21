# Curated practice traits — provenance and evidence

The seven traits below `git-checkpoint` in the shipped set
([traits & permissions](traits-permissions.md)) are distilled from external prompt-engineering
guidance rather than from this project's own experience. This file records where each came from,
how strong the evidence actually is, and — just as importantly — **what was evaluated and
rejected**, so the set grows on evidence instead of accumulating folklore.

## The shape decision

The obvious design was an always-on prompt extension: one curated block appended to every
composed prompt, toggleable per routine. It was rejected in favour of ordinary **traits**, for
three reasons that all point the same way:

- **The prompt's scarce resource is attention, not tokens.** The composed prompt is ~21.5k chars
  and cached, so the marginal token cost of more prose is ~0.1x. The real cost is dilution:
  every added instruction competes with the harness contract for adherence. Anthropic's own
  guidance for recent models warns that prompts written for older ones "are often too
  prescriptive… and can degrade output quality"; Aider's field heuristic is that conventions get
  forgotten past ~150–200 lines; Cursor's community warning is that "every always-on rule eats
  tokens from every interaction, whether relevant or not."
- **Relevance is per-routine, not global.** `review-recall` earns its place in an audit routine
  and is noise in a scraper. A single global block has to be written for the intersection of all
  routines, which is where generic prompt boilerplate comes from.
- **Traits already are this mechanism**, and a better version of it: selected per routine,
  *adapted* to the task by the generator at creation, then owned and refined by the routine.
  A trait that is off contributes nothing at all — no truncation, no partial application.

So: no new mechanism, no always-on block, no automatic application. A trait is on when the
routine has it, off when it doesn't.

## Per-trait provenance

| trait | source | evidence strength |
|---|---|---|
| `evidence-discipline` | Anthropic "ground progress claims" | **Strongest in the survey.** Anthropic reports this wording "nearly eliminated fabricated status reports" in their own testing on tasks designed to elicit them. The binary verified/unverified framing (rather than a confidence score) comes from Xiong et al., [ICLR 2024](https://arxiv.org/abs/2306.13063): verbalized confidence is markedly overconfident (ECE > 0.377). |
| `independent-verification` | Anthropic verifier-subagent guidance + two papers | **Strong and convergent.** Huang et al., [*LLMs Cannot Self-Correct Reasoning Yet*](https://arxiv.org/abs/2310.01798) (ICLR 2024): with no external feedback GPT-3.5 fixed 7.6% of wrong answers while **breaking 8.8% of correct ones** — net negative. [Chain-of-Verification](https://arxiv.org/abs/2309.11495) (ACL Findings 2024) works only in its *factored* form, where verification doesn't attend to the original draft. Anthropic independently: fresh-context verifier subagents outperform self-critique. All three say the same thing, which is why this trait routes to `subtask` rather than asking for self-review. |
| `error-recovery` | [Reflexion](https://arxiv.org/abs/2303.11366) | **Real but heavily caveated.** 91% vs 80% pass@1 on HumanEval — but it depends on ground-truth tests, is capability-gated, *hurts* on MBPP (80.1% → 77.1%), and suffers "degeneration of thought" where flawed reasoning repeats. The trait therefore fires only on a **real external error signal** (nonzero exit, missing path, denied call), never as free-floating reflection. |
| `decision-commitment` | Anthropic "commit to an approach" + "act when you have enough" | Anecdotal, but the cheapest block in the set and the failure it targets is billed directly against `max_turns`. |
| `change-restraint` | Anthropic anti-over-engineering + anti-hardcoding | Anecdotal; named model tendencies. Overlaps this project's own no-backwards-compatibility rule, which is why the shim clause is worded strongly. |
| `review-recall` | Anthropic Opus 4.8 "coverage not filtering" | Anthropic reports measurable recall/precision shifts in internal evals. Relevant here because literal-minded models can read a "don't nitpick" instruction as licence to suppress real findings. |
| `teaching-insights` | Claude Code [`explanatory-output-style`](https://github.com/anthropics/claude-code/tree/main/plugins/explanatory-output-style) plugin | Design intent only, no effectiveness data. Kept because it is opt-in and conversation-shaped: the original licenses longer output for a human watching in real time, which is pure burn on a cron run and reasonable in a chat. The `★ Insight ───` divider format was dropped — it renders badly in `say` (inline markdown) and is decoration, not mechanism. |
| `interface-design` | Claude Code [`frontend-design`](https://github.com/anthropics/claude-code/tree/main/plugins/frontend-design) skill + the `<frontend_aesthetics>` snippet in [`claude-opus-4-5-migration`](https://github.com/anthropics/claude-code/tree/main/plugins/claude-opus-4-5-migration) | Anthropic ships this as a real skill and maintains a condensed prompt-snippet form of it, which is the closest thing to a field-tested version. The named default clusters (cream/serif/terracotta, near-black/acid accent, broadsheet hairline) are **time-sensitive calibration** — they describe where generated design converges today and should be re-checked rather than treated as permanent. |
| `interface-copy` | the "writing in design" section of the same `frontend-design` skill | No effectiveness data, but unusually concrete and it generalizes past UI to any product surface this system emits (notification text, report headings, error messages). Split from `interface-design` deliberately: a routine writing user-facing copy wants this without the visual guidance. |
| `test-design` | [`pr-review-toolkit/agents/pr-test-analyzer`](https://github.com/anthropics/claude-code/tree/main/plugins/pr-review-toolkit) | No effectiveness data, but it closes a hole **underneath** `independent-verification`: that trait says "prefer a mechanical check", and a test that asserts the implementation just written is a mechanical check that cannot fail. The hollow check then reports a pass and the verification trait amplifies it. "Watch it fail once" is executable in this engine (run it, observe a nonzero exit) rather than aspirational. |
| `failure-visibility` | [`pr-review-toolkit/agents/silent-failure-hunter`](https://github.com/anthropics/claude-code/tree/main/plugins/pr-review-toolkit) | No effectiveness data. Kept for one bullet that is unusually good because it is *checkable*: before writing a broad catch, enumerate which unrelated failures it will now absorb. Note the deliberate split from `error-recovery` — that trait is the agent reacting to its own failed observation, this one is the error handling the agent writes into code. The closing bullet is load-bearing: without it the trait pulls against `change-restraint` and produces defensively over-wrapped code. |

## Evaluated and rejected

| candidate | why not |
|---|---|
| "Double-check your work before finishing" | The intervention the literature says is **net-negative** without external feedback (Huang et al. above). Shipped as `independent-verification` instead. |
| "Don't be sycophantic" | UK AISI's [*Ask Don't Tell*](https://www.aisi.gov.uk/blog/ask-dont-tell-reducing-sycophancy-in-large-language-models-2) found the explicit instruction was the **least effective method tested**. Sycophancy is RLHF-general ([Sharma et al.](https://arxiv.org/pdf/2310.13548)) and wants a structural fix, not a sentence. |
| Numeric confidence per finding | ECE > 0.377 (Xiong et al.). Asks for a number that looks like evidence and isn't. |
| Parallel tool calls | Architecturally impossible: one JSON action per turn. `read_file`'s `paths` batching is the real analog and the harness contract already teaches it. |
| "Reflect on tool results before proceeding" | Verbatim duplicate of the `say` contract ("what the last observation taught you + why this action"). Don't pay for it twice. |
| Plan-then-act / ReAct framing | The workflow-is-the-harness loop *is* ReAct ([Yao et al.](https://arxiv.org/abs/2210.03629)). Re-litigates a decision the engine enforces. |
| "Persist regardless of remaining context" | Anthropic's wording assumes automatic compaction and **no hard ceiling**. This engine has real enforced `max_turns` / `total_tokens` / `wall_clock_min`; pasting it produces a run that ignores the 85% warning and gets killed mid-edit. The useful half — don't wrap up on *context* grounds, compaction is automatic — is already in the harness contract. |
| Explanatory/Learning insight blocks as a global | Pedagogy for a human watching live; explicitly licenses longer output. Kept only as the opt-in `teaching-insights`. |
| Markdown minimization, frontend aesthetics | Domain skills, large, and the renderers here *want* structure. |
| Anything mined from `awesome-cursorrules` | No benchmarks, no ablations, no negative results in either direction; content is overwhelmingly "you are an expert in X" boilerplate — the canonical unevidenced ritual. |
| The `code-reviewer` agents' confidence gating (`pr-review-toolkit`, `feature-dev`) | Their headline instruction is "only report issues with confidence ≥ 80", scored 0–100. That **directly contradicts `review-recall`** ("find first, filter second"; "uncertainty is a label, not grounds for omission") and re-introduces the numeric-confidence pattern already rejected above. A trait that fights another shipped trait is worse than no trait. Only one idea survived, as a single `review-recall` bullet: separate what the change introduced from what it inherited. |
| `type-design-analyzer` | Four 1–10 ratings — the numeric pattern again. The underlying content ("make illegal states unrepresentable", constructor validation, immutability) is textbook material the model already applies; a trait's job is countering a tendency, not restating knowledge. |
| `code-simplifier` | Its scope-control content is `change-restraint`; the rest is one project's TypeScript/React style guide. One line survived as a `change-restraint` clause: explicit code beats compact code. |
| `plugin-dev/.../system-prompt-design.md` | Guidance for *humans* authoring prompts, not practice for an agent to follow. Its length advice (1,000–5,000 words) would produce a trait 5–10× this format and contradicts the central finding that the scarce resource is attention. Its "Plan → Execute → Verify → Report" skeleton is the plan-then-act framing already rejected. One idea is worth keeping for elsewhere: requiring an explicit "edge cases" section would suit generated **workflow patterns** (`workflows/lint.py` or the `generate` prompt), not traits. |

## Held candidates — good, but waiting on an observed failure

Applying this file's own growth rule rather than shipping everything that scored well:

- **`comment-rot`** (from `pr-review-toolkit/agents/comment-analyzer`) — the strongest held candidate,
  and the one with a genuinely architecture-specific hook: `edit_file` anchor-replaces *in place*,
  so the mechanic that makes revisions cost the diff instead of the document is exactly the mechanic
  that leaves a docstring describing code that no longer exists. Also targets the changelog-comment
  tendency ("added null check") that is near-universal in generated diffs. **Held** because no run
  has yet produced the failure, and three code-facing traits landing at once is more than the
  start-minimal rule supports. One decision away from shipping.
- **`local-convention`** (from `feature-dev/agents/code-architect`) — find the nearest existing
  analog and follow its shape before writing anything new. Real tendency to counter, but anecdotal
  evidence and largely already done by the target repo's own CLAUDE.md, which a routine reads anyway.
- **`codebase-orientation`** (from `feature-dev/agents/code-explorer`) — trace one execution path end
  to end instead of sampling files by name. After removing what `decision-commitment` (when to stop
  looking) and `ledger-discipline` (record it so the next run doesn't re-explore) already cover, the
  residue is one heuristic, and `read_file`'s `paths` batching is already taught by the harness
  contract.

## A note on how this survey was done

The first pass over the [Claude Code plugins repo](https://github.com/anthropics/claude-code/tree/main/plugins)
filtered it by *mechanism* — "which plugins inject prompt text at session start" — found the two
output-style hooks, and concluded the repo offered a pattern but no content. That was wrong, and
wrong in an instructive way: the largest body of reusable practice prose in that repo is in its
**skills and agent definitions**, not its hooks. `frontend-design` is a skill; the
`claude-opus-4-5-migration` plugin carries a `references/prompt-snippets.md` that is literally a
set of drop-in system-prompt fragments; `pr-review-toolkit` ships six specialized review lenses as
agent prompts.

The lesson generalizes to anyone extending this set: **survey by content, not by delivery
mechanism.** A hook, a skill, an agent definition and a slash command are four packaging formats
for the same underlying thing — prose that shapes how a model works — and filtering on the
packaging silently discards most of the material.

## How this set should grow

Anthropic's context-engineering guidance offers one operational heuristic worth adopting
wholesale: **start minimal and add instructions only in response to observed failures.** A trait
added because it sounds wise is indistinguishable, at write time, from one that works.

This instance can do better than taste, because a trait is part of a routine's recipe and
`status.json` already stamps `recipe_commit`: adding or removing one is a recipe version change,
so `run_health.py` buckets the runs before and after it and the Stats tab's spend series shows
what it cost. Judge a trait on outcome *and* spend together — the literature's clearest warning
about agent evaluation ([*AI Agents That Matter*](https://arxiv.org/abs/2407.01502)) is that
accuracy-only comparison produces agents that are needlessly complex and expensive.
