"""FLUX 3 Image Klein API graphs derived from the user's exported edit workflow.

``workflows/flux3_klein_edit_api.json`` is a region-edit graph (replace an
earring): LoadImage -> ReferenceMultiLatent supplies conditioning and the
sampler latent, a bounding-box layout prompt drives the edit, and a 9B prompt
upsampler rewrites the text. Two graphs are derived from it:

* t2i  — UNET/CLIP/VAE loaders, CLIPTextEncode(raw prompt), ConditioningZeroOut
          negative, EmptyFlux2LatentImage (reconnected; disconnected in the
          export) -> KSampler -> VAEDecode -> SaveImage.
* ref  — the same, with conditioning through ReferenceMultiLatent fed by up to
          4 LoadImage nodes (characters first, then an optional location
          reference). The sampler latent is configurable: ``reference`` (as in
          the export) or ``empty_16x9``.

Layout, compare/preview and upsampler nodes are dropped; the upsampler can be
re-attached with ``IMAGEGEN_PROMPT_UPSAMPLER``. Every graph is checked against
the live ``/object_info`` before it is queued.
"""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS = ROOT / "workflows"
EDIT_TEMPLATE = WORKFLOWS / "flux3_klein_edit_api.json"
T2I_TEMPLATE = WORKFLOWS / "flux3_klein_t2i_api.json"
REF_TEMPLATE = WORKFLOWS / "flux3_klein_ref_api.json"
MAP_FILE = WORKFLOWS / "flux3_klein_map.json"

MAX_REFERENCES = 4
REFERENCE_NODES = ("11", "12", "13", "14")
DEFAULT_SIZE = (1280, 720)
LATENT_SOURCES = ("reference", "empty_16x9")
_LOADERS = ("1", "2", "3")
_UPSAMPLER = ("46", "47", "48")
_CHECKED_OPTIONS = {"unet_name", "clip_name", "vae_name", "sampler_name", "scheduler", "type"}


class TemplateError(RuntimeError):
    """A derived graph does not match the installed ComfyUI nodes."""


def load_edit_template(path=EDIT_TEMPLATE) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _node(edit: dict, node_id: str) -> dict:
    if node_id not in edit:
        raise TemplateError(f"the exported workflow has no node {node_id}; re-export it from ComfyUI")
    return copy.deepcopy(edit[node_id])


def _meta(title: str) -> dict:
    return {"title": title}


def derive_t2i(edit: dict) -> dict:
    graph = {node_id: _node(edit, node_id) for node_id in _LOADERS}
    encode = _node(edit, "4")
    encode["inputs"] = {"text": "", "clip": ["2", 0]}
    graph["4"] = encode
    graph["32"] = {"class_type": "ConditioningZeroOut", "inputs": {"conditioning": ["4", 0]},
                   "_meta": _meta("Negative (zeroed)")}
    latent = _node(edit, "44")
    latent["inputs"] = {"width": DEFAULT_SIZE[0], "height": DEFAULT_SIZE[1], "batch_size": 1}
    graph["44"] = latent
    sampler = _node(edit, "7")
    sampler["inputs"].update({"positive": ["4", 0], "negative": ["32", 0],
                              "latent_image": ["44", 0], "seed": 0})
    graph["7"] = sampler
    graph["8"] = _node(edit, "8")
    save = _node(edit, "9")
    save["inputs"]["filename_prefix"] = "private_gravity"
    graph["9"] = save
    return graph


def derive_ref(edit: dict) -> dict:
    graph = derive_t2i(edit)
    reference = _node(edit, "17")
    inputs = {key: value for key, value in reference["inputs"].items()
              if not key.startswith("images.")}
    inputs.update({"positive": ["4", 0], "negative": ["32", 0], "vae": ["3", 0]})
    for index, node_id in enumerate(REFERENCE_NODES):
        loader = _node(edit, "11")
        loader["inputs"] = {"image": f"reference_{index}.png"}
        loader["_meta"] = _meta(f"Reference {index}")
        graph[node_id] = loader
        inputs[f"images.image{index}"] = [node_id, 0]
    reference["inputs"] = inputs
    graph["17"] = reference
    graph["7"]["inputs"].update({"positive": ["17", 0], "negative": ["17", 1],
                                 "latent_image": ["17", 2]})
    return graph


def default_map() -> dict:
    return {
        "prompt": ["4", "text"],
        "seed": ["7", "seed"],
        "steps": ["7", "steps"],
        "width": ["44", "width"],
        "height": ["44", "height"],
        "filename_prefix": ["9", "filename_prefix"],
        "models": {"unet": ["1", "unet_name"], "text_encoder": ["2", "clip_name"],
                   "vae": ["3", "vae_name"]},
        "references": [[node_id, "image"] for node_id in REFERENCE_NODES],
        "reference_input": ["17", "images.image{index}"],
        "latent_input": ["7", "latent_image"],
        "latent_sources": {"reference": ["17", 2], "empty_16x9": ["44", 0]},
        "output_node": "9",
    }


