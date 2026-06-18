"""
Prometheus metrics for FieldOpsIQ.

Counters/histograms are defined once at module level (Prometheus client
convention) and imported wherever a measurement needs to be recorded.
Exposed at GET /metrics by the API layer.
"""
from __future__ import annotations

from prometheus_client import Counter, Gauge, Histogram

# --------------------------------------------------------------------------
# STT metrics
# --------------------------------------------------------------------------

STT_REQUESTS_TOTAL = Counter(
    "fieldopsiq_stt_requests_total",
    "Total STT transcription attempts",
    labelnames=["status"],  # success|error
)

STT_DURATION_SECONDS = Histogram(
    "fieldopsiq_stt_duration_seconds",
    "Time spent transcribing a single audio file",
    buckets=(1, 5, 10, 30, 60, 120, 300, 600),
)

# --------------------------------------------------------------------------
# LLM structuring metrics
# --------------------------------------------------------------------------

LLM_REQUESTS_TOTAL = Counter(
    "fieldopsiq_llm_requests_total",
    "Total LLM structuring attempts",
    labelnames=["status"],  # success|unavailable|schema_failure
)

LLM_STRUCTURING_DURATION_SECONDS = Histogram(
    "fieldopsiq_llm_structuring_duration_seconds",
    "Time spent structuring a transcript into a FieldReport",
    buckets=(0.5, 1, 2, 5, 10, 20, 40),
)

# --------------------------------------------------------------------------
# Sync queue metrics
# --------------------------------------------------------------------------

SYNC_ATTEMPTS_TOTAL = Counter(
    "fieldopsiq_sync_attempts_total",
    "Total sync attempts to the central server",
    labelnames=["status"],  # success|failed|exhausted
)

SYNC_QUEUE_DEPTH = Gauge(
    "fieldopsiq_sync_queue_depth",
    "Current number of records waiting to sync",
)

CONNECTIVITY_STATE = Gauge(
    "fieldopsiq_connectivity_state",
    "Current connectivity state (1=online, 0=offline)",
)

# --------------------------------------------------------------------------
# Pipeline-level metrics
# --------------------------------------------------------------------------

PIPELINE_JOBS_TOTAL = Counter(
    "fieldopsiq_pipeline_jobs_total",
    "Total audio jobs processed end to end",
    labelnames=["final_status"],
)

HUMAN_REVIEW_QUEUE_DEPTH = Gauge(
    "fieldopsiq_human_review_queue_depth",
    "Current number of reports flagged for human review",
)
