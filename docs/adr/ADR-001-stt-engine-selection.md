# ADR-001: STT Engine Selection — faster-whisper

## Status
Accepted

## Context
FieldOpsIQ must transcribe field-technician voice recordings fully offline, on
hardware ranging from a rugged Windows/Linux tablet to a low-power laptop with
no GPU. Candidates evaluated:

| Engine | Pros | Cons |
|---|---|---|
| **faster-whisper** (CTranslate2) | ~4x faster than openai-whisper on CPU at equal accuracy; int8 quantization; active maintenance; Python-native | Still a few hundred MB model download; CTranslate2 native dependency |
| **whisper.cpp** | Smallest binary footprint; runs on very constrained hardware (Raspberry Pi class); no Python runtime dependency | C++ build/packaging overhead; Python bindings less mature; slightly more integration work for a Python-first stack |
| **Distil-Whisper** | Fastest inference (~6x); smallest model size | Measurable accuracy drop on domain-specific vocabulary (equipment names, site jargon) that field reports depend on |

## Decision
Use **faster-whisper** as the default STT engine, with the `small` model size
and `int8` compute type as defaults (both overridable via `WHISPER_MODEL_SIZE`
and `WHISPER_COMPUTE_TYPE` env vars).

Rationale:
- Field reports frequently contain equipment IDs, site-specific jargon, and
  safety-critical phrasing ("isolated," "lockout," "tagged out") where
  transcription accuracy directly affects downstream report quality. The
  accuracy/speed tradeoff favors faster-whisper over Distil-Whisper for this
  domain.
- The target hardware (rugged tablets/laptops used by field technicians) has
  enough CPU and storage budget to comfortably run faster-whisper's `small`
  model in int8 mode within a few seconds per minute of audio — whisper.cpp's
  footprint advantage isn't needed at this hardware tier.
- faster-whisper is pure-Python at the integration layer (the CTranslate2
  backend is a compiled dependency, but no separate build step is required by
  the application developer), which keeps the codebase consistent with the
  rest of the Python service stack used across all FDE case studies.

## Consequences
- The model is loaded once at process startup (`get_stt_service()` eagerly
  calls `load_model()`) rather than per-request, since model load is the
  single most expensive STT operation and the device is single-tenant.
- `whisper_device` defaults to `"cpu"` since most target field hardware lacks
  a GPU; `cuda` remains supported via config for any technician using a
  GPU-equipped laptop.
- If a future case study or deployment targets genuinely constrained hardware
  (e.g. an embedded gateway), whisper.cpp should be revisited — the STT
  service is already isolated behind `WhisperSTTService` so swapping engines
  only requires a new implementation of the same interface.
