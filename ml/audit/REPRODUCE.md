# Reproduce the local EVA audit

All commands below are PowerShell, from the repository root unless noted. Use the role's installed dependencies. This trial used the existing ML Python environment, which also had the backend packages installed. Use the backend's own environment and lockfile on a fresh installation. Use new output filenames or a new run directory for every measurement. The primary comparison uses the same sorted field order as the backend database. `--file-order` reproduces the earlier caller-order diagnostic only. The benchmark opens its output in exclusive-create mode.

## Identity of this run

- Base Git revision: `a8ee9d3c3f942422849adbf2d85087b5c9fc7f75`. The original branch was clean. See [the preregistered gate](BASELINE.md) for control metrics and file hashes.
- GGUF SHA-256: `4c0beeaa5d24765d791fc6c0fee9b4a30b5e563d18dd6830c61b9993faf8e0b6`. Local llama-server.exe SHA-256: `cade0928910ef7c315a8d785bb5b38803b3acfd0d15fca0c5755ec68dd1e8f20`.
- Private organizer envelope SHA-256: `7311f19010932804b1fd3391d50a311dd8699a04e8441e756ae94a8b51c0b455`. Canonical input IDs come from array order; the benchmark normalizes MM:SS offsets and leaves unconfirmed voice offsets null. The existing reference and baseline ZIP are hashed in BASELINE.md.
- Control for human evaluation: archived default consultation-v1.1 on 15 fixed chats (5 per language). Control for synthetic and voice runs: fresh default provider, same GGUF and worker. No fitting on the 15 for this branch; they were already used to select the previous evaluator. Iterating a profile after seeing aggregate results makes repeated comparisons retrospective. The nine counterfactual pairs and translations are development tests; group by underlying scenario, never split translations across train/test. The five organizer calls are a technical pilot, with no human gold for this run. Private data is not necessary for the synthetic tests.
- An already running, reviewed llama.cpp worker on loopback served the exact model alias. Replace 59068 with the verified local worker port on your machine. The default production ML service also supports a private worker that it starts itself. No models or private transcripts are sent to an external endpoint.

## Unit, API, UI build

```powershell
Push-Location ml
.\.venv\Scripts\python.exe -m pytest -q
Pop-Location
Push-Location backend
.\.venv\Scripts\python.exe -m pytest -q
Pop-Location
.\ml\.venv\Scripts\python.exe tools/check_contracts.py
Push-Location frontend
npm ci
npm run build
node --test tests/*.test.mjs
Pop-Location
```

## Paired measurements

From the repository root, the following command runs the paired control and candidate, creates a fresh ignored run directory and writes the measured per-input outputs. Use a different RunName for each run.

```powershell
.\ml\benchmarks\run_audit.ps1 -WorkerPort 59068 -RunName audit-repeat -SyntheticOnly -Ablations
# Full human / pairs / five calls (requires the team's existing private inputs):
.\ml\benchmarks\run_audit.ps1 -WorkerPort 59068 -RunName audit-full -Dev <local-envelope.json> -Reference <reference.json> -Ablations
```

Equivalent individual commands (from ml):

```powershell
$p='.\.venv\Scripts\python.exe'
$worker=59068
$run='data/audit-repeat'
New-Item -ItemType Directory -Force $run | Out-Null
& $p -m benchmarks.audit_run --port $worker --variant baseline --pairs --out "$run/wire-pairs-baseline.jsonl"
& $p -m benchmarks.audit_run --port $worker --profile source-events --variant trace,policy,score,sentinel --pairs --out "$run/wire-pairs-final.jsonl"
& $p -m benchmarks.audit_compare --baseline "$run/wire-pairs-baseline.jsonl" --candidate "$run/wire-pairs-final.jsonl"
```

For the human run, omit `--pairs`, supply `--dev <local organizer envelope>` and `--reference <local adjudicated reference JSON>`. Run `--variant baseline` and the candidate on the same inputs, and also compare to the frozen archive via `--baseline data/consultation-v1.1/sealed/blind15-consultation-v1.1.zip`. The historical timing file is configurable with `--times`. For the pilot use `--voices`; no accuracy is computed on those records. Run the same profile with `--pairs --ablation` and one component removed, for example `--variant trace,policy,sentinel`. The six preselected scenarios cover both directions of each pair, distributed over RU/KZ/EN; this small set measures mechanism behavior rather than population quality. Run serially on a warmed worker. For optional live shadow validation, run a separate backend with `audit_stack.py backend --mode shadow --port 18604 --run-dir <the same isolated run folder>`, then `audit_shadow.py http://127.0.0.1:18604 <receipt.json>`. The per-input JSONL is private and belongs in an ignored directory.

`audit_compare` returns counts per language and criterion, false/missed N/A, precision/recall/F1, misses including abstentions, false accusations, reference integrity, coverage, score MAE on the same completed conversations, and improvement/regression lists. Undefined denominators are JSON null. `audit_evidence.py <candidate.jsonl>` needs openpyxl (optional reporting dependency) and the locally supplied adjudicated workbook. It measures agreement with recorded reference IDs; evaluating semantic sufficiency still needs an independent human assessment of the model's actual rationale. To build the same aggregate as this branch from the full protocol, use `benchmarks.audit_release_report --run-dir data/audit-components --out audit/metrics.json`. It includes the previous experimental profiles for comparison. File names correspond to the profiles in [the report](RESULTS.md).

## End-to-end, isolated host

Use distinct unused ports and a fresh ignored run folder. In three terminals:

```powershell
# 1: ML, using an explicitly supplied local worker
.\ml\.venv\Scripts\python.exe ml/integration/audit_stack.py ml --port 18601 --engine-port 59068 --run-dir ml/data/audit-components/final-integration
# 2: Backend, isolated SQLite under backend/data/audit-integration
.\ml\.venv\Scripts\python.exe ml/integration/audit_stack.py backend --port 18600 --run-dir ml/data/audit-components/final-integration
# 3: UI, from frontend
$env:VITE_API_URL='http://127.0.0.1:18600'
$env:VITE_API_MODE='http'
$env:VITE_API_TIMEOUT_MS='110000'
npm run dev -- --port 5188 --strictPort
```

Run the real checks from root:

```powershell
$p='.\ml\.venv\Scripts\python.exe'
$receipt='ml/data/audit-components/final-integration/receipt.json'
& $p ml/integration/audit_http.py http://127.0.0.1:18600 $receipt
& $p ml/integration/audit_long.py http://127.0.0.1:18601
& $p ml/integration/audit_challenges.py http://127.0.0.1:18601 ml/data/audit-components/challenges.json
# Node with Playwright and Edge installed locally; EVA_PLAYWRIGHT_PATH can point at the installed module.
node ml/integration/audit_ui.mjs $receipt
& $p ml/integration/audit_rollback.py $receipt backend/data/audit-integration/final-integration/audit.sqlite
```

The HTTP test imports a synthetic mixed-language conversation, evaluates it, reviews one criterion twice with the same operation ID, changes weights, confirms reuse of the auto-decisions, and exports the history. SQLite rollback is tested by copying the DB with its backup API, extracting only the original backend/contracts from Git, then reading through the previous application. Only the temporary copy is opened by old code. Use `audit_resources.py --pid <worker PID> --samples 24 --out data/<run>/resource-samples.jsonl` from ml to measure Windows host and GPU memory without installing a monitoring agent.
