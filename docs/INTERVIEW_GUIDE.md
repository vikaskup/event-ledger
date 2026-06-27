# How to Talk About This Take-Home in the Interview

This is a guide for the walkthrough round, not a script to memorize. The
goal of a take-home like this isn't "did you build a production system in 4
hours" — it's "can we trust your judgment about a production system based on
how you talk about the smaller one you built." Optimize for that.

---

## 1. What this kind of assessment is actually evaluating

A distributed-systems take-home like this one is rarely graded purely on
"does it work." Interviewers are typically scoring across these dimensions,
roughly in this order of weight:

| Dimension | What they're looking for |
|---|---|
| **Functional correctness** | Does idempotency actually work under a real duplicate? Is balance actually order-independent, or does it just look that way in the happy path? |
| **Judgment under a time box** | Did you spend your limited hours on the things the brief emphasized (idempotency, ordering, resiliency, tracing) instead of gold-plating something else? |
| **Tradeoff articulation** | Can you say *why* you picked a circuit breaker over a bulkhead, SQLite over Postgres, sync over async - with reasons, not just "the docs said it was fine"? |
| **Failure-mode thinking** | Did you actually test what happens when the dependency is down, not just the happy path? Do you know what happens to data when it's down? |
| **Scalability awareness (not necessarily implementation)** | Can you name the next three bottlenecks and roughly quantify them, even though you didn't build past them? |
| **Observability instinct** | Did you build in a way to *see* what the system is doing, or only a way to make it work once on your laptop? |
| **Communication** | Is the README/walkthrough something a teammate could pick up without you in the room? |

Notice "scale to millions of requests" is on this list as an *awareness*
dimension, not an *implementation* dimension. Nobody expects a 4-hour
take-home to come with Kafka and sharded Postgres. They expect you to know
that's what's missing and why.

---

## 2. The structure for answering "how would this scale to millions of requests?"

This question (or some version of it) is almost guaranteed. Don't jump
straight to "I'd add Kafka and shard the database." That answer is correct
but sounds memorized. Use this structure instead:

### Step 1 - Ask what "millions" means before answering
"Millions of requests" could mean 1M/day or 1M/second - those have wildly
different answers. A senior engineer asks before designing. Say something
like: *"Before I answer, what's the actual target - requests per second
sustained, or total volume per day? And what's the latency SLA on
POST /events?"* This alone signals seniority.

### Step 2 - Name the current bottleneck, specifically
Don't say "it wouldn't scale." Say *what* breaks and *why*:
*"SQLite serializes writes at the file level - there's no horizontal write
path. That's the first wall, not the database choice in the abstract."*
Specificity is the whole signal here.

### Step 3 - Quantify, even roughly
Even rough back-of-envelope math (see `SCALABILITY_REVIEW.md` in this repo)
shows rigor that a vague "it would need to be more distributed" doesn't.
You don't need to be exact - you need to show you instinctively reach for
numbers instead of adjectives.

### Step 4 - Lead with the change that fixes the most things at once
In this system, that's decoupling ingestion from application via a queue -
it fixes both a correctness gap (events stuck unapplied when the downstream
is down) and a scale gap (bursts) in one change. Leading with that over "add
more servers" shows you're solving the actual problem, not pattern-matching
to "scale = add infrastructure."

### Step 5 - State what you'd explicitly NOT do yet
Just as important: say what you'd hold off on. *"I wouldn't reach for a
distributed SQL engine like Spanner or CockroachDB unless we needed
cross-region writes - Postgres sharded by accountId solves this domain's
actual access pattern, since nothing here needs cross-account transactions."*
This shows cost/complexity awareness, which is the difference between
"knows the buzzwords" and "would actually be trusted to make this call."

---

## 3. Questions you should expect, and how to ground your answers

**"Why a circuit breaker instead of retry-with-backoff or a bulkhead?"**
Ground it in the call's position in the system: it's synchronous and on the
client-facing critical path. Retrying a dead dependency just adds latency
without changing the outcome. A bulkhead alone doesn't stop you from
hammering a dead service. The breaker gives a fast, predictable failure mode
and lets the dependency recover without being re-flooded the moment it's
back. Be ready to walk through the actual state machine in
`gateway/app/circuit_breaker.py` (CLOSED -> OPEN after N failures -> HALF_OPEN
probe after a timeout -> CLOSED or back to OPEN) without looking at the code.

**"What happens to data correctness if you ran this with multiple Gateway
replicas right now, unmodified?"**
This is a trap question testing whether you understand your own code's
limits. The honest answer: idempotency still holds (it's enforced at the DB
layer via the `eventId` primary key, not in-process state), but the circuit
breaker state is per-replica, so replicas could disagree about whether the
breaker is open. That's a degraded-but-not-broken outcome, and you should be
able to say so plainly instead of pretending it's fine.

**"Why SQLite at all, why not start with Postgres?"**
Be honest about scope: the brief explicitly allowed embedded/in-memory DBs
and the time box was 3-4 hours. SQLite let you spend the time budget on the
actual hard requirements (idempotency, ordering, tracing, resiliency)
instead of provisioning and wiring a database. That's a legitimate
engineering tradeoff for the constraint you were given - the mistake would be
defending SQLite as a *production* choice, which it isn't, and you should say
so unprompted.

**"What's the one thing you'd fix first if this went to production
tomorrow?"**
Answer with the correctness gap (2.3 in `SCALABILITY_REVIEW.md`): events
that fail to apply when Account Service is down are never retried. That's
not a hypothetical scale problem, it's a real bug that exists today, at any
volume. Naming a correctness bug before a scale concern shows the right
priority order.

**"How would you test the scaled-up version?"**
Load testing with realistic burst patterns (not steady-state - this domain's
upstream is explicitly described as batch-driven), chaos testing on the
downstream dependency (kill it mid-burst, not just at idle), and replaying
production-shaped duplicate/out-of-order traffic through a staging
environment before trusting it in prod.

---

## 4. What NOT to do in this kind of interview

- **Don't claim the take-home already handles scale.** It doesn't, and
  saying it does is the fastest way to lose credibility once they ask a
  follow-up.
- **Don't recite an idealized "real" architecture you didn't build and can't
  defend in detail.** If you say "I'd use Kafka," be ready for "why Kafka and
  not SQS/Pulsar/Kinesis" - have an actual reason (ordering guarantees per
  partition key, replay-ability, ecosystem) or don't lead with a specific
  product name; say "a durable queue" and let them probe.
- **Don't over-index on the bonus list.** OpenTelemetry Collector + Jaeger,
  Prometheus, rate limiting, etc. were explicitly marked optional. If you
  skipped them, say why (time budget, prioritized the required list) rather
  than apologizing for it.
- **Don't be vague about your own code.** "I think it handles that" is worse
  than "no, it doesn't, here's why and here's what I'd change." They're
  testing your relationship with your own work, not just the work itself.

---

## 5. A one-line summary to internalize

**The take-home proves you can build a correct, well-tested, well-reasoned
system within a tight scope. The walkthrough proves you know exactly where
the edges of that scope are, and can reason past them without overclaiming.**
That second part is the actual interview.
