# run-comfy — bulk ComfyUI generation on RunPod

One command renders every row of a CSV through a ComfyUI workflow and downloads the results.
Two backends, same CSV, same workflow, same outputs:

| `backend =` | how it works | pick it when |
|---|---|---|
| `"pod"` | rents whole GPUs for the batch, **always** terminates them after | big batches, any custom nodes/image, long videos |
| `"serverless"` | sends jobs to a RunPod Serverless endpoint running [worker-comfyui](https://github.com/runpod-workers/worker-comfyui); scales to zero by itself | small/frequent batches, no boot wait, nothing to clean up |

```bash
python -m run_comfy validate     # free: checks workflow + CSV
python -m run_comfy run          # spends money, then tears everything down
```

## One-time setup (the manual 5%)

1. `python -m venv .venv && .venv/bin/pip install -e .`
2. `cp .env.example .env` and paste your RunPod API key.
3. **Pod image.** In `config.toml` set `template_id` (a RunPod ComfyUI template) or `image`
   (any Docker image that serves ComfyUI on port 8188 with the custom nodes you need).
4. **Models.** Create a RunPod *network volume*, put your checkpoints/LoRAs/Wan/FLUX weights on it
   once, and set `network_volume_id` + `data_center_id`. Without it, every run re-downloads models.
5. **Workflow.** Build it in the ComfyUI UI, enable dev mode, *Save (API Format)*, drop it in
   `workflows/`, and replace the values that change per job with `{{column_name}}`.
6. **Serverless only:** set `[serverless].image` (a worker-comfyui image with your custom nodes)
   and `network_volume_id`, then run `python -m run_comfy serverless-setup` once. It creates the
   endpoint and writes `endpoint_id` into `config.toml`. Set `backend = "serverless"`.
7. Test once on a single row: `python -m run_comfy run` with a 1-line CSV.

After that, a batch is: edit `jobs.csv` → `python -m run_comfy run`.

## Jobs CSV

| column | meaning |
|---|---|
| `id` | optional; output files are named after it (`<id>.mp4`) |
| anything else | fills `{{same_name}}` in the workflow |
| `seed` | blank = random |

- A cell that is a path to a local file (e.g. `faces/anna.png`) is **uploaded to ComfyUI
  automatically** and replaced with the uploaded name — use it for LoadImage / LivePortrait /
  PuLID reference inputs.
- `{{job_id}}` is always available (handy for `filename_prefix`).
- A placeholder that is the whole value (`"seed": "{{seed}}"`) becomes a number when it looks like one.

## What's automated

| | |
|---|---|
| GPU selection | walks `gpu_types` until one has capacity |
| Boot wait | polls ComfyUI until it answers; aborts if the pod dies or other pods already finished |
| Throughput | `pods = N` shares one queue across N GPUs; `inflight_per_pod` keeps each GPU fed |
| Failures | OOM/node errors → VRAM freed and retried; dead pod → job requeued, pod replaced |
| Teardown | on success, error, Ctrl-C, SIGTERM, budget or timeout; atexit fallback; `cleanup` sweeps leftovers |
| Cost guard | `max_spend_usd` and `max_runtime_min` hard stops |
| Resume | `output/state.jsonl` — rerun the same command and finished jobs are skipped (`--fresh` to redo) |
| Serverless | request concurrency cap, per-request execution timeout, in-flight requests cancelled on Ctrl-C/budget, base64 and S3-URL outputs |
| Outputs | `output/<id>.<ext>`, `report.csv`, `run.log`; optional ffmpeg stitch + loudnorm |

Exit codes: `0` all done, `2` some failed/not run (see `report.csv`), `130` interrupted.

## Other commands

```bash
python -m run_comfy gpus                        # valid gpu_types ids
python -m run_comfy cleanup                     # kill run-comfy-* pods, purge the serverless queue
python -m run_comfy run --backend serverless    # override the backend for one run
python -m run_comfy run --url http://host:8188  # use an existing ComfyUI (no pods created)
pytest                                        # simulated ComfyUI + RunPod, no money spent
```

## Serverless notes

- Input images are sent inline as base64; a request is capped at 10 MB, so resize huge reference images.
- Videos: worker-comfyui returns whatever your Save node writes. For large MP4s configure the
  worker's S3 upload (`BUCKET_ENDPOINT_URL` etc. via `[serverless].env`) so outputs come back as
  URLs instead of base64. Check this on your first run.
- `cost_per_sec_usd` is only used for the budget guard — set it to your endpoint's real price.
