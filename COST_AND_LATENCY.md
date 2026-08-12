# Cost and latency engineering

A WhatsApp bot has a strange performance profile. Nobody expects an instant answer in a chat, but Meta *does* expect the webhook acknowledged fast, and every LLM call costs real money on every single message. So the two budgets I actually engineer against are: acknowledge in under a second, and spend model tokens only where a deterministic path cannot do the job.

## The latency budget

Meta retries webhooks it thinks failed, and retries mean duplicate processing and eventually a disabled webhook. The pipeline in front of the model is therefore deliberately thin:

verify the HMAC signature, parse the payload, deduplicate against Redis, schedule a background task, return 200. **Under one second, no LLM work, no API calls.** Everything expensive happens after Meta has its acknowledgement.

The customer experience gets its own tricks:

* **Instant feedback.** The read receipt and typing indicator go out before any model call, so the chat feels alive while the real work runs.
* **Burst merging.** People text in fragments: "tomorrow", then "around 2", then "with Nimal". A short debounce window (2.5 seconds) buffers fragments and processes them as one turn. A message that looks complete (ends in punctuation, or is long) skips the wait but still drains the buffer. This is a latency *and* cost feature: three fragments become one model call instead of three.
* **No dead air on model failure.** A quota error used to mean about 37 seconds of SDK retries before the customer saw anything. A Redis circuit breaker now trips on the first quota error and every turn for the next five minutes skips the model instantly (details in [RELIABILITY.md](RELIABILITY.md)).

## The token budget

The design keeps a hard ceiling of **two model calls per typed turn**: one call to extract intent and entities, one call to phrase the reply. There is no agent loop, no tool calling chain, no "let me think step by step" multiplier. The LLM understands and phrases; deterministic code decides everything that touches the database.

Below that ceiling, most turns cost less:

* **Taps cost zero NLU calls.** Every selectable step in every flow is tappable (WhatsApp lists and buttons). A tap returns a structured ID that routes straight into the active flow, no model needed to understand it. Since guided customers tap through most of a booking, a large share of real turns skip the understanding call entirely.
* **Prompts are kept small on purpose.** The model sees the last 8 messages plus a compact context block (current time, upcoming appointments, a derived customer profile), not an unbounded transcript. Stored history caps at 20 messages; cold start recaps from the durable log truncate each message to 300 characters.
* **Reference data is cached, not refetched.** Tenant config, WhatsApp credentials, business knowledge, and service categories each sit behind a five minute cache; the customer's bookings are cached for five minutes and busted on every write. A typical turn makes one or two internal API calls instead of six.
* **The durable chat log never blocks a reply.** Writing the conversation to MySQL happens off thread, after the send. Logging is not allowed to add latency.

## Measuring it instead of guessing

Every turn's actual token usage is recorded, not estimated. An accumulator reads the usage metadata from both model calls and logs input and output token counts onto that turn's row in the durable chat log. The admin panel prices those stored counts, so "what does a conversation cost" is a query, not a back of the envelope.

The same discipline applies to the eval suite: every paid eval run appends its spend to a token ledger in CI. Cumulative spend across all eval runs to date is about **2.26 million tokens**, which is the total price paid for knowing the bot's accuracy numbers instead of assuming them.

## Why a small model is enough

The bot runs on a small, fast Gemini model rather than a flagship. That only works because of the division of labour above: the model never does arithmetic on prices, never computes availability, never decides state transitions. It maps messy human text (Sinhala, Singlish, typos, emoji) into structured intent, and turns structured facts into natural sounding Sinhala. Those are exactly the tasks small models are good at, and the eval suite holds the proof: 99.3% intent accuracy and 100% adversarial safety on the current gates, at a fraction of flagship cost and latency.
