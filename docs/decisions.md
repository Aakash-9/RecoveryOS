# Engineering log

Decisions that are not obvious from the code, and two bugs the test suite caught that changed the
design. Kept because the reasoning is the interesting part, and because a reviewer asking "why is
it like that?" deserves an answer that is not archaeology.

---

## Why `NO_ACTION` scores exactly zero

It would have been easy to give doing nothing a small positive score for "goodwill preserved", or
a negative one for "revenue foregone". Both are wrong, and both destroy the property that makes
the engine legible: because `utility(NO_ACTION) = 0` by construction, the sentence "this action
beat doing nothing" is literally what the comparison computes. Any other value turns it into a
number nobody can interpret.

## Why uplift is modelled multiplicatively

`p_treated = p_self + (1 − p_self) × lift` rather than estimating `p_treated` directly and
subtracting. The multiplicative form guarantees that uplift shrinks towards zero as self-cure
rises, which is the behaviour we actually want, and it matches the potential-outcomes composition
the simulator uses on the other side. Estimating both ends independently would let the engine
produce negative uplift for a reliable payer, which is meaningless.

## Why the utility floor only applies to human escalation

The first version applied `min_utility_paise` to every action, and it suppressed ₹0.85 messages
that were worth ₹40. The floor exists to stop a *person's time* being spent on trivia; a
sub-rupee automated action only has to beat doing nothing, which `NO_ACTION = 0` already enforces.

## Why escalation is a last resort rather than a competing option

Escalation is a terminal state: choosing it forfeits every cheaper attempt that could still have
worked. Scoring it against the other actions on utility alone meant the engine escalated early
and often — 30% of the book at one point — which is the opposite of automating anything. Making a
person the fallback when automation has run out of moves is both how collections teams actually
work and, measurably, worth more money.

## Why there are two drivers for one loop

`agent/graph.py` builds a LangGraph `StateGraph`; it also carries a twenty-line plain driver over
the same node functions. Normally that is waste. It is kept because the graph is the artefact
people want to see and the fallback removes a dependency failure from the demo path.
`test_langgraph_and_the_plain_driver_agree` asserts they produce identical decisions, so the two
cannot drift.

## Why `RULEBOOK+RULES` exists

Without it the comparison is dishonest in the baseline's favour *and* ours. Plain `RULEBOOK`
sometimes out-earns RecoveryOS — but it does so with ~195 guardrail violations and ~19 customers
driven to opt out. A fully compliant version of the same ladder is the only fair like-for-like:
identical compliance posture, different decision quality. Leaving it out would have let us
compare against a strawman.

## Why the headline uses the worst seed

The seed-42 column in the README is RecoveryOS's *lowest* of seven seeds. Picking the best seed is
the easiest way to lie with a simulation, and the track brief asks for honest metrics three
separate times.

## Why sqlite3 and not an ORM

Twelve tables, simple selects and an append-only ledger. An ORM would be a dependency and a layer
of indirection for no gain. The audit ledger in particular is clearer as literal SQL.

---

## Bug: the model could schedule around the guardrails

**Found by** `test_llm_is_not_load_bearing::test_a_hostile_model_changes_nothing_that_matters`,
on the first run, before it had ever passed.

The claim was that the language model cannot affect what the system does with a merchant's money.
The test ran the whole book twice — once with no model, once with a model returning hostile
garbage — and the traces diverged.

The cause: promise extraction was handed straight to the model. A hallucinated date became a real
scheduling decision. The hostile narrator returned `2099-01-01`, the case entered `WAITING`
against a live promise, and the customer would never have been chased again. The model had reached
into business state through a side door that no guardrail was watching.

**The fix was architectural, not a prompt.** `engine/promises.py` now runs a deterministic parser
first, handling the shapes people actually write ("on Friday", "by the 5th", "give me till
Monday", "end of the week"), with negation and intent detection. The model is a fallback for text
the parser cannot read, and even then `clamp_promise_date` forces its answer into a policy window
before it can schedule anything: never in the past, never less than 12 hours out, never more than
21 days out. A promise beyond that is not a promise to pay, it is a way of not being contacted.

The test now passes because the parser covers the simulator's texts. `clamp_promise_date` is
tested separately, so the boundary is still proven for text the parser cannot read.

## Bug: waiting on a promise burned the iteration budget

**Found by** running the `promise` demo scenario and reading the output.

A case monitoring a live promise chose `NO_ACTION` with no stop reason — correct — but
`_advance_hours` returned 0 for `NO_ACTION`, so the clock never moved. The case was re-decided at
the same timestamp every round until it hit `MAX_ITERATIONS` and stopped, *before the customer's
own promised date had even arrived*. The output was seven identical lines of "Monitoring an active
promise to pay" at `24 Sep 10:30`.

Fixed by sleeping until the promise comes due. `test_waiting_on_a_promise_advances_the_clock`
guards it: at most two waiting decisions, at least one decision after the promised date, and the
promise must end `FULFILLED` or `BROKEN` rather than dangling.

This one is worth noting because it was invisible to the test suite as it stood — every existing
assertion passed. It took looking at the actual output of a demo.
