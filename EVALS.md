# How I test an LLM system

You cannot unit test a language model. The same prompt gives you slightly different words every run, so `assertEqual` is useless the moment a real model enters the loop. But "we eyeballed some chats and it seemed fine" is not engineering either.

Slotly's answer is two layers. Each one catches what the other cannot.

## Layer 1: 1,701+ deterministic tests, the LLM faked

Both Gemini seams (the NLU call and the reply call) are replaced with fakes that return exactly what I tell them to. That makes everything downstream deterministic, free, and fast enough to run on every pull request.

This layer proves the machinery is correct *given* correct LLM output: state machine transitions, slot filling, revision handling, Redis session storage, idempotency locks, API payloads, WhatsApp message building. It includes five hardening tiers built specifically around how systems fail quietly:

* **Silent failures.** The scary ones: a reschedule that half completes, a double tap that books twice, a price that reaches the customer altered, a stale cache showing a cancelled booking, an offered time the API never returned. Each has a dedicated guard suite.
* **Messy conversations.** Mid sentence mind changes ("yes, but can we do 3pm?"), task switching, emoji barrages, stop and resume, over answering, wishy washy replies.
* **Input variety.** Typos, romanized Sinhala, fuzzy service names, requests for services the salon does not offer.
* **Config variety.** A business with zero services, services with null durations, a customer with five upcoming appointments.
* **Failure injection.** Every API call point broken mid conversation, Redis down mid turn, Gemini dying halfway through a booking. The bot must degrade to a polite reply and never leave a half finished write.

## Layer 2: scored evals against the real model

With a real LLM you do not assert, you *evaluate*: run curated datasets, score the output against thresholds, and gate on aggregate metrics plus zero critical failures. Never exact text matching.

The suite as it stands:

* **NLU accuracy.** 153 gold labelled inputs covering pure Sinhala, romanized Singlish, English, code switched text, typos, emoji, multi intent messages, and relative dates. Scored on intent plus six entity slots at temperature 0. Gate: intent at or above 98%, every slot at or above 95%. Latest run: **99.3% intent, 97.3% worst slot**.
* **Reply quality.** Representative instruction and data pairs rendered into real Sinhala replies, graded on deterministic invariants: contains Sinhala, structured data reproduced verbatim (no altered prices or IDs), no system prompt leak, no AI disclosure, no fabricated details. Gate: zero critical failures. Latest: **100% critical clean**.
* **End to end journeys.** Whole conversations driven through the real graph with both LLM seams live, against a real seeded API. 28 journeys across four sets: the five core flows in English, the same flows typed in Sinhala script and in romanized Singlish, nine edge and recovery paths (slot busy, staff name that matches nobody, a fully booked day, injected API failures), and four mid flow revision journeys. Side effects are hard asserted: the exact service IDs, branch, date, and time that hit the database. Gate: **100% task success, 100% correct side effects**, currently holding.
* **Adversarial.** 33 attack prompts: prompt injection (delimiter tricks, role confusion, smuggled bookings), jailbreaks, system prompt extraction, cross tenant probes, booking abuse. Gate: zero unsafe outcomes, meaning zero write side effects and zero leaks. Latest: **100% safe**, refused every attack, in Sinhala.

## The part that matters: it runs every night

A GitHub Actions workflow runs the full paid suite nightly against a seeded stack, regenerates the scorecards, appends a token ledger, then runs a regression gate that fails the build on any new threshold breach or any drop beyond tolerance versus a committed, human reviewed baseline. It never rebaselines itself; a human promotes new baselines after reviewing them.

Gates are earned, not declared. A dataset starts observational, and only gets promoted to a hard gate after two consecutive clean runs.

## Runs that earned their keep

The point of paying for real model evals is catching what the free layer physically cannot. Some finds:

* **The revision eval caught a real misroute on its first run.** A customer at the booking confirmation step said "actually, can we do 09:30 instead?" and the live model classified it as a *reschedule*, which wiped the in progress booking and jumped to a flow that immediately aborted. The faked NLU layer could never surface this, because the bug *was* the real model's classification. The fix (treat a reschedule classification carrying a new time as a revision while a booking is in progress) shipped with a regression test, and the eval went to 100%.
* **An eval proved my gold label wrong.** I labelled "next friday" as the immediately coming Friday. The model consistently read it as Friday of *next week*, and after checking how people here actually use the phrase, the model was right. The gold was corrected, not the model.
* **Ambiguity got measured instead of argued about.** A bare "morning" at the time question is genuinely ambiguous (greeting or time?). Rather than forcing a gate, those cases sit in an observational set that is measured on every run but never fails the build.

## What this costs

Cumulative spend across every paid eval run to date is about **2.26 million tokens**, tracked run by run in a ledger the nightly job appends to. On a small Gemini model that is pocket change for what it buys: I know before customers do when a model update, a prompt change, or a "harmless" refactor shifts behaviour.
