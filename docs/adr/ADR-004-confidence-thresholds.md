# ADR-004: Confidence Thresholds for Low-Confidence / Human-Review Flagging

## Status
Accepted

## Context
Two independent confidence signals exist in the pipeline:

1. **STT confidence** (`Transcript.low_confidence`) — derived from
   faster-whisper's per-segment `avg_logprob` and the overall
   `language_probability`.
2. **Structuring confidence** (`FieldReport.needs_human_review`) — derived
   from the LLM's self-reported `extraction_confidence`, plus a hard override
   for safety-relevant severity.

Both need concrete numeric thresholds, and those thresholds materially affect
how much human review load the system generates — too strict and every
report needs review (defeating the automation purpose); too loose and bad
transcriptions/extractions silently reach dispatch systems.

## Decision
- **STT low-confidence**: `avg_logprob < -1.0` OR `language_probability < 0.5`
  (constants: `LOW_CONFIDENCE_LOGPROB_THRESHOLD`,
  `LOW_CONFIDENCE_LANGUAGE_PROB_THRESHOLD`).
- **Human review required**: `extraction_confidence < 0.65` OR
  `severity in {HIGH, CRITICAL}` (constant:
  `HUMAN_REVIEW_CONFIDENCE_THRESHOLD`).

Rationale:
- `avg_logprob < -1.0` is a widely-used informal threshold in the
  Whisper/faster-whisper community for "the model itself is uncertain about
  this segment" — it's not a calibrated probability, but consistently
  correlates with garbled audio, heavy accents, or background noise (all
  common at job sites — wind, machinery, radio static).
- `language_probability < 0.5` catches a different failure mode: the model
  isn't unsure about *words*, it's unsure about *which language* it's even
  hearing — common when a recording is mostly silence/noise with a few
  fragments of speech.
- The **severity override** for human review is deliberate and non-negotiable
  regardless of LLM confidence: a `CRITICAL` or `HIGH` severity report
  (e.g. a safety incident) should always get human eyes before it's treated
  as final, even if the LLM is "confident" — the cost of a missed safety
  signal vastly outweighs the cost of an unnecessary human review.
- `0.65` for general extraction confidence was chosen as a deliberately
  moderate bar: low enough that well-formed, unambiguous transcripts (most
  routine "maintenance completed" notes) sail through without review, high
  enough that genuinely ambiguous extractions (the LLM had to guess at an
  equipment ID, or the category was a toss-up) get flagged.

## Consequences
- These are environment-config-free constants (in `domain/constants.py`),
  not per-deployment tunables, by design — they're closer to a clinical
  threshold than an ops preference, and should change only via a reviewed
  ADR update, not a `.env` tweak. (If a future deployment genuinely needs a
  different threshold per customer, that should be a deliberate follow-up
  ADR, not an undocumented config drift.)
- `needs_human_review` does not block sync — the report still queues and
  syncs to the central server; the flag travels with it as a `warnings`
  entry in `PipelineResult` so the *central* system's human-review queue
  (out of scope for this case study) can act on it. FieldOpsIQ's job is to
  flag, not to gate.
- Because both thresholds are heuristics rather than calibrated
  probabilities, this ADR explicitly does not claim any precision/recall
  guarantee — a production rollout should validate these thresholds against
  a labeled sample of real field recordings before trusting them at scale.