def write_templates(edit_path=EDIT_TEMPLATE, folder=WORKFLOWS) -> dict:
    """Derive and save the t2i/ref templates and the placeholder map."""
    edit = load_edit_template(edit_path)
    folder = Path(folder)
    files = {
        folder / T2I_TEMPLATE.name: derive_t2i(edit),
        folder / REF_TEMPLATE.name: derive_ref(edit),
        folder / MAP_FILE.name: default_map(),
    }
    for path, value in files.items():
        path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return {path.name: value for path, value in files.items()}


def load_templates(folder=WORKFLOWS) -> tuple[dict, dict, dict]:
    folder = Path(folder)
    if not (folder / T2I_TEMPLATE.name).exists():
        write_templates(folder=folder)
    read = lambda name: json.loads((folder / name).read_text(encoding="utf-8"))
    return read(T2I_TEMPLATE.name), read(REF_TEMPLATE.name), read(MAP_FILE.name)


# ── Region edit (Phase 3.5-D) ───────────────────────────────────────────────
# The user's edit export, kept as an editor: the shot is <ref_image_0>, QA boxes
# become <obj_N> through CreateBoundingBoxes → Flux3LayoutPrompt, up to 3 extra
# references (approved portraits for identity fixes) follow, the latent comes
# from the shot (16:9 kept) and the prompt upsampler is bypassed. With no box
# the same graph runs as an instruction-only edit.

REGION_EDIT_TEMPLATE = WORKFLOWS / "flux3_klein_regionedit_api.json"
REGION_CANVAS = (1360, 768)                 # CreateBoundingBoxes needs multiples of 16; ~16:9


def derive_region_edit(edit: dict) -> dict:
    graph = {node_id: _node(edit, node_id) for node_id in _LOADERS}
    for index, node_id in enumerate(REFERENCE_NODES):
        loader = _node(edit, "11")
        loader["inputs"] = {"image": "shot.png" if index == 0 else f"reference_{index}.png"}
        loader["_meta"] = _meta("Shot (<ref_image_0>)" if index == 0 else f"Reference {index}")
        graph[node_id] = loader
    boxes = _node(edit, "33")
    boxes["inputs"] = {"width": REGION_CANVAS[0], "height": REGION_CANVAS[1], "editor_state": {"__value__": []},
                       "background": ["11", 0]}
    graph["33"] = boxes
    layout = _node(edit, "49")
    layout["inputs"] = {"prompt": "", "element": ["33", 2]}
    graph["49"] = layout
    encode = _node(edit, "4")
    encode["inputs"] = {"text": ["49", 0], "clip": ["2", 0]}
    graph["4"] = encode
    graph["32"] = _node(edit, "32")
    reference = _node(edit, "17")
    inputs = {key: value for key, value in reference["inputs"].items() if not key.startswith("images.")}
    inputs.update({"positive": ["4", 0], "negative": ["32", 0], "vae": ["3", 0]})
    inputs.update({f"images.image{index}": [node_id, 0] for index, node_id in enumerate(REFERENCE_NODES)})
    reference["inputs"] = inputs
    graph["17"] = reference
    sampler = _node(edit, "7")
    sampler["inputs"].update({"positive": ["17", 0], "negative": ["17", 1], "latent_image": ["17", 2], "seed": 0})
    graph["7"] = sampler
    graph["8"] = _node(edit, "8")
    save = _node(edit, "9")
    save["inputs"]["filename_prefix"] = "private_gravity_edit"
    graph["9"] = save
    return graph


def load_region_edit(folder=WORKFLOWS, edit_path=EDIT_TEMPLATE) -> dict:
    path = Path(folder) / REGION_EDIT_TEMPLATE.name
    if not path.exists():
        path.write_text(json.dumps(derive_region_edit(load_edit_template(edit_path)), indent=2,
                                   ensure_ascii=False) + "\n", encoding="utf-8")
    return json.loads(path.read_text(encoding="utf-8"))


def region_canvas(source_size=None, pixels: int = 1024 * 1024) -> tuple[int, int]:
    """The box canvas for a source image: its ratio at ~1 MP in multiples of 16, the size the
    reference latent ("resize", 1 MP) gives the edit (1920x1080 -> 1360x768)."""
    if not source_size or not all(source_size):
        return REGION_CANVAS
    width, height = source_size
    scale = (pixels / (width * height)) ** 0.5
    return max(16, round(width * scale / 16) * 16), max(16, round(height * scale / 16) * 16)


