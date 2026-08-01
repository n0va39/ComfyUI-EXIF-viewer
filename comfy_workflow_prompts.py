from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any


TEXT_INPUT_KEYS = (
    "caption",
    "prompt",
    "positive_prompt",
    "negative_prompt",
    "text",
    "text_a",
    "text_b",
    "text_g",
    "text_l",
    "text_positive",
    "text_negative",
    "text_pos",
    "text_neg",
    "text_pos_g",
    "text_pos_l",
    "text_neg_g",
    "text_neg_l",
    "string_a",
    "string_b",
    "wildcard_text",
    "populated_text",
    "positive",
    "negative",
    "string",
    "value",
)

POSITIVE_TEXT_INPUT_KEYS = (
    "positive",
    "positive_prompt",
    "text_positive",
    "text_pos",
    "text_pos_g",
    "text_pos_l",
    "text",
    "quality_tags",
    "metadata_prompt",
)

NEGATIVE_TEXT_INPUT_KEYS = (
    "negative",
    "negative_prompt",
    "text_negative",
    "text_neg",
    "text_neg_g",
    "text_neg_l",
    "text_1",
    "negative_quality_tags",
    "metadata_negative_prompt",
)

INACTIVE_UI_NODE_MODES = {2, 4}
UI_BYPASS_MODE = 4

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


@dataclass(frozen=True)
class NodeRef:
    node_id: str
    output_index: int | None = None

    def label(self) -> str:
        if self.output_index is None:
            return self.node_id
        return f"{self.node_id}:{self.output_index}"


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


def describe_workflow_node(
    workflow_json: str,
    prompt_json: str,
    node_id: str,
) -> str:
    docs, warnings = _load_docs(workflow_json, prompt_json)
    node_id = node_id.strip()
    if not node_id:
        return "Node ID is empty."

    lines: list[str] = []
    api_node = docs.api.get(node_id)
    if isinstance(api_node, dict):
        lines.extend(_describe_api_node(docs.api, node_id, api_node))

    ui_nodes = _ui_nodes_by_id(docs.ui)
    ui_node = ui_nodes.get(node_id)
    if isinstance(ui_node, dict):
        if lines:
            lines.append("")
        lines.extend(_describe_ui_node(docs.ui, node_id, ui_node))

    if warnings:
        if lines:
            lines.append("")
        lines.extend(["[warnings]", *warnings])

    if not lines:
        return f"Node {node_id} was not found."
    return "\n".join(lines)


def split_node_ids(value: str) -> list[str]:
    return [part for part in re.split(r"[\s,;]+", value.strip()) if part]


def decode_delimiter(value: str) -> str:
    if value == "":
        return ""
    return value.encode("utf-8").decode("unicode_escape")


def _describe_api_node(
    api: dict[str, Any],
    node_id: str,
    node: dict[str, Any],
) -> list[str]:
    inputs = node.get("inputs")
    extracted = _api_node_text(api, node_id, set())
    lines = [
        "[API prompt node]",
        f"ID: {node_id}",
        f"Class: {node.get('class_type', '-')}",
        f"Title: {node.get('_meta', {}).get('title', '-') if isinstance(node.get('_meta'), dict) else '-'}",
    ]
    if isinstance(inputs, dict):
        lines.append("Inputs:")
        for key, value in inputs.items():
            lines.append(f"- {key}: {_short_value(value)}")
    if extracted:
        lines.extend(["", "[extracted text]", extracted])
    return lines


def _describe_ui_node(
    ui: dict[str, Any],
    node_id: str,
    node: dict[str, Any],
) -> list[str]:
    extracted = _trace_ui_conditioning_to_text(ui, node_id, None, set())
    lines = [
        "[UI workflow node]",
        f"ID: {node_id}",
        f"Type: {node.get('type', '-')}",
        f"Title: {node.get('title', '-')}",
    ]
    inputs = node.get("inputs")
    if isinstance(inputs, list):
        lines.append("Inputs:")
        for item in inputs:
            if isinstance(item, dict):
                lines.append(
                    f"- {item.get('name', '-')}: link={item.get('link', '-')}, type={item.get('type', '-')}"
                )
    widgets = node.get("widgets_values")
    if widgets:
        lines.append(f"Widgets: {_short_value(widgets)}")
    if extracted:
        lines.extend(["", "[extracted text]", extracted])
    return lines


def _short_value(value: Any, limit: int = 240) -> str:
    text = json.dumps(value, ensure_ascii=False) if not isinstance(value, str) else value
    if len(text) > limit:
        return text[: limit - 3] + "..."
    return text


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
            text = _trace_ui_conditioning_to_text(docs.ui, node_id, None, set())
        if text:
            values.append(text)
        else:
            warnings.append(f"No text found for node {node_id}.")
    return delimiter.join(value for value in values if value)


