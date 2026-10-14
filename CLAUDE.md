# run-comfy

Bulk ComfyUI generation on RunPod. Entry point: `python -m run_comfy {validate,run,cleanup,gpus,serverless-setup}`.
Backends: `pod` (runner.py `Runner`) and `serverless` (serverless.py), chosen by `[pipeline].backend` or `run --backend`.
Code: `run_comfy/runner.py` (orchestration), `pods.py` (RunPod), `comfy.py` (ComfyUI API), `jobs.py` (CSV + templating).

When the user asks for a batch:
1. Write/modify `jobs.csv` (columns must match `{{placeholders}}` in the workflow in `config.toml`).
2. Run `python -m run_comfy validate` and fix any errors before spending money.
3. Run `python -m run_comfy run` in the background and monitor `output/run.log`.
4. Read `output/report.csv`; for failed rows, read the error, fix the workflow/CSV, and rerun (finished jobs are skipped).
5. If a run was killed abnormally, run `python -m run_comfy cleanup` (terminates pods, purges serverless queue).

`serverless-setup` creates billable RunPod resources: only run it when the user asks.

Never raise `max_spend_usd` / `max_runtime_min` or put the API key anywhere but `.env` without asking.
Run `pytest` after code changes.
