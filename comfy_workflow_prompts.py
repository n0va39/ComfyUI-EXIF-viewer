from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any


TEXT_INPUT_KEYS = (
    "caption",
    "prompt",
    "text",
    "text_a",
    "text_b",
    "positive",
    "negative",
    "string",
    "value",
)

IGNORED_STRING_KEYS = {
    "ckpt_name",
    "control_net_name",
    "filename_prefix",
    "image",
    "lora_name",
    "model_name",
    "vae_name",
}


@dataclass(frozen=True)
class PromptGuess:
    positive: str
    negative: str
    details: str
    warnings: tuple[str, ...] = ()


@dataclass
class WorkflowDocs:
    api: dict[str, Any]
    ui: dict[str, Any]


def infer_workflow_prompts(
    workflow_json: str,
    prompt_json: str,
    mode: str,
    positive_node_ids: str = "",
    negative_node_ids: str = "",
    delimiter: str = "\n",
) -> PromptGuess:
    docs, warnings = _load_docs(workflow_json, prompt_json)
    if not docs.api and not docs.ui:
        return PromptGuess("", "", "No workflow or prompt JSON found.", tuple(warnings))

    if mode == "manual":
        guess = _infer_manual(docs, positive_node_ids, negative_node_ids, delimiter)
    else:
        guess = _infer_auto_clip(docs, delimiter)

    merged_warnings = tuple([*warnings, *guess.warnings])
    return PromptGuess(guess.positive, guess.negative, guess.details, merged_warnings)


def split_node_ids(value: str) -> list[str]:
    return [part for part in re.split(r"[\s,;]+", value.strip()) if part]


def decode_delimiter(value: str) -> str:
    if value == "":
        return ""
    return value.encode("utf-8").decode("unicode_escape")


def _load_docs(workflow_json: str, prompt_json: str) -> tuple[WorkflowDocs, list[str]]:
    warnings: list[str] = []
    api = _load_json_dict(prompt_json, "prompt", warnings)
    ui = _load_json_dict(workflow_json, "workflow", warnings)
    return WorkflowDocs(api=api, ui=ui), warnings


def _load_json_dict(value: str, label: str, warnings: list[str]) -> dict[str, Any]:
    if not value.strip():
        return {}
    try:
        loaded = json.loads(value)
    except json.JSONDecodeError as exc:
        warnings.append(f"Failed to parse {label} JSON: {exc}")
        return {}
    if isinstance(loaded, dict):
        return loaded
    warnings.append(f"{label} JSON is not an object.")
    return {}


def _infer_manual(
    docs: WorkflowDocs,
    positive_node_ids: str,
    negative_node_ids: str,
    delimiter: str,
) -> PromptGuess:
    positive_ids = split_node_ids(positive_node_ids)
    negative_ids = split_node_ids(negative_node_ids)
    warnings: list[str] = []
    positive = _concat_manual_nodes(docs, positive_ids, delimiter, warnings)
    negative = _concat_manual_nodes(docs, negative_ids, delimiter, warnings)
    details = [
        "Mode: manual node selection",
        f"Positive node IDs: {', '.join(positive_ids) or '-'}",
        f"Negative node IDs: {', '.join(negative_ids) or '-'}",
    ]
    return PromptGuess(positive, negative, "\n".join(details), tuple(warnings))


def _concat_manual_nodes(
    docs: WorkflowDocs,
    node_ids: list[str],
    delimiter: str,
    warnings: list[str],
) -> str:
    values: list[str] = []
    for node_id in node_ids:
        text = _api_node_text(docs.api, node_id, set())
        if not text:
            text = _ui_node_text(docs.ui, node_id, set())
        if text:
            values.append(text)
        else:
            warnings.append(f"No text found for node {node_id}.")
    return delimiter.join(value for value in values if value)


def _infer_auto_clip(docs: WorkflowDocs, delimiter: str) -> PromptGuess:
    api_guess = _infer_auto_clip_api(docs.api, delimiter) if docs.api else None
    ui_guess = _infer_auto_clip_ui(docs.ui, delimiter) if docs.ui else None

    positive = ""
    negative = ""
    detail_parts = ["Mode: automatic CLIP trace"]
    warnings: list[str] = []

    if api_guess and (api_guess.positive or api_guess.negative):
        positive = api_guess.positive
        negative = api_guess.negative
        detail_parts.append(api_guess.details)
        warnings.extend(api_guess.warnings)
    elif ui_guess and (ui_guess.positive or ui_guess.negative):
        positive = ui_guess.positive
        negative = ui_guess.negative
        detail_parts.append(ui_guess.details)
        warnings.extend(ui_guess.warnings)
    else:
        if api_guess:
            warnings.extend(api_guess.warnings)
        if ui_guess:
            warnings.extend(ui_guess.warnings)
        warnings.append("No CLIPTextEncode prompt path found.")

    return PromptGuess(positive, negative, "\n".join(detail_parts), tuple(warnings))


