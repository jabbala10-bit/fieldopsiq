# ADR-008: Plain Orchestrator Class, Not a LangGraph State Machine

## Status
Accepted

## Context
Several other case studies in this portfolio (notably SupportIQ's adaptive
multi-agent RAG pipeline) use LangGraph to model branching, looping
agent behavior — where the next step genuinely depends on runtime decisions
a model makes (which tool to call, whether to retry a different retrieval
strategy, whether to escalate). FieldOpsIQ's pipeline needed the same
question asked of it: does this orchestration deserve a graph-based state
machine, or is it simpler than that?

The FieldOpsIQ pipeline is: validate → probe duration → transcribe →
structure → persist → enqueue. Each stage either succeeds and moves to the
next, or fails and the whole job is marked `FAILED`. There is no point at
which the pipeline branches based on a model's decision, retries a different
strategy, or loops back to an earlier stage.

## Decision
Implement `FieldOpsPipeline` as a **plain Python class** with a linear
`run()` method — explicit sequential calls to each service, explicit
try/except around the whole sequence, explicit status updates at each
stage — rather than as a LangGraph `StateGraph`.

Rationale:
- LangGraph earns its complexity when the *control flow itself* is dynamic —
  when an LLM's output determines which node runs next, or when a pipeline
  needs to loop (e.g. retrieve → grade → retrieve again) based on runtime
  state. FieldOpsIQ's flow is identical for every job: the LLM is used
  *within* a stage (structuring) but never decides *which stage* comes next.
- A plain class is easier to read, debug, and unit-test for this shape of
  problem: `tests/integration/test_pipeline_integration.py` exercises every
  failure branch (bad audio path, STT failure, LLM failure) via straight
  mock injection and assertion, with no need to reason about graph state or
  node wiring.
- Introducing a graph framework for a linear sequence would add a dependency
  and a layer of indirection that provides no behavioral benefit here, while
  making the crash-resume status tracking (`JobStatus` transitions persisted
  at each stage) less obvious to a future engineer reading the code.

## Consequences
- If FieldOpsIQ's scope grows to include genuinely agentic behavior — for
  example, an LLM deciding whether a transcript needs a *second* recording
  pass, or routing different report categories to different downstream
  structuring prompts/models — that would be the point to revisit this ADR
  and consider LangGraph, consistent with how SupportIQ justified its own
  use of the framework.
- The explicit, sequential `try/except FieldOpsIQError` block in `run()` is
  the single place that defines "what happens on failure" for the entire
  pipeline — there's no scattered per-node error handling to keep in sync.
- This keeps FieldOpsIQ's dependency footprint smaller than the RAG-style
  case studies, which is itself a deliberate point of contrast across the
  portfolio: not every FDE engagement needs an agent framework, and knowing
  when *not* to reach for one is part of the architectural judgment being
  demonstrated.