def _infer_auto_clip(docs: WorkflowDocs, delimiter: str) -> PromptGuess:
    api_guess = (
        _infer_auto_clip_api(docs.api, delimiter, docs.ui) if docs.api else None
    )
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


def _infer_auto_clip_api(
    api: dict[str, Any],
    delimiter: str,
    ui: dict[str, Any] | None = None,
) -> PromptGuess:
    warnings: list[str] = []
    positive_refs: list[NodeRef] = []
    negative_refs: list[NodeRef] = []

    sampler_id = _first_api_sampler_id(api)
    if sampler_id:
        node = api.get(sampler_id)
        inputs = node.get("inputs")
        if isinstance(inputs, dict):
            positive_refs.extend(_connected_node_refs(inputs.get("positive")))
            negative_refs.extend(_connected_node_refs(inputs.get("negative")))
    else:
        warnings.append("No sampler node found in API prompt graph.")

    if not positive_refs and not negative_refs:
        clip_ids = [
            str(node_id)
            for node_id, node in api.items()
            if isinstance(node, dict) and _is_text_encode_node(node.get("class_type", ""))
        ]
        if len(clip_ids) == 2:
            positive_refs = [NodeRef(clip_ids[0])]
            negative_refs = [NodeRef(clip_ids[1])]
            warnings.append("No sampler path found; using first two TextEncode nodes.")
        elif clip_ids:
            positive_refs = [NodeRef(node_id) for node_id in clip_ids]
            warnings.append("No sampler path found; showing all TextEncode nodes as positive.")

    positive = _concat_traced_api_nodes(api, positive_refs, delimiter, ui)
    negative = _concat_traced_api_nodes(api, negative_refs, delimiter, ui)
    details = [
        "Source: API prompt graph",
        f"Sampler node: {sampler_id or '-'}",
        f"Positive source nodes: {', '.join(_unique([ref.label() for ref in positive_refs])) or '-'}",
        f"Negative source nodes: {', '.join(_unique([ref.label() for ref in negative_refs])) or '-'}",
    ]
    return PromptGuess(positive, negative, "\n".join(details), tuple(warnings))


def _first_api_sampler_id(api: dict[str, Any]) -> str:
    sampler_ids = [
        str(node_id)
        for node_id, node in api.items()
        if isinstance(node, dict) and _is_sampler_node(node.get("class_type", ""))
    ]
    if not sampler_ids:
        return ""

    upstream_cache: dict[str, set[str]] = {}
    visiting: set[str] = set()

    def upstream(node_id: str) -> set[str]:
        if node_id in upstream_cache:
            return upstream_cache[node_id]
        if node_id in visiting:
            return set()
        node = api.get(node_id)
        if not isinstance(node, dict):
            upstream_cache[node_id] = set()
            return set()
        visiting.add(node_id)
        result: set[str] = set()
        try:
            inputs = node.get("inputs")
            if isinstance(inputs, dict):
                for value in inputs.values():
                    for source_id in _connected_node_ids(value):
                        if source_id in result:
                            continue
                        result.add(source_id)
                        result.update(upstream(source_id))
        finally:
            visiting.remove(node_id)
        upstream_cache[node_id] = result
        return result

    sampler_set = set(sampler_ids)
    first_candidates = [
        node_id for node_id in sampler_ids if not (upstream(node_id) & sampler_set)
    ]
    if first_candidates:
        return sorted(first_candidates, key=_node_sort_key)[0]
    return sorted(sampler_ids, key=_node_sort_key)[0]


def _concat_traced_api_nodes(
    api: dict[str, Any],
    node_refs: list[NodeRef],
    delimiter: str,
    ui: dict[str, Any] | None = None,
) -> str:
    values = []
    seen: set[NodeRef] = set()
    for node_ref in node_refs:
        if node_ref in seen:
            continue
        seen.add(node_ref)
        text = _trace_api_conditioning_to_text(
            api, node_ref.node_id, node_ref.output_index, set(), ui
        )
        if text:
            values.append(text)
    return delimiter.join(values)