def _infer_auto_clip_api(api: dict[str, Any], delimiter: str) -> PromptGuess:
    warnings: list[str] = []
    positive_ids: list[str] = []
    negative_ids: list[str] = []

    for node_id, node in api.items():
        if not isinstance(node, dict):
            continue
        inputs = node.get("inputs")
        if not isinstance(inputs, dict):
            continue
        if "positive" in inputs or "negative" in inputs:
            positive_ids.extend(_connected_node_ids(inputs.get("positive")))
            negative_ids.extend(_connected_node_ids(inputs.get("negative")))

    if not positive_ids and not negative_ids:
        clip_ids = [
            str(node_id)
            for node_id, node in api.items()
            if isinstance(node, dict) and _is_clip_text_node(node.get("class_type", ""))
        ]
        if len(clip_ids) == 2:
            positive_ids = [clip_ids[0]]
            negative_ids = [clip_ids[1]]
            warnings.append("No sampler path found; using first two CLIPTextEncode nodes.")
        elif clip_ids:
            positive_ids = clip_ids
            warnings.append("No sampler path found; showing all CLIPTextEncode nodes as positive.")

    positive = _concat_traced_api_nodes(api, positive_ids, delimiter)
    negative = _concat_traced_api_nodes(api, negative_ids, delimiter)
    details = [
        "Source: API prompt graph",
        f"Positive source nodes: {', '.join(_unique(positive_ids)) or '-'}",
        f"Negative source nodes: {', '.join(_unique(negative_ids)) or '-'}",
    ]
    return PromptGuess(positive, negative, "\n".join(details), tuple(warnings))


def _concat_traced_api_nodes(
    api: dict[str, Any],
    node_ids: list[str],
    delimiter: str,
) -> str:
    values = []
    for node_id in _unique(node_ids):
        text = _trace_api_conditioning_to_text(api, node_id, set())
        if text:
            values.append(text)
    return delimiter.join(values)


def _trace_api_conditioning_to_text(
    api: dict[str, Any],
    node_id: str,
    visited: set[str],
) -> str:
    node_id = str(node_id)
    if node_id in visited:
        return ""
    visited.add(node_id)
    node = api.get(node_id)
    if not isinstance(node, dict):
        return ""

    if _is_clip_text_node(node.get("class_type", "")):
        return _api_node_text(api, node_id, visited)

    inputs = node.get("inputs")
    if isinstance(inputs, dict):
        for key in ("conditioning", "positive", "negative", "text"):
            for source_id in _connected_node_ids(inputs.get(key)):
                text = _trace_api_conditioning_to_text(api, source_id, visited)
                if text:
                    return text
    return _api_node_text(api, node_id, visited)


def _api_node_text(api: dict[str, Any], node_id: str, visited: set[str]) -> str:
    node = api.get(str(node_id))
    if not isinstance(node, dict):
        return ""
    inputs = node.get("inputs")
    if not isinstance(inputs, dict):
        return ""

    ordered_keys = [key for key in TEXT_INPUT_KEYS if key in inputs]
    ordered_keys.extend(
        key
        for key, value in inputs.items()
        if key not in ordered_keys
        and key.lower() not in IGNORED_STRING_KEYS
        and _input_contains_text(value)
    )

    values: list[str] = []
    for key in ordered_keys:
        text = _api_input_text(api, inputs.get(key), visited)
        if text:
            values.append(text)
    return "\n".join(_unique(values))


def _api_input_text(api: dict[str, Any], value: Any, visited: set[str]) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        connected = _connected_node_ids(value)
        if connected:
            return "\n".join(
                text
                for source_id in connected
                if (text := _api_node_text(api, source_id, visited))
            )
        return "\n".join(str(item).strip() for item in value if isinstance(item, str))
    if isinstance(value, dict):
        return "\n".join(
            text
            for item in value.values()
            if (text := _api_input_text(api, item, visited))
        )
    return ""


def _input_contains_text(value: Any) -> bool:
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, list):
        return any(isinstance(item, str) and item.strip() for item in value)
    if isinstance(value, dict):
        return any(_input_contains_text(item) for item in value.values())
    return False


def _connected_node_ids(value: Any) -> list[str]:
    if (
        isinstance(value, list)
        and len(value) >= 2
        and isinstance(value[0], (str, int))
        and isinstance(value[1], int)
    ):
        return [str(value[0])]
    return []


