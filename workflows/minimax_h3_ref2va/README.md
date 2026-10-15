# MiniMax H3 ref2va (Singularity v1.3 int8)

`workflow_ui.json` — the original ComfyUI workflow (editor format), kept as-is.

To make it runnable by run-comfy, add next to it:
- `workflow_api.json` — the same graph exported from ComfyUI with **Workflow → Export (API)**
- `manifest.toml` — typed inputs (prompt, reference images, duration), models and custom nodes

Until `workflow_api.json` exists, the registry ignores this folder.

Notes from the graph:
- Models: `Minimax-h3_Singularity_ref2va_v1.3_int8.safetensors`, `qwen3vl_32b_minimax_h3_int8_convrot.safetensors`,
  `minimax_h3_video_vae_int8_convrot.safetensors`, `minimax_h3_audio_vae_fp32.safetensors`,
  `minimax_h3_latent_upscaler_3d_fp16.safetensors`
- Custom nodes: KJNodes (Set/Get), ComfyUI-Easy-Use, VideoHelperSuite, a ResolutionSelector node, `忽略多组孤海`
- Defaults: 9:16 at 0.5 MP, 6 steps (beta), 1.5x latent upscale, 21 s at 24 fps, video + audio in one pass