def _trace_api_conditioning_to_text(
    api: dict[str, Any],
    node_id: str,
    output_index: int | None,
    visited: set[str],
    ui: dict[str, Any] | None = None,
) -> str:
    node_id = str(node_id)
    visit_key = f"{node_id}:{output_index}"
    if visit_key in visited:
        return ""
    visited.add(visit_key)
    node = api.get(node_id)
    if not isinstance(node, dict):
        return ""

    if _is_clip_text_node(node.get("class_type", "")):
        return _api_node_text(api, node_id, visited, ui)

    inputs = node.get("inputs")
    if isinstance(inputs, dict):
        polarity = _ui_output_polarity_for_id(ui, node_id, output_index)
        if polarity:
            text = _api_input_keys_text(
                api, inputs, _prompt_input_keys(polarity), visited, ui
            )
            if text:
                return text

        if _is_context_node(node.get("class_type", "")) and output_index is not None:
            context_key = _context_output_key(output_index)
            if context_key:
                for source_ref in _connected_node_refs(inputs.get(context_key)):
                    text = _trace_api_conditioning_to_text(
                        api, source_ref.node_id, source_ref.output_index, visited, ui
                    )
                    if text:
                        return text
                for source_ref in _connected_node_refs(inputs.get("base_ctx")):
                    text = _trace_api_conditioning_to_text(
                        api, source_ref.node_id, output_index, visited, ui
                    )
                    if text:
                        return text

        for key in ("conditioning", "positive", "negative", "text"):
            for source_ref in _connected_node_refs(inputs.get(key)):
                text = _trace_api_conditioning_to_text(
                    api, source_ref.node_id, source_ref.output_index, visited, ui
                )
                if text:
                    return text
    return _api_node_text(api, node_id, visited, ui)


def _api_node_text(
    api: dict[str, Any],
    node_id: str,
    visited: set[str],
    ui: dict[str, Any] | None = None,
) -> str:
    node = api.get(str(node_id))
    if not isinstance(node, dict):
        return ""
    inputs = node.get("inputs")
    if not isinstance(inputs, dict):
        return ""

    ordered_keys = [key for key in TEXT_INPUT_KEYS if key in inputs]
    ordered_keys.extend(
        key
        for key in inputs
        if key not in ordered_keys and _is_numbered_string_key(key)
    )

    values: list[str] = []
    for key in ordered_keys:
        text = _api_input_text(api, inputs.get(key), visited, ui)
        if text:
            values.append(text)
    if _is_text_concat_node(node.get("class_type", "")) and "separator" in inputs:
        separator = inputs.get("separator")
        if isinstance(separator, str):
            return separator.join(values)
    return "\n".join(_unique(values))


def _api_input_text(
    api: dict[str, Any],
    value: Any,
    visited: set[str],
    ui: dict[str, Any] | None = None,
) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        connected = _connected_node_refs(value)
        if connected:
            return "\n".join(
                text
                for source_ref in connected
                if (text := _trace_api_conditioning_to_text(
                    api, source_ref.node_id, source_ref.output_index, visited, ui
                ))
            )
        return "\n".join(str(item).strip() for item in value if isinstance(item, str))
    if isinstance(value, dict):
        return "\n".join(
            text
            for item in value.values()
            if (text := _api_input_text(api, item, visited, ui))
        )
    return ""


def _api_input_keys_text(
    api: dict[str, Any],
    inputs: dict[str, Any],
    input_keys: tuple[str, ...],
    visited: set[str],
    ui: dict[str, Any] | None,
) -> str:
    values: list[str] = []
    for key in input_keys:
        if key not in inputs:
            continue
        text = _api_input_text(api, inputs.get(key), visited, ui)
        if text:
            values.append(text)
    return "\n".join(_unique(values))


def _input_contains_text(value: Any) -> bool:
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, list):
        return any(isinstance(item, str) and item.strip() for item in value)
    if isinstance(value, dict):
        return any(_input_contains_text(item) for item in value.values())
    return False


def _is_numbered_string_key(key: str) -> bool:
    lowered = key.lower()
    return bool(re.fullmatch(r"(string|text|prompt)_?\d+", lowered))


def _connected_node_ids(value: Any) -> list[str]:
    return [node_ref.node_id for node_ref in _connected_node_refs(value)]


def _connected_node_refs(value: Any) -> list[NodeRef]:
    if (
        isinstance(value, list)
        and len(value) >= 2
        and isinstance(value[0], (str, int))
        and isinstance(value[1], int)
    ):
        return [NodeRef(str(value[0]), int(value[1]))]
    return []