def _infer_auto_clip_ui(ui: dict[str, Any], delimiter: str) -> PromptGuess:
    warnings: list[str] = []
    nodes = _ui_nodes_by_id(ui)
    link_sources = _ui_link_sources(ui)
    positive_ids: list[str] = []
    negative_ids: list[str] = []

    for node_id, node in nodes.items():
        input_names = _ui_input_names(node)
        node_type = str(node.get("type") or node.get("class_type") or "")
        if "sampler" not in node_type.lower() and not {"positive", "negative"} & input_names:
            continue
        positive_ids.extend(_ui_source_node_ids(node, "positive", link_sources))
        negative_ids.extend(_ui_source_node_ids(node, "negative", link_sources))

    if not positive_ids and not negative_ids:
        clip_ids = [
            node_id
            for node_id, node in nodes.items()
            if _is_clip_text_node(str(node.get("type") or node.get("class_type") or ""))
        ]
        if len(clip_ids) == 2:
            positive_ids = [clip_ids[0]]
            negative_ids = [clip_ids[1]]
            warnings.append("No UI sampler path found; using first two CLIPTextEncode nodes.")
        elif clip_ids:
            positive_ids = clip_ids
            warnings.append("No UI sampler path found; showing all CLIPTextEncode nodes as positive.")

    positive = _concat_traced_ui_nodes(ui, positive_ids, delimiter)
    negative = _concat_traced_ui_nodes(ui, negative_ids, delimiter)
    details = [
        "Source: UI workflow graph",
        f"Positive source nodes: {', '.join(_unique(positive_ids)) or '-'}",
        f"Negative source nodes: {', '.join(_unique(negative_ids)) or '-'}",
    ]
    return PromptGuess(positive, negative, "\n".join(details), tuple(warnings))


def _concat_traced_ui_nodes(
    ui: dict[str, Any],
    node_ids: list[str],
    delimiter: str,
) -> str:
    values = []
    for node_id in _unique(node_ids):
        text = _trace_ui_conditioning_to_text(ui, node_id, set())
        if text:
            values.append(text)
    return delimiter.join(values)


def _trace_ui_conditioning_to_text(
    ui: dict[str, Any],
    node_id: str,
    visited: set[str],
) -> str:
    nodes = _ui_nodes_by_id(ui)
    link_sources = _ui_link_sources(ui)
    node_id = str(node_id)
    if node_id in visited:
        return ""
    visited.add(node_id)
    node = nodes.get(node_id)
    if not node:
        return ""
    node_type = str(node.get("type") or node.get("class_type") or "")

    if _is_clip_text_node(node_type):
        return _ui_node_text(ui, node_id, visited)

    for input_name in ("conditioning", "positive", "negative", "text"):
        for source_id in _ui_source_node_ids(node, input_name, link_sources):
            text = _trace_ui_conditioning_to_text(ui, source_id, visited)
            if text:
                return text
    return _ui_node_text(ui, node_id, visited)


def _ui_node_text(ui: dict[str, Any], node_id: str, visited: set[str]) -> str:
    nodes = _ui_nodes_by_id(ui)
    link_sources = _ui_link_sources(ui)
    node = nodes.get(str(node_id))
    if not node:
        return ""

    values: list[str] = []
    for input_name in ("text", "prompt", "string", "value"):
        for source_id in _ui_source_node_ids(node, input_name, link_sources):
            text = _trace_ui_conditioning_to_text(ui, source_id, visited)
            if text:
                values.append(text)

    widgets = node.get("widgets_values")
    if isinstance(widgets, list):
        values.extend(item.strip() for item in widgets if isinstance(item, str) and item.strip())
    elif isinstance(widgets, dict):
        for key, value in widgets.items():
            if key.lower() in TEXT_INPUT_KEYS and isinstance(value, str) and value.strip():
                values.append(value.strip())

    properties = node.get("properties")
    if isinstance(properties, dict):
        for key, value in properties.items():
            if key.lower() in TEXT_INPUT_KEYS and isinstance(value, str) and value.strip():
                values.append(value.strip())

    return "\n".join(_unique(values))


def _ui_nodes_by_id(ui: dict[str, Any]) -> dict[str, dict[str, Any]]:
    nodes = ui.get("nodes")
    if not isinstance(nodes, list):
        return {}
    return {
        str(node.get("id")): node
        for node in nodes
        if isinstance(node, dict) and node.get("id") is not None
    }


def _ui_link_sources(ui: dict[str, Any]) -> dict[int, str]:
    links = ui.get("links")
    if not isinstance(links, list):
        return {}
    sources: dict[int, str] = {}
    for link in links:
        if isinstance(link, list) and len(link) >= 3:
            try:
                sources[int(link[0])] = str(link[1])
            except (TypeError, ValueError):
                continue
    return sources


def _ui_input_names(node: dict[str, Any]) -> set[str]:
    inputs = node.get("inputs")
    if not isinstance(inputs, list):
        return set()
    return {
        str(item.get("name")).lower()
        for item in inputs
        if isinstance(item, dict) and item.get("name") is not None
    }


def _ui_source_node_ids(
    node: dict[str, Any],
    input_name: str,
    link_sources: dict[int, str],
) -> list[str]:
    inputs = node.get("inputs")
    if not isinstance(inputs, list):
        return []
    source_ids: list[str] = []
    for item in inputs:
        if not isinstance(item, dict) or str(item.get("name")).lower() != input_name:
            continue
        link_id = item.get("link")
        if link_id is None:
            continue
        try:
            source_id = link_sources.get(int(link_id))
        except (TypeError, ValueError):
            source_id = None
        if source_id:
            source_ids.append(source_id)
    return source_ids


def _is_clip_text_node(value: Any) -> bool:
    return "cliptextencode" in str(value).lower()


def _unique(values: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value not in seen:
            result.append(value)
            seen.add(value)
    return result
