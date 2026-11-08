# OpenCode Go Free-Model Worker Execution

## 1. Correct interpretation of "zero consumption"

Feature_create does make OpenCode inference calls. It uses two OpenCode Go free models for the actual semantic work:

- Worker A: `opencode-go/crouton-alpha-free`
- Worker B: `opencode-go/beam-free`

The target is zero paid inference cost for the worker calls, not zero model execution or literal zero tokens processed. These models still process tokens; their published input/output/cache price is Free while the offer is active.

The OpenCode Go plan subscription is a separate fixed fee. The Skill must never describe the whole subscription or workflow as free merely because the selected model endpoints currently have zero per-token price.

## 2. Current preferred model pair

The official OpenCode Go documentation checked on 2026-11-08 lists Crouton Alpha Free and Beam Free as limited-time free models. The published pricing table marks input, output, and cached-read prices as Free and shows an Unlimited request allowance for each. Both model identifiers are listed as:
- `crouton-alpha-free`
- `beam-free`

For OpenCode Go the configured model references are:
- `opencode-go/crouton-alpha-free`
- `opencode-go/beam-free`

The list and offers can change. The policy file is a dated reference snapshot, not a perpetual guarantee. Before each run, refresh the available model catalog and verify current price metadata or a fresh official price record. If the evidence does not prove zero pricing, do not dispatch the call.

Sources:
- [OpenCode Go — model list and pricing](https://opencode.ai/docs/console/go/)
- [OpenCode Models — free model definition and catalog](https://opencode.ai/docs/models/)

## 3. Antigravity Supervisor vs OpenCode workers

Antigravity is used as a thin Supervisor:
- decide the task plan;
- inspect small manifests and progress cards;
- dispatch OpenCode worker tasks;
- react to worker failures;
- request bounded targeted review;
- trigger local Python scripts;
- report final counts.

It must not receive whole-paper/SI bundles for routine extraction. Bulk scientific interpretation belongs to the OpenCode free workers.

Worker A and Worker B are real OpenCode model calls, not Antigravity personas. They have separate model IDs, independent prompts, and independent first-pass outputs.

## 4. Explicit model pinning

Every inference request must explicitly select its model using the installed CLI's supported `--model provider/model` option. Do not rely on the session default or global default.

Illustrative Worker A invocation:

```powershell
opencode run --model opencode-go/crouton-alpha-free --title "FC-A-TASK-000001" --format json --file task_worker_a.md "Execute the attached Feature_create Worker A task and return only the contract JSON."
```

Illustrative Worker B invocation:

```powershell
opencode run --model opencode-go/beam-free --title "FC-B-TASK-000001" --format json --file task_worker_b.md "Execute the attached Feature_create Worker B task and return only the contract JSON."
```

These examples show the routing pattern. The implementation must check the installed OpenCode CLI version and supported flags before relying on a specific option combination. OpenCode documents `run --model`, `--title`, `--format json`, and `--file` for non-interactive/scripted runs.

The attached task file should contain the worker contract and bounded source payload or references to task files. Do not pass very long prompts as a single Windows command-line argument. Prefer file attachment or a stdin mechanism only after confirming that the installed CLI version supports the intended stdin behavior.

## 5. Hidden calls and helper models

The no-paid-cost policy covers every model call made in support of a task.

- Always supply a deterministic session title using `--title`; do not ask OpenCode to generate a title.
- If the configured OpenCode `small_model` can be invoked for any background task, explicitly pin it to a currently verified free model.
- Configure worker agents with their explicit free model references; do not inherit a paid parent-session model.
- Disable unnecessary automatic subagents and model-generated summaries.
- Audit the structured output for any unexpected model/provider change.

OpenCode's model configuration may have defaults and fallback behavior. Explicit per-run model selection and helper-model pinning are therefore part of the cost guard, not optional optimizations.

## 6. Sessions and cleanup

Each task should have a unique deterministic task ID and an isolated OpenCode session. Capture the actual session ID from the structured output/session metadata. Once the response has been saved and validated:
- retain the raw worker JSON in the run artifacts;
- retain the compact task/usage accounting record;
- delete the ephemeral OpenCode session when the workflow's audit-retention policy permits.

Do not delete a session before its raw output and usage metadata have been persisted. Do not reuse an unrelated user session.

## 7. Parallelism

Initial default:
- at most 2 papers active;
- up to Worker A and Worker B per paper;
- at most 4 concurrent OpenCode inference calls.

The orchestrator should enforce a global semaphore, rate limiting, retry backoff, and dynamic concurrency reduction on HTTP 429. Retry the same task with the same pinned free model only. A retry must not switch to a paid model.

## 8. Accounting and postflight

For each Worker task, save:
- requested model reference;
- actual model/provider reported by OpenCode, if exposed;
- session ID;
- task ID and contract version;
- input/output/cache usage when exposed;
- cost/allowance when exposed;
- exit status and retry count.

If OpenCode reports a non-zero cost, an unexpected paid model, or a provider mismatch, quarantine the output and stop new dispatches. Missing cost metadata must be recorded as unknown, not silently treated as zero.

OpenCode's structured JSON output and session stats can be used for postflight accounting. This is a backstop; preflight price verification remains mandatory.

## 9. Privacy note for the current pair

The current Go documentation says Crouton Alpha Free and Beam Free retain data for 30 days and are not used for model training. This is not zero-retention. Do not upload material whose confidentiality terms prohibit that processing. Re-check provider privacy terms before production use.

## 10. Failure behavior

If either model is unavailable, no longer Free, or cannot be verified:
1. stop dispatching affected tasks;
2. save completed worker outputs and checkpoints;
3. mark pending tasks `PAUSED_NO_VERIFIED_FREE_MODEL`;
4. report the evidence and blocked tasks;
5. resume only after two distinct free models are verified.

Never silently fall back to a paid model or shift the entire source extraction burden onto Antigravity.