def _infer_auto_clip_ui(ui: dict[str, Any], delimiter: str) -> PromptGuess:
    primary_guess = _infer_auto_clip_ui_graph(ui, delimiter)
    if primary_guess.positive or primary_guess.negative:
        return primary_guess

    for subgraph in _iter_ui_subgraphs(ui):
        subgraph_guess = _infer_auto_clip_ui_graph(subgraph, delimiter)
        if not (subgraph_guess.positive or subgraph_guess.negative):
            continue
        subgraph_name = str(subgraph.get("name") or subgraph.get("id") or "-")
        details = subgraph_guess.details.replace(
            "Source: UI workflow graph",
            f"Source: UI workflow subgraph\nSubgraph: {subgraph_name}",
            1,
        )
        warnings = (
            *subgraph_guess.warnings,
            "Top-level prompt path not found; used a Node 2.0 subgraph.",
        )
        return PromptGuess(
            subgraph_guess.positive,
            subgraph_guess.negative,
            details,
            warnings,
        )
    return primary_guess


def _infer_auto_clip_ui_graph(ui: dict[str, Any], delimiter: str) -> PromptGuess:
    warnings: list[str] = []
    nodes = _ui_nodes_by_id(ui)
    link_sources = _ui_link_sources(ui)
    positive_refs: list[NodeRef] = []
    negative_refs: list[NodeRef] = []

    sampler_id = _first_ui_sampler_id(ui)
    if sampler_id:
        node = nodes.get(sampler_id, {})
        positive_refs.extend(_ui_source_node_refs(node, "positive", link_sources))
        negative_refs.extend(_ui_source_node_refs(node, "negative", link_sources))
        if not positive_refs and not negative_refs:
            for source_ref in _ui_source_node_refs(node, "base_ctx", link_sources):
                positive_refs.append(NodeRef(source_ref.node_id, 4))
                negative_refs.append(NodeRef(source_ref.node_id, 5))
    else:
        warnings.append("No sampler node found in UI workflow graph.")

    if not positive_refs and not negative_refs:
        clip_ids = [
            node_id
            for node_id, node in nodes.items()
            if _is_ui_node_active(node)
            and _is_text_encode_node(str(node.get("type") or node.get("class_type") or ""))
        ]
        if len(clip_ids) == 2:
            positive_refs = [NodeRef(clip_ids[0])]
            negative_refs = [NodeRef(clip_ids[1])]
            warnings.append("No UI sampler path found; using first two TextEncode nodes.")
        elif clip_ids:
            positive_refs = [NodeRef(node_id) for node_id in clip_ids]
            warnings.append("No UI sampler path found; showing all TextEncode nodes as positive.")

    positive = _concat_traced_ui_nodes(ui, positive_refs, delimiter)
    negative = _concat_traced_ui_nodes(ui, negative_refs, delimiter)
    details = [
        "Source: UI workflow graph",
        f"Sampler node: {sampler_id or '-'}",
        f"Positive source nodes: {', '.join(_unique([ref.label() for ref in positive_refs])) or '-'}",
        f"Negative source nodes: {', '.join(_unique([ref.label() for ref in negative_refs])) or '-'}",
    ]
    return PromptGuess(positive, negative, "\n".join(details), tuple(warnings))


def _first_ui_sampler_id(ui: dict[str, Any]) -> str:
    nodes = _ui_nodes_by_id(ui)
    link_sources = _ui_link_sources(ui)
    sampler_ids = [
        node_id
        for node_id, node in nodes.items()
        if _is_ui_node_active(node)
        and _is_ui_sampler_candidate(ui, node)
    ]
    if not sampler_ids:
        return ""

    upstream_cache: dict[str, set[str]] = {}

    def upstream(node_id: str) -> set[str]:
        if node_id in upstream_cache:
            return upstream_cache[node_id]
        node = nodes.get(node_id)
        if not node:
            upstream_cache[node_id] = set()
            return set()
        result: set[str] = set()
        inputs = node.get("inputs")
        if isinstance(inputs, list):
            for item in inputs:
                if not isinstance(item, dict) or item.get("link") is None:
                    continue
                try:
                    source_ref = link_sources.get(int(item["link"]))
                except (TypeError, ValueError):
                    source_ref = None
                source_id = source_ref.node_id if source_ref else None
                if not source_id or source_id in result:
                    continue
                result.add(source_id)
                result.update(upstream(source_id))
        upstream_cache[node_id] = result
        return result

    sampler_set = set(sampler_ids)
    first_candidates = [
        node_id for node_id in sampler_ids if not (upstream(node_id) & sampler_set)
    ]
    if first_candidates:
        return sorted(first_candidates, key=_node_sort_key)[0]
    return sorted(sampler_ids, key=_node_sort_key)[0]


def _concat_traced_ui_nodes(
    ui: dict[str, Any],
    node_refs: list[NodeRef],
    delimiter: str,
) -> str:
    values = []
    seen: set[NodeRef] = set()
    for node_ref in node_refs:
        if node_ref in seen:
            continue
        seen.add(node_ref)
        text = _trace_ui_conditioning_to_text(
            ui, node_ref.node_id, node_ref.output_index, set()
        )
        if text:
            values.append(text)
    return delimiter.join(values)


