---
name: run-comfy-batch
description: Create, render, review and export AI ad batches with run-comfy (UGC talking heads, product statics, short-form clips on RunPod). Use when the user asks to make ads, variations, hooks, creatives, a batch, or to check on / export one.
---

# Running an ad batch with run-comfy

Prefer the `run-comfy` MCP tools when they are connected; otherwise use the CLI
(`python -m run_comfy batch ...`). Both drive the same factory and database as the dashboard
(`python -m run_comfy serve`, http://127.0.0.1:8000).

## Flow

1. **Brand.** `list_brands`. If none fits, `save_brand` with the product line, audience, the
   *approved facts* (only these claims may appear) and *banned* words. Ask the user for facts rather
   than inventing them.
2. **Create + plan.** `create_batch` with recipe `ugc` (presenter talks to camera), `product`
   (static images; needs a product photo) or `clip` (generated scenes + VO). Pass local image paths.
   It returns Claude-written hooks, scripts, CTAs and prompts. Show the hooks to the user; edit with
   `update_plan` if they want changes.
3. **Matrix + estimate.** `build_matrix` (default `pairwise`) returns variant codes and an estimate:
   GPU renders per workflow, $ cost, savings from stage reuse, warnings (non-commercial model
   licences, workflows not yet verified on a GPU) and `setup`: what the first paid run will create
   (network volume + monthly storage cost, model GB to download, endpoint). **Before rendering on a
   paid backend (`serverless`, `pod`), show the estimate including setup and get an explicit OK.**
   `demo` is free. `setup_infra` (dry_run first) can provision ahead of time.
4. **Render.** `run_batch` (wait=false), then poll `batch_status` every ~30 s. Report progress,
   spend vs budget, and failures with their error text.
5. **Review.** Look at QA scores/issues in `batch_status`. Suggest `regenerate_variant` for a weak
   stage (e.g. `avatar`, `talk`, `broll`, `scene`) - only that stage re-renders. Record the user's
   picks with `review_variant`.
6. **Export.** `export_batch` (approved only by default) gives a zip named by variant code plus
   `manifest.csv`, ready for Meta/TikTok bulk upload and split-test attribution.

## Rules

- Never raise a batch budget or switch to a paid backend without the user asking.
- Variant codes (`H2-S1-A3-V1`) encode the choices: Hook, Script, CTA, Avatar/presenter, Voice,
  B=scene style, L=headline. Use them when reporting results.
- Real people as presenters or voices need the user's confirmation that they have consent/rights.
- If a stage fails on a real GPU, run `python -m run_comfy workflows check --url <comfy url>` to
  compare the workflow graph with the installed nodes before retrying.