def build_region_edit(*, instruction: str, seed: int, shot: str, references: list[str] = (),
                      boxes: list[tuple] = (), steps: int | None = None, source_size=None,
                      filename_prefix: str = "private_gravity_edit", folder=WORKFLOWS) -> dict:
    """The region-edit graph: ``shot`` and ``references`` are uploaded image names,
    ``boxes`` [(normalized [x0, y0, x1, y1], short description)] in <obj_1>, <obj_2>… order.
    The box canvas follows ``source_size`` (the shot's pixels); the output size follows the
    shot through the reference latent."""
    if len(references) > MAX_REFERENCES - 1:
        raise TemplateError(f"a region edit takes the shot plus at most {MAX_REFERENCES - 1} references")
    graph = copy.deepcopy(load_region_edit(folder))
    names = [shot, *references]
    for index, node_id in enumerate(REFERENCE_NODES):
        if index < len(names):
            graph[node_id]["inputs"]["image"] = names[index]
        else:
            graph.pop(node_id, None)
            graph["17"]["inputs"].pop(f"images.image{index}", None)
    width, height = region_canvas(source_size)
    graph["33"]["inputs"].update(width=width, height=height)
    graph["33"]["inputs"]["editor_state"] = {"__value__": [
        {"x": int(round(box[0] * width)), "y": int(round(box[1] * height)),
         "width": max(16, int(round((box[2] - box[0]) * width))),
         "height": max(16, int(round((box[3] - box[1]) * height))),
         "metadata": {"type": "obj", "text": "", "desc": str(desc)[:80], "palette": []}}
        for box, desc in boxes]}
    graph["49"]["inputs"]["prompt"] = instruction
    graph["7"]["inputs"]["seed"] = int(seed) % (2 ** 63)
    if steps:
        graph["7"]["inputs"]["steps"] = int(steps)
    graph["9"]["inputs"]["filename_prefix"] = filename_prefix
    return graph


def build_masked_restore(*, prompt: str, seed: int, image: str, mask: str, denoise: float, references: list[str] = (),
                         width: int = DEFAULT_SIZE[0], height: int = DEFAULT_SIZE[1], steps: int | None = None,
                         filename_prefix: str = "private_gravity", folder=WORKFLOWS, differential: bool = False) -> dict:
    """Klein on a crop, inside the mask only (SetLatentNoiseMask). With ``differential`` the model goes through
    DifferentialDiffusion, so a soft mask (full inside, a ring falling off outside) blends the repaint (Phase 3.9.6)."""
    graph = build_graph("ref" if references else "t2i", prompt=prompt, seed=seed, width=width, height=height, steps=steps,
                        references=list(references), latent_source="reference", filename_prefix=filename_prefix, folder=folder)
    graph.pop("44", None)
    graph["61"] = {"class_type": "LoadImage", "inputs": {"image": image}, "_meta": _meta("Erased crop")}
    graph["62"] = {"class_type": "VAEEncode", "inputs": {"pixels": ["61", 0], "vae": ["3", 0]}}
    graph["63"] = {"class_type": "LoadImageMask", "inputs": {"image": mask, "channel": "red"}, "_meta": _meta("Mask")}
    graph["64"] = {"class_type": "SetLatentNoiseMask", "inputs": {"samples": ["62", 0], "mask": ["63", 0]}}
    graph["7"]["inputs"].update(latent_image=["64", 0], denoise=float(denoise))
    if differential:
        graph["65"] = {"class_type": "DifferentialDiffusion", "inputs": {"model": graph["7"]["inputs"]["model"]}}
        graph["7"]["inputs"]["model"] = ["65", 0]
    return graph


def graph_hash(graph: dict) -> str:
    return hashlib.sha256(json.dumps(graph, sort_keys=True).encode("utf-8")).hexdigest()


def model_names(graph: dict, mapping: dict) -> dict:
    return {name: graph[node]["inputs"].get(field)
            for name, (node, field) in mapping["models"].items() if node in graph}


def _set(graph: dict, target, value):
    node, field = target
    graph[node]["inputs"][field] = value