def _trace_ui_conditioning_to_text(
    ui: dict[str, Any],
    node_id: str,
    output_index: int | None,
    visited: set[str],
) -> str:
    nodes = _ui_nodes_by_id(ui)
    link_sources = _ui_link_sources(ui)
    node_id = str(node_id)
    visit_key = f"{node_id}:{output_index}"
    if visit_key in visited:
        return ""
    visited.add(visit_key)
    node = nodes.get(node_id)
    if not node:
        return ""
    node_type = str(node.get("type") or node.get("class_type") or "")

    mode = _ui_node_mode(node)
    if mode == 2:
        return ""
    if mode == UI_BYPASS_MODE:
        for source_ref in _ui_bypass_source_refs(node, output_index, link_sources):
            text = _trace_ui_conditioning_to_text(
                ui, source_ref.node_id, source_ref.output_index, visited
            )
            if text:
                return text
        return ""

    virtual_kind = _ui_virtual_node_kind(node)
    if virtual_kind == "get":
        set_node_id = _ui_set_node_ids_by_name(ui).get(_ui_virtual_node_name(node))
        if set_node_id is not None:
            return _trace_ui_conditioning_to_text(ui, set_node_id, output_index, visited)
        return ""
    if virtual_kind == "set":
        for source_ref in _ui_all_linked_source_refs(node, link_sources):
            source_output_index = source_ref.output_index
            if output_index in {4, 5} and _ui_node_has_context_socket(node):
                source_output_index = output_index
            text = _trace_ui_conditioning_to_text(
                ui, source_ref.node_id, source_output_index, visited
            )
            if text:
                return text
        return ""

    cached_output_text = _ui_connected_display_text(ui, node_id, output_index)
    if cached_output_text:
        return cached_output_text

    if _is_clip_text_node(node_type):
        return _ui_node_text(ui, node_id, visited)

    polarity = _ui_node_output_polarity(node, output_index)
    if polarity:
        text = _trace_ui_prompt_inputs(
            ui, node, polarity, link_sources, visited
        )
        if text:
            return text

    if (
        output_index is not None
        and (_is_context_node(node_type) or _ui_node_has_context_output(node))
    ):
        context_key = _context_output_key(output_index)
        if context_key:
            input_keys = (context_key,)
            if context_key in {"positive", "negative"}:
                text = _trace_ui_prompt_inputs(
                    ui, node, context_key, link_sources, visited
                )
            else:
                text = _trace_ui_input_keys(
                    ui, node, input_keys, link_sources, visited
                )
            if text:
                return text
            for source_ref in _ui_source_node_refs(node, "base_ctx", link_sources):
                text = _trace_ui_conditioning_to_text(
                    ui, source_ref.node_id, output_index, visited
                )
                if text:
                    return text

    for input_name in ("conditioning", "positive", "negative", "text"):
        for source_ref in _ui_source_node_refs(node, input_name, link_sources):
            text = _trace_ui_conditioning_to_text(
                ui, source_ref.node_id, source_ref.output_index, visited
            )
            if text:
                return text
    return _ui_node_text(ui, node_id, visited)


def _trace_ui_input_keys(
    ui: dict[str, Any],
    node: dict[str, Any],
    input_keys: tuple[str, ...],
    link_sources: dict[int, NodeRef],
    visited: set[str],
) -> str:
    values: list[str] = []
    for input_name in input_keys:
        for source_ref in _ui_source_node_refs(node, input_name, link_sources):
            text = _trace_ui_conditioning_to_text(
                ui, source_ref.node_id, source_ref.output_index, visited
            )
            if text:
                values.append(text)
    return "\n".join(_unique(values))


def _trace_ui_prompt_inputs(
    ui: dict[str, Any],
    node: dict[str, Any],
    polarity: str,
    link_sources: dict[int, NodeRef],
    visited: set[str],
) -> str:
    values: list[str] = []
    direct_text = _trace_ui_input_keys(
        ui, node, _prompt_input_keys(polarity), link_sources, visited
    )
    if direct_text:
        values.append(direct_text)
    for source_ref in _ui_virtual_source_refs_for_polarity(
        ui, node, polarity, link_sources
    ):
        text = _trace_ui_conditioning_to_text(
            ui, source_ref.node_id, source_ref.output_index, visited
        )
        if text:
            values.append(text)
    return "\n".join(_unique(values))


