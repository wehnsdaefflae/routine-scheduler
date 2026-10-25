"""The stage order a recipe STATES (statemap.declared_order): its numbered `## Run flow` list or
its arrow chain — never the first place prose happens to name a stage. First mention mis-ordered
10 of 29 live routines (scheduler-builder's phase graph started at `build`)."""

from rsched.readmodels import statemap


def test_an_intro_naming_a_stage_file_does_not_outrank_the_flow_list():
    md = ("You build here (`stages/build.md` is the heart of it).\n\n## Run flow\n"
          "1. **orient** — look around.\n2. **build** — the change.\n3. **record** — close.\n")
    assert statemap.declared_order(md, ["build", "orient", "record"]) == [
        "orient", "build", "record"]
    assert statemap.module_rank(md, "build") < statemap.module_rank(md, "orient")  # the old way


def test_an_arrow_chain_is_a_stated_order_even_across_a_wrapped_line():
    md = ("Each run is one state machine: orient → build → hand-back →\n"
          "record-close. Read `stages/build.md` for the work.\n")
    assert statemap.declared_order(md, ["build", "hand-back", "orient", "record-close"]) == [
        "orient", "build", "hand-back", "record-close"]


def test_an_item_walking_several_stages_contributes_them_in_its_order():
    md = ("## Run flow\n\n1. **orient** — the targets.\n"
          "2. Per target, the sweep's first work:\n"
          "   - `outcome-review`, then `improve` or `rebuild`\n\n"
          "3. **record** — finish.\n\nPhase model is steady.\n")
    assert statemap.declared_order(md, ["improve", "orient", "outcome-review", "rebuild",
                                        "record"]) == [
        "orient", "outcome-review", "improve", "rebuild", "record"]


def test_stages_the_flow_never_names_follow_by_first_mention():
    md = ("See `stages/extra.md` when needed.\n\n## Run flow\n"
          "1. **gather**\n2. **act**\n")
    assert statemap.declared_order(md, ["act", "extra", "gather", "zzz"]) == [
        "gather", "act", "extra", "zzz"]


def test_a_recipe_stating_no_order_keeps_first_mention():
    md = "Start with `stages/orient.md`, then `stages/record.md`.\n"
    assert statemap.declared_order(md, ["record", "orient"]) == ["orient", "record"]
