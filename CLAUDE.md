# run-comfy

AI ad & content factory on RunPod + ComfyUI. Python package `run_comfy`, React dashboard in `web/`.

Entry points: `python -m run_comfy {serve,batch,setup,workflows,build-image,seed-volume,serverless-setup,run,validate,cleanup,gpus}`
and the MCP server `python -m run_comfy.mcp`. The dashboard, CLI and MCP server all call `factory/service.py:Factory`.

Code map:
- `stages.py`: stage DAG executor, content-addressed cache, in-flight dedupe, retries
- `backends/`: `demo.py` (fake media, no GPU), `comfy_pool.py` (existing ComfyUI or self-healing RunPod pods), `serverless_backend.py`
- `infra.py`: automatic RunPod setup (network volume via REST, model ledger + seed pod, image choice, endpoint); `Factory.provision` runs it before paid batches
- `registry.py`: `workflows/<name>/{workflow_api.json, manifest.toml}`; typed and derived inputs, version hash, `check_against_server`
- `factory/`: `writer.py` (Claude structured output), `matrix.py`, `recipes/{ugc,product,clip}.py`, `voice.py`, `captions.py`, `assembly.py` (ffmpeg), `qa.py`, `export.py`, `service.py`
- Legacy CSV pipeline: `runner.py` (pods), `serverless.py`, `jobs.py`, `post.py`

To make ads, follow `.claude/skills/run-comfy-batch/SKILL.md`. Show the estimate and get the user's OK before
rendering on `serverless` or `pod`; `demo` is free.

For the CSV pipeline: edit `jobs.csv`, run `python -m run_comfy validate`, then `python -m run_comfy run`,
then read `output/report.csv`. Finished jobs are skipped on rerun. If a run was killed, run
`python -m run_comfy cleanup`.

Rules:
- Never raise budgets (`max_spend_usd`, `max_runtime_min`), switch to a paid backend, run `setup` /
  `serverless-setup` / `seed-volume` (they create billable RunPod resources), or put API keys anywhere but `.env`
  without asking. Use `setup --dry-run` to show the plan first.
- Workflow graphs are `verified = false` until `workflows check --url` passes on a real GPU. Don't claim otherwise.
- After code changes: run `pytest`. If `web/` changed: `cd web && npm run build` (output is committed in `run_comfy/web_dist`).
- Tests set `RUN_COMFY_X264_PRESET=ultrafast` (tests/conftest.py). Real renders use `medium`.