def _ui_node_text(ui: dict[str, Any], node_id: str, visited: set[str]) -> str:
    nodes = _ui_nodes_by_id(ui)
    link_sources = _ui_link_sources(ui)
    node = nodes.get(str(node_id))
    if not node or _ui_node_mode(node) == 2:
        return ""

    linked_values: list[str] = []
    for input_name in TEXT_INPUT_KEYS:
        for source_ref in _ui_source_node_refs(node, input_name, link_sources):
            text = _trace_ui_conditioning_to_text(
                ui, source_ref.node_id, source_ref.output_index, visited
            )
            if text:
                linked_values.append(text)
    if linked_values:
        return "\n".join(_unique(linked_values))

    values: list[str] = []
    widgets = node.get("widgets_values")
    if isinstance(widgets, list):
        values.extend(_ui_widget_strings(node))
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


def _ui_link_sources(ui: dict[str, Any]) -> dict[int, NodeRef]:
    links = ui.get("links")
    if not isinstance(links, list):
        return {}
    sources: dict[int, NodeRef] = {}
    for link in links:
        parts = _ui_link_parts(link)
        if parts is None:
            continue
        link_id, origin_id, origin_slot, _target_id, _target_slot = parts
        sources[link_id] = NodeRef(origin_id, origin_slot)
    return sources


def _ui_link_parts(link: Any) -> tuple[int, str, int, str, int] | None:
    if isinstance(link, list) and len(link) >= 5:
        values = (link[0], link[1], link[2], link[3], link[4])
    elif isinstance(link, dict):
        values = (
            link.get("id"),
            link.get("origin_id"),
            link.get("origin_slot"),
            link.get("target_id"),
            link.get("target_slot"),
        )
    else:
        return None
    try:
        return (
            int(values[0]),
            str(values[1]),
            int(values[2]),
            str(values[3]),
            int(values[4]),
        )
    except (TypeError, ValueError):
        return None


def _ui_connected_display_text(
    ui: dict[str, Any],
    node_id: str,
    output_index: int | None,
) -> str:
    if output_index is None:
        return ""
    nodes = _ui_nodes_by_id(ui)
    links = ui.get("links")
    if not isinstance(links, list):
        return ""
    for link in links:
        parts = _ui_link_parts(link)
        if parts is None:
            continue
        _link_id, origin_id, origin_slot, target_id, _target_slot = parts
        if origin_id != str(node_id) or origin_slot != output_index:
            continue
        target = nodes.get(target_id)
        if not target or not _is_text_display_node(target):
            continue
        widget_strings = _ui_widget_strings(target)
        if widget_strings:
            return widget_strings[0]
    return ""


def _ui_source_node_refs(
    node: dict[str, Any],
    input_name: str,
    link_sources: dict[int, NodeRef],
) -> list[NodeRef]:
    inputs = node.get("inputs")
    if not isinstance(inputs, list):
        return []
    target_name = input_name.lower()
    source_refs: list[NodeRef] = []
    for item in inputs:
        if not isinstance(item, dict):
            continue
        names = {str(item.get("name") or "").lower()}
        widget = item.get("widget")
        if isinstance(widget, dict):
            names.add(str(widget.get("name") or "").lower())
        if target_name not in names:
            continue
        link_id = item.get("link")
        if link_id is None:
            continue
        try:
            source_ref = link_sources.get(int(link_id))
        except (TypeError, ValueError):
            source_ref = None
        if source_ref:
            source_refs.append(source_ref)
    return source_refs


def _ui_all_linked_source_refs(
    node: dict[str, Any],
    link_sources: dict[int, NodeRef],
) -> list[NodeRef]:
    inputs = node.get("inputs")
    if not isinstance(inputs, list):
        return []
    source_refs: list[NodeRef] = []
    for item in inputs:
        if not isinstance(item, dict) or item.get("link") is None:
            continue
        try:
            source_ref = link_sources.get(int(item["link"]))
        except (TypeError, ValueError):
            source_ref = None
        if source_ref:
            source_refs.append(source_ref)
    return source_refs


def _ui_bypass_source_refs(
    node: dict[str, Any],
    output_index: int | None,
    link_sources: dict[int, NodeRef],
) -> list[NodeRef]:
    inputs = node.get("inputs")
    if not isinstance(inputs, list):
        return []
    linked_items: list[tuple[dict[str, Any], NodeRef]] = []
    for item in inputs:
        if not isinstance(item, dict) or item.get("link") is None:
            continue
        try:
            source_ref = link_sources.get(int(item["link"]))
        except (TypeError, ValueError):
            source_ref = None
        if source_ref:
            linked_items.append((item, source_ref))
    if not linked_items:
        return []

    if output_index is not None and 0 <= output_index < len(inputs):
        candidate = inputs[output_index]
        for item, source_ref in linked_items:
            if item is candidate:
                return [source_ref]

    outputs = node.get("outputs")
    if (
        output_index is not None
        and isinstance(outputs, list)
        and 0 <= output_index < len(outputs)
        and isinstance(outputs[output_index], dict)
    ):
        output = outputs[output_index]
        output_name = str(output.get("name") or "").lower()
        output_type = str(output.get("type") or "").lower()
        for item, source_ref in linked_items:
            input_name = str(item.get("name") or "").lower()
            input_type = str(item.get("type") or "").lower()
            if output_name and input_name == output_name:
                return [source_ref]
            if output_type and input_type == output_type:
                return [source_ref]

    return [linked_items[0][1]]


