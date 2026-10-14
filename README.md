# bulkgen — bulk ComfyUI generation on ephemeral RunPod GPUs

One command: pods up → every CSV row rendered → files downloaded → pods **always** terminated.

```bash
python -m bulkgen validate     # free: checks workflow + CSV
python -m bulkgen run          # spends money, then tears everything down
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
6. Test once on a single row: `python -m bulkgen run` with a 1-line CSV.

After that, a batch is: edit `jobs.csv` → `python -m bulkgen run`.

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
| Outputs | `output/<id>.<ext>`, `report.csv`, `run.log`; optional ffmpeg stitch + loudnorm |

Exit codes: `0` all done, `2` some failed/not run (see `report.csv`), `130` interrupted.

## Other commands

```bash
python -m bulkgen gpus                        # valid gpu_types ids
python -m bulkgen cleanup                     # kill any bulkgen-* pods still running
python -m bulkgen run --url http://host:8188  # use an existing ComfyUI (no pods created)
pytest                                        # simulated ComfyUI + RunPod, no money spent
```
