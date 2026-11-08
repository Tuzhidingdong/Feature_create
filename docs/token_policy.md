# Model Routing and Token Policy

## 1. Hard requirement

OpenCode model-token consumption is required to be **exactly zero** for every Feature_create run.

This is a routing prohibition, not a preference for a free OpenCode model.

## 2. Forbidden routes

The workflow must not:
- invoke `opencode run` or an equivalent OpenCode inference command;
- create or resume an OpenCode model session;
- start an OpenCode worker or daemon task that performs inference;
- call an OpenCode model provider through another wrapper;
- use OpenCode as a fallback when Antigravity fails;
- select a model based on an unverified `free` label;
- silently reroute a failed task to another provider.

OpenCode may not appear as a semantic execution dependency in the task graph, worker contracts, or retry policy.

## 3. Allowed routes

- **Local deterministic execution:** Python, parsers, validators, file operations, JSON serialization, checksums, and deterministic calculations. These do not consume model tokens.
- **Semantic execution:** the Antigravity host model available to the current Skill run.
- **Native Antigravity task delegation:** allowed only where the host actually exposes and supports it. Do not pretend that parallel workers exist when execution is sequential.

Use the already selected Antigravity route unless the user or host explicitly authorizes a different route. This policy does not promise that Antigravity usage is free.

## 4. Fail-closed behavior

If Antigravity model access is unavailable, rate-limited, or blocked:
1. do not dispatch new semantic tasks;
2. save completed task outputs and a checkpoint;
3. label pending tasks as `PAUSED_MODEL_UNAVAILABLE` or an equivalent explicit state;
4. return a concise blocker report;
5. resume after Antigravity access returns.

Never continue by switching to OpenCode.

## 5. Auditability

Each semantic task should record:
- provider route: `antigravity_host`;
- model identifier when the host exposes it;
- task ID and contract version;
- start/end status;
- retry count;
- whether the model identifier was observable.

Do not claim verified zero OpenCode usage merely because no OpenCode model name appears in a log. The implementation should also enforce a static/runtime guard that rejects OpenCode command invocation and should document the limits of what it can observe.

## 6. Token accounting

Keep separate fields for:
- OpenCode model calls: required value `0`;
- OpenCode token usage: required value `0`;
- Antigravity model usage: record if observable, otherwise `unknown/not exposed`;
- local deterministic processing: no model-token usage.

Do not label the total workflow as “zero tokens” or “free” unless all providers' usage is independently known to be zero.