def _ui_widget_strings(node: dict[str, Any]) -> list[str]:
    widgets = node.get("widgets_values")
    values = (
        widgets
        if isinstance(widgets, list)
        else list(widgets.values()) if isinstance(widgets, dict) else []
    )
    return [value.strip() for value in values if isinstance(value, str) and value.strip()]


def _ui_virtual_node_kind(node: dict[str, Any]) -> str:
    node_type = str(node.get("type") or node.get("class_type") or "")
    normalized = re.sub(r"[^a-z0-9]+", "", node_type.lower())
    if normalized.endswith("getnode"):
        return "get"
    if normalized.endswith("setnode"):
        return "set"
    return ""


def _ui_virtual_node_name(node: dict[str, Any]) -> str:
    widget_strings = _ui_widget_strings(node)
    if widget_strings:
        return widget_strings[0]
    properties = node.get("properties")
    if isinstance(properties, dict):
        return str(properties.get("previousName") or "").strip()
    return ""


def _ui_virtual_source_refs_for_polarity(
    ui: dict[str, Any],
    node: dict[str, Any],
    polarity: str,
    link_sources: dict[int, NodeRef],
) -> list[NodeRef]:
    nodes = _ui_nodes_by_id(ui)
    result: list[NodeRef] = []
    for source_ref in _ui_all_linked_source_refs(node, link_sources):
        source_node = nodes.get(source_ref.node_id)
        if not source_node or not _ui_virtual_node_kind(source_node):
            continue
        outputs = source_node.get("outputs")
        output_types: set[str] = set()
        if isinstance(outputs, list):
            output_types = {
                str(output.get("type") or "").upper()
                for output in outputs
                if isinstance(output, dict)
            }
        if output_types and not any(
            marker in output_type
            for output_type in output_types
            for marker in ("STRING", "CONDITIONING", "CONTEXT")
        ):
            continue
        if _label_polarity(_ui_virtual_node_name(source_node)) == polarity:
            result.append(source_ref)
    return result


def _ui_set_node_ids_by_name(ui: dict[str, Any]) -> dict[str, str]:
    return {
        name: node_id
        for node_id, node in _ui_nodes_by_id(ui).items()
        if _ui_virtual_node_kind(node) == "set"
        and (name := _ui_virtual_node_name(node))
    }


def _ui_node_mode(node: dict[str, Any]) -> int:
    try:
        return int(node.get("mode", 0))
    except (TypeError, ValueError):
        return 0


def _is_ui_node_active(node: dict[str, Any]) -> bool:
    return _ui_node_mode(node) not in INACTIVE_UI_NODE_MODES


def _prompt_input_keys(polarity: str) -> tuple[str, ...]:
    if polarity == "negative":
        return NEGATIVE_TEXT_INPUT_KEYS
    return POSITIVE_TEXT_INPUT_KEYS


def _ui_node_output_polarity(
    node: dict[str, Any],
    output_index: int | None,
) -> str:
    outputs = node.get("outputs")
    if (
        output_index is None
        or not isinstance(outputs, list)
        or output_index < 0
        or output_index >= len(outputs)
        or not isinstance(outputs[output_index], dict)
    ):
        return ""
    output = outputs[output_index]
    label = " ".join(
        str(output.get(key) or "") for key in ("name", "label")
    ).lower()
    compact = re.sub(r"[^a-z0-9+-]+", "", label)
    if "conditioning-" in compact or "cond-" in compact:
        return "negative"
    if "conditioning+" in compact or "cond+" in compact:
        return "positive"
    return _label_polarity(label)


def _label_polarity(value: str) -> str:
    lowered = value.lower()
    normalized = re.sub(r"[^a-z0-9]+", "_", lowered).strip("_")
    tokens = {token for token in normalized.split("_") if token}
    if "negative" in tokens or "neg" in tokens:
        return "negative"
    if "positive" in tokens or "pos" in tokens:
        return "positive"
    if "prompt" in tokens or "quality" in tokens or "qualitytags" in tokens:
        return "positive"
    return ""


