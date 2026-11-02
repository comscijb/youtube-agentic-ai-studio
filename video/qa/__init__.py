"""Layered, local-first visual QA of generated shots (Phase 3.5).

* ``base`` — the result schema (people count, issues with boxes, verdict) and the
  VLM backend interface;
* ``deterministic`` — layer 1 on the CPU: person/face detection (OpenCV DNN),
  duplicate faces, headcount, extra people, borders/panels, optional OCR;
* ``focused`` — the short yes/no questions layer 2 asks about crops, and how
  their answers become issues (the VLM classifies, it never locates);
* ``comfy_vlm`` / ``ollama_vlm`` / ``gemini_vlm`` — layer-2/3 backends;
* ``layered`` — runs the configured layers on one image or a batch.
"""
