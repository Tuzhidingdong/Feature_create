# Model Routing and Zero-Paid-Cost Policy

## 1. Actual target

Feature_create is designed to use two OpenCode Go models whose current published input/output/cache prices are all Free. Worker A and Worker B are separate model workers.

The target is:
- OpenCode paid inference charge for Worker A: $0;
- OpenCode paid inference charge for Worker B: $0;
- paid fallback calls: 0;
- no semantic extraction routed to the more expensive Antigravity host model.

This is not a claim that the system generates zero tokens. The models still process input/output tokens; their published price is Free while the free offer is active.

The OpenCode Go subscription fee, if subscribed, is separate from inference token prices.

## 2. Current preferred pair

As verified against the official OpenCode Go model and pricing documentation on 2026-11-08:

| Worker | Model reference | Current published price | Offer status |
|---|---|---|---|
| A — source-centric | `opencode-go/crouton-alpha-free` | Input/output/cache Free | Limited-time offer |
| B — feature-centric | `opencode-go/beam-free` | Input/output/cache Free | Limited-time offer |

The published Go documentation currently lists unlimited request allowances for these free models, but availability and offers can change. The local runtime must not rely on this snapshot alone.

Official references:
- https://opencode.ai/docs/console/go/
- https://opencode.ai/docs/console/models/

## 3. Required model-routing behavior

Every Worker A and Worker B task must be invoked with an explicit `--model provider/model` selection. The models must be different.

- Worker A uses the configured verified-free model A.
- Worker B uses the configured verified-free model B.
- Do not use a global default model for worker inference.
- Do not allow a model-resolution failure to fall back to the default model.
- Do not retry a failed task on a different, unverified, or paid model.
- A paid-model fallback is never an acceptable availability workaround.

If either selected model is absent from the current model catalog or cannot be verified as zero-priced, pause before dispatching the semantic task.

## 4. Hidden/background OpenCode model calls

The cost policy applies to every OpenCode inference call, not just the main worker response.

- Use deterministic `--title` values so session-title generation does not call a helper model.
- If `small_model` or an equivalent helper route is configured and may run, explicitly pin it to one of the verified free models.
- Do not leave title/helper model selection to implicit defaults.
- Do not allow provider auto-selection or paid model fallback.
- Keep all agents used by the worker task pinned to the same verified free model unless a separate explicitly verified free route is part of the task plan.

The orchestrator should avoid unnecessary automatic subagents and model-generated summaries. Deterministic Python summaries should be preferred.

## 5. Free-model verification

At run preflight:
1. Refresh/read the models available to the current OpenCode installation.
2. Confirm both exact model references are present and enabled.
3. Check current provider/catalog pricing metadata or a freshly verified official pricing snapshot.
4. Require input, output, cached-read, and cached-write prices to be zero/free.
5. Confirm the selected pair contains two distinct model IDs.
6. Record model IDs, verification source, and verification date in the run manifest.
7. Dispatch only after both pass.

If the local model list exposes availability but not price metadata, that alone is not enough to prove a model is free. The preflight must rely on a current official price record; if it cannot do so, pause and request an updated verification rather than guessing.

## 6. Post-call accounting verification

Parse OpenCode's structured JSON events and usage metadata where available. For every worker task record:
- requested provider/model;
- actual provider/model reported by the response;
- input/output/cached token counts when exposed;
- cost/allowance when exposed;
- task/session ID;
- exit status and retry count.

Reject and quarantine the worker output if the actual model differs from the pinned model, if any paid route appears, or if cost metadata indicates a charge. A post-call check is an audit backstop, not a substitute for preflight.

## 7. Antigravity Supervisor budget

Antigravity is the orchestration/control plane, not a third literature worker.

It should:
- read compact corpus/task manifests and progress cards;
- prepare bounded dispatch requests;
- launch or monitor OpenCode Worker A/B;
- inspect exceptions and targeted review requests;
- coordinate the deterministic reconciliation pipeline.

It should not read every full paper/SI or re-extract values itself. The bulk semantic workload belongs to the two free OpenCode workers. Use Python for all deterministic work.

## 8. Fail-closed behavior

If a free model becomes unavailable, changes price, is region-restricted, or fails model resolution:
1. do not start that worker task on a paid default;
2. checkpoint completed work;
3. mark the pending task as `PAUSED_NO_VERIFIED_FREE_MODEL`;
4. report the unavailable model and verification evidence;
5. resume only after a second verified free model is configured.

No silent substitution or paid fallback is allowed.

## 9. Cost terminology

Reports must distinguish:
- free model inference price: verified Free / not verified;
- actual usage-token counts: record if exposed;
- billed/allowance cost: record if exposed;
- OpenCode Go subscription fee: separate;
- Antigravity Supervisor usage: separate and not treated as OpenCode worker usage.

Never say “zero tokens were processed.” Say “zero paid model-token cost” only when the model pricing and actual route are verified.