def _ui_output_polarity_for_id(
    ui: dict[str, Any] | None,
    node_id: str,
    output_index: int | None,
) -> str:
    if not ui:
        return ""
    node = _ui_node_by_id(ui, node_id)
    if not node:
        return ""
    return _ui_node_output_polarity(node, output_index)


def _ui_node_by_id(
    ui: dict[str, Any],
    node_id: str,
) -> dict[str, Any] | None:
    target = str(node_id)
    for graph in [ui, *_iter_ui_subgraphs(ui)]:
        node = _ui_nodes_by_id(graph).get(target)
        if node:
            return node
    return None


def _ui_node_has_context_output(node: dict[str, Any]) -> bool:
    outputs = node.get("outputs")
    if not isinstance(outputs, list):
        return False
    for output in outputs:
        if not isinstance(output, dict):
            continue
        value = f"{output.get('name', '')} {output.get('type', '')}".lower()
        if "context" in value:
            return True
    return False


def _ui_node_has_context_socket(node: dict[str, Any]) -> bool:
    for collection_name in ("inputs", "outputs"):
        collection = node.get(collection_name)
        if not isinstance(collection, list):
            continue
        for item in collection:
            if not isinstance(item, dict):
                continue
            value = f"{item.get('name', '')} {item.get('type', '')}".lower()
            if "context" in value:
                return True
    return False


def _is_text_display_node(node: dict[str, Any]) -> bool:
    node_type = str(node.get("type") or node.get("class_type") or "")
    normalized = re.sub(r"[^a-z0-9]+", "", node_type.lower())
    return "showtext" in normalized


def _ui_subgraph_definitions(ui: dict[str, Any]) -> list[dict[str, Any]]:
    definitions = ui.get("definitions")
    if not isinstance(definitions, dict):
        return []
    subgraphs = definitions.get("subgraphs")
    if isinstance(subgraphs, list):
        return [item for item in subgraphs if isinstance(item, dict)]
    if isinstance(subgraphs, dict):
        return [item for item in subgraphs.values() if isinstance(item, dict)]
    return []


def _iter_ui_subgraphs(ui: dict[str, Any]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    pending = list(_ui_subgraph_definitions(ui))
    seen: set[int] = set()
    while pending:
        subgraph = pending.pop(0)
        marker = id(subgraph)
        if marker in seen:
            continue
        seen.add(marker)
        result.append(subgraph)
        pending.extend(_ui_subgraph_definitions(subgraph))
    return result


def _ui_subgraph_by_id(
    ui: dict[str, Any],
    subgraph_id: str,
) -> dict[str, Any] | None:
    target = str(subgraph_id)
    for subgraph in _iter_ui_subgraphs(ui):
        if str(subgraph.get("id")) == target:
            return subgraph
    return None


def _is_ui_sampler_candidate(
    ui: dict[str, Any],
    node: dict[str, Any],
) -> bool:
    node_type = str(node.get("type") or node.get("class_type") or "")
    if _is_sampler_node(node_type):
        return True
    subgraph = _ui_subgraph_by_id(ui, node_type)
    if not subgraph:
        return False
    return _is_sampler_node(str(subgraph.get("name") or ""))


def _is_clip_text_node(value: Any) -> bool:
    return "cliptextencode" in str(value).lower()


def _is_text_encode_node(value: Any) -> bool:
    return "textencode" in re.sub(r"[^a-z0-9]+", "", str(value).lower())


def _is_text_concat_node(value: Any) -> bool:
    normalized = re.sub(r"[^a-z0-9]+", "", str(value).lower())
    return any(
        marker in normalized
        for marker in (
            "textconcatenate",
            "concatenatetext",
            "stringconcatenate",
            "concatenatestring",
        )
    )


def _is_context_node(value: Any) -> bool:
    return "context" in str(value).lower()


def _context_output_key(output_index: int) -> str:
    output_map = {
        0: "model",
        1: "clip",
        2: "vae",
        3: "latent",
        4: "positive",
        5: "negative",
        6: "seed",
        7: "steps",
        8: "step_refiner",
        9: "cfg",
        10: "sampler",
        11: "scheduler",
    }
    return output_map.get(output_index, "")


def _is_sampler_node(value: Any) -> bool:
    lowered = str(value).lower()
    if "sampler" not in lowered:
        return False
    return "selector" not in lowered and "config" not in lowered


def _node_sort_key(value: str) -> tuple[int, Any]:
    if re.fullmatch(r"\d+", value):
        return (0, int(value))
    return (1, value)


def _unique(values: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value not in seen:
            result.append(value)
            seen.add(value)
    return result