def build_graph(kind: str, *, prompt: str, seed: int, width: int = DEFAULT_SIZE[0],
                height: int = DEFAULT_SIZE[1], steps: int | None = None,
                references: list[str] = (), latent_source: str = "reference",
                upsampler: bool = False, filename_prefix: str = "private_gravity",
                folder=WORKFLOWS, edit_path=EDIT_TEMPLATE) -> dict:
    """A queue-ready API graph for one image.

    ``references`` are image names already uploaded to ComfyUI, in prompt
    order. With no references the t2i graph is used whatever ``kind`` says.
    """
    if len(references) > MAX_REFERENCES:
        raise TemplateError(f"at most {MAX_REFERENCES} reference images are supported")
    if latent_source not in LATENT_SOURCES:
        raise TemplateError(f"latent_source must be one of {LATENT_SOURCES}")
    t2i, ref, mapping = load_templates(folder)
    use_ref = kind == "ref" and references
    graph = copy.deepcopy(ref if use_ref else t2i)
    _set(graph, mapping["prompt"], prompt)
    _set(graph, mapping["seed"], int(seed) % (2 ** 63))
    _set(graph, mapping["width"], int(width))
    _set(graph, mapping["height"], int(height))
    _set(graph, mapping["filename_prefix"], filename_prefix)
    if steps:
        _set(graph, mapping["steps"], int(steps))
    if use_ref:
        ref_node, pattern = mapping["reference_input"]
        for index, (node, field) in enumerate(mapping["references"]):
            if index < len(references):
                graph[node]["inputs"][field] = references[index]
            else:
                graph.pop(node, None)
                graph[ref_node]["inputs"].pop(pattern.format(index=index), None)
        graph[mapping["latent_input"][0]]["inputs"][mapping["latent_input"][1]] = \
            list(mapping["latent_sources"][latent_source])
        if latent_source == "reference":
            graph.pop("44", None)
    if upsampler:
        _attach_upsampler(graph, prompt, load_edit_template(edit_path),
                          references if use_ref else [], mapping)
    return graph


def _attach_upsampler(graph: dict, prompt: str, edit: dict, references, mapping) -> None:
    """Opt-in: route the prompt through the export's TextGenerate upsampler."""
    for node_id in _UPSAMPLER:
        graph[node_id] = _node(edit, node_id)
    generate = graph["47"]["inputs"]
    generate["prompt"] = prompt
    generate["clip"] = ["46", 0]
    generate["system_prompt"] = ["48", 0]
    if references:
        batch = _node(edit, "50")
        batch["inputs"] = {f"images.image{index}": [node, 0] for index, (node, _) in
                           enumerate(mapping["references"][:len(references)])}
        graph["50"] = batch
        generate["image"] = ["50", 0]
    else:
        generate.pop("image", None)
    graph["4"]["inputs"]["text"] = ["47", 0]


def _input_spec(object_info: dict, class_type: str) -> tuple[dict, set[str]]:
    info = object_info.get(class_type) or {}
    sections = info.get("input") or {}
    required = sections.get("required") or {}
    optional = sections.get("optional") or {}
    return {**required, **optional}, set(required)


def _options(spec) -> list | None:
    if isinstance(spec, (list, tuple)) and spec:
        if isinstance(spec[0], list):
            return spec[0]
        if spec[0] == "COMBO" and len(spec) > 1 and isinstance(spec[1], dict):
            return spec[1].get("options")
    return None


def validate_graph(graph: dict, object_info: dict) -> list[str]:
    """Problems with node classes, input names, required inputs and model files."""
    problems = []
    for node_id, node in graph.items():
        class_type = node.get("class_type")
        if class_type not in object_info:
            problems.append(f"node {node_id}: class {class_type!r} is not installed in ComfyUI")
            continue
        specs, required = _input_spec(object_info, class_type)
        prefixes = {name.split(".")[0] for name in specs}
        for name, value in node.get("inputs", {}).items():
            known = name in specs or name.split(".")[0] in prefixes or "." in name and \
                any(spec.startswith(name.split(".")[0]) for spec in specs)
            if not known:
                problems.append(f"node {node_id} ({class_type}): unknown input {name!r}")
                continue
            # Only model files and sampler choices are checked: LoadImage lists the
            # files present when /object_info was read, not ones uploaded later.
            options = _options(specs.get(name)) if name in _CHECKED_OPTIONS else None
            if options is not None and not isinstance(value, list) and value not in options:
                problems.append(f"node {node_id} ({class_type}): {name}={value!r} is not available "
                                f"(e.g. a missing model file)")
        missing = [name for name in required if name not in node.get("inputs", {})
                   and not any(key.startswith(name + ".") for key in node.get("inputs", {}))]
        if missing:
            problems.append(f"node {node_id} ({class_type}): missing required inputs {missing}")
        for value in node.get("inputs", {}).values():
            if isinstance(value, list) and len(value) == 2 and isinstance(value[0], str) \
                    and value[0] not in graph:
                problems.append(f"node {node_id} ({class_type}): links to missing node {value[0]}")
    return problems
