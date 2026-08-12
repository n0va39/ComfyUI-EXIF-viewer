from __future__ import annotations

import json
import unittest

from comfy_workflow_prompts import decode_delimiter, describe_workflow_node
from comfy_workflow_prompts import infer_workflow_prompts


class WorkflowPromptInferenceTests(unittest.TestCase):
    def test_manual_node_selection_concats_api_text_nodes(self) -> None:
        prompt = {
            "10": {"class_type": "PrimitiveString", "inputs": {"value": "cat"}},
            "11": {"class_type": "PrimitiveString", "inputs": {"value": "best quality"}},
            "12": {"class_type": "PrimitiveString", "inputs": {"value": "bad hands"}},
        }

        guess = infer_workflow_prompts(
            workflow_json="",
            prompt_json=json.dumps(prompt),
            mode="manual",
            positive_node_ids="10,11",
            negative_node_ids="12",
            delimiter=", ",
        )

        self.assertEqual(guess.positive, "cat, best quality")
        self.assertEqual(guess.negative, "bad hands")

    def test_auto_clip_trace_from_api_sampler(self) -> None:
        prompt = {
            "4": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "model.safetensors"}},
            "6": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["4", 1], "text": "cat"}},
            "7": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["4", 1], "text": "bad hands"}},
            "8": {
                "class_type": "KSampler",
                "inputs": {"positive": ["6", 0], "negative": ["7", 0]},
            },
        }

        guess = infer_workflow_prompts("", json.dumps(prompt), "auto")

        self.assertEqual(guess.positive, "cat")
        self.assertEqual(guess.negative, "bad hands")
        self.assertIn("API prompt graph", guess.details)

    def test_auto_clip_trace_resolves_api_text_concat_node(self) -> None:
        prompt = {
            "20": {
                "class_type": "Text Concatenate",
                "inputs": {"text_a": "cat", "text_b": "best quality"},
            },
            "6": {"class_type": "CLIPTextEncode", "inputs": {"text": ["20", 0]}},
            "7": {"class_type": "CLIPTextEncode", "inputs": {"text": "bad hands"}},
            "8": {
                "class_type": "KSampler",
                "inputs": {"positive": ["6", 0], "negative": ["7", 0]},
            },
        }

        guess = infer_workflow_prompts("", json.dumps(prompt), "auto")

        self.assertEqual(guess.positive, "cat\nbest quality")
        self.assertEqual(guess.negative, "bad hands")

    def test_auto_clip_trace_uses_first_sampler_only(self) -> None:
        prompt = {
            "6": {"class_type": "CLIPTextEncode", "inputs": {"text": "first cat"}},
            "7": {"class_type": "CLIPTextEncode", "inputs": {"text": "first bad"}},
            "8": {
                "class_type": "KSampler",
                "inputs": {"positive": ["6", 0], "negative": ["7", 0]},
            },
            "9": {"class_type": "VAEDecode", "inputs": {"samples": ["8", 0]}},
            "16": {"class_type": "CLIPTextEncode", "inputs": {"text": "second cat"}},
            "17": {"class_type": "CLIPTextEncode", "inputs": {"text": "second bad"}},
            "18": {
                "class_type": "KSampler",
                "inputs": {
                    "samples": ["8", 0],
                    "positive": ["16", 0],
                    "negative": ["17", 0],
                },
            },
        }

        guess = infer_workflow_prompts("", json.dumps(prompt), "auto")

        self.assertEqual(guess.positive, "first cat")
        self.assertEqual(guess.negative, "first bad")
        self.assertIn("Sampler node: 8", guess.details)

    def test_auto_clip_trace_handles_cyclic_sampler_graph(self) -> None:
        prompt = {
            "1": {
                "class_type": "KSampler",
                "inputs": {"model": ["2", 0], "positive": ["3", 0]},
            },
            "2": {
                "class_type": "KSampler",
                "inputs": {"model": ["1", 0]},
            },
            "3": {"class_type": "CLIPTextEncode", "inputs": {"text": "cat"}},
        }

        guess = infer_workflow_prompts("", json.dumps(prompt), "auto")

        self.assertEqual(guess.positive, "cat")
        self.assertIn("Sampler node: 1", guess.details)

    def test_ui_auto_clip_trace_handles_cyclic_sampler_graph(self) -> None:
        workflow = {
            "nodes": [
                {
                    "id": 1,
                    "type": "KSampler",
                    "inputs": [
                        {"name": "model", "link": 1},
                        {"name": "positive", "link": 3},
                    ],
                },
                {
                    "id": 2,
                    "type": "KSampler",
                    "inputs": [{"name": "model", "link": 2}],
                },
                {"id": 3, "type": "CLIPTextEncode", "widgets_values": ["cat"]},
            ],
            "links": [
                [1, 2, 0, 1, 0, "MODEL"],
                [2, 1, 0, 2, 0, "MODEL"],
                [3, 3, 0, 1, 1, "CONDITIONING"],
            ],
        }

        guess = infer_workflow_prompts(json.dumps(workflow), "", "auto")

        self.assertEqual(guess.positive, "cat")
        self.assertIn("Sampler node: 1", guess.details)

    def test_auto_clip_trace_respects_context_output_index(self) -> None:
        prompt = {
            "6": {"class_type": "CLIPTextEncode", "inputs": {"text": "cat"}},
            "7": {"class_type": "CLIPTextEncode", "inputs": {"text": "bad"}},
            "20": {
                "class_type": "Context Big (rgthree)",
                "inputs": {"positive": ["6", 0], "negative": ["7", 0]},
            },
            "8": {
                "class_type": "KSampler",
                "inputs": {"positive": ["20", 4], "negative": ["20", 5]},
            },
        }

        guess = infer_workflow_prompts("", json.dumps(prompt), "auto")

        self.assertEqual(guess.positive, "cat")
        self.assertEqual(guess.negative, "bad")

    def test_auto_clip_trace_supports_extended_api_text_keys(self) -> None:
        prompt = {
            "6": {
                "class_type": "CLIPTextEncodeSDXL",
                "inputs": {"text_g": "portrait", "text_l": "detailed lighting"},
            },
            "7": {
                "class_type": "WildcardTextEncode",
                "inputs": {"populated_text": "bad hands"},
            },
            "8": {
                "class_type": "KSampler",
                "inputs": {"positive": ["6", 0], "negative": ["7", 0]},
            },
        }

        guess = infer_workflow_prompts("", json.dumps(prompt), "auto")

        self.assertEqual(guess.positive, "portrait\ndetailed lighting")
        self.assertEqual(guess.negative, "bad hands")

    def test_auto_clip_trace_from_ui_workflow_sampler(self) -> None:
        workflow = {
            "nodes": [
                {
                    "id": 6,
                    "type": "CLIPTextEncode",
                    "inputs": [{"name": "clip", "link": 1}],
                    "widgets_values": ["cat"],
                },
                {
                    "id": 7,
                    "type": "CLIPTextEncode",
                    "inputs": [{"name": "clip", "link": 1}],
                    "widgets_values": ["bad hands"],
                },
                {
                    "id": 8,
                    "type": "KSampler",
                    "inputs": [
                        {"name": "positive", "link": 2},
                        {"name": "negative", "link": 3},
                    ],
                },
            ],
            "links": [
                [2, 6, 0, 8, 0, "CONDITIONING"],
                [3, 7, 0, 8, 1, "CONDITIONING"],
            ],
        }

        guess = infer_workflow_prompts(json.dumps(workflow), "", "auto")

        self.assertEqual(guess.positive, "cat")
        self.assertEqual(guess.negative, "bad hands")
        self.assertIn("UI workflow graph", guess.details)

    def test_ui_linked_text_overrides_stale_widget_value(self) -> None:
        workflow = {
            "nodes": [
                {"id": 1, "type": "PrimitiveStringMultiline", "widgets_values": ["current cat"]},
                {
                    "id": 6,
                    "type": "CLIPTextEncode",
                    "inputs": [
                        {"name": "text", "link": 1, "widget": {"name": "text"}},
                    ],
                    "widgets_values": ["stale cat"],
                },
                {"id": 7, "type": "CLIPTextEncode", "widgets_values": ["bad hands"]},
                {
                    "id": 8,
                    "type": "KSampler",
                    "inputs": [
                        {"name": "positive", "link": 2},
                        {"name": "negative", "link": 3},
                    ],
                },
            ],
            "links": [
                [1, 1, 0, 6, 0, "STRING"],
                [2, 6, 0, 8, 0, "CONDITIONING"],
                [3, 7, 0, 8, 1, "CONDITIONING"],
            ],
        }

        guess = infer_workflow_prompts(json.dumps(workflow), "", "auto")

        self.assertEqual(guess.positive, "current cat")
        self.assertNotIn("stale cat", guess.positive)
        self.assertEqual(guess.negative, "bad hands")

    def test_ui_auto_trace_skips_muted_first_sampler(self) -> None:
        workflow = {
            "nodes": [
                {"id": 6, "type": "CLIPTextEncode", "widgets_values": ["muted cat"]},
                {"id": 7, "type": "CLIPTextEncode", "widgets_values": ["muted bad"]},
                {
                    "id": 8,
                    "type": "KSampler",
                    "mode": 2,
                    "inputs": [
                        {"name": "positive", "link": 1},
                        {"name": "negative", "link": 2},
                    ],
                },
                {"id": 16, "type": "CLIPTextEncode", "widgets_values": ["active cat"]},
                {"id": 17, "type": "CLIPTextEncode", "widgets_values": ["active bad"]},
                {
                    "id": 18,
                    "type": "KSampler",
                    "inputs": [
                        {"name": "positive", "link": 3},
                        {"name": "negative", "link": 4},
                    ],
                },
            ],
            "links": [
                [1, 6, 0, 8, 0, "CONDITIONING"],
                [2, 7, 0, 8, 1, "CONDITIONING"],
                [3, 16, 0, 18, 0, "CONDITIONING"],
                [4, 17, 0, 18, 1, "CONDITIONING"],
            ],
        }

        guess = infer_workflow_prompts(json.dumps(workflow), "", "auto")

        self.assertEqual(guess.positive, "active cat")
        self.assertEqual(guess.negative, "active bad")
        self.assertIn("Sampler node: 18", guess.details)

    def test_ui_context_trace_respects_source_output_index(self) -> None:
        workflow = {
            "nodes": [
                {"id": 6, "type": "CLIPTextEncode", "widgets_values": ["cat"]},
                {"id": 7, "type": "CLIPTextEncode", "widgets_values": ["bad"]},
                {
                    "id": 20,
                    "type": "Context Big (rgthree)",
                    "inputs": [
                        {"name": "positive", "link": 1},
                        {"name": "negative", "link": 2},
                    ],
                },
                {
                    "id": 8,
                    "type": "KSampler",
                    "inputs": [
                        {"name": "positive", "link": 3},
                        {"name": "negative", "link": 4},
                    ],
                },
            ],
            "links": [
                [1, 6, 0, 20, 0, "CONDITIONING"],
                [2, 7, 0, 20, 1, "CONDITIONING"],
                [3, 20, 4, 8, 0, "CONDITIONING"],
                [4, 20, 5, 8, 1, "CONDITIONING"],
            ],
        }

        guess = infer_workflow_prompts(json.dumps(workflow), "", "auto")

        self.assertEqual(guess.positive, "cat")
        self.assertEqual(guess.negative, "bad")

    def test_ui_set_get_and_bypass_chain_resolves_source_text(self) -> None:
        workflow = {
            "nodes": [
                {"id": 1, "type": "PrimitiveStringMultiline", "widgets_values": ["cat"]},
                {
                    "id": 2,
                    "type": "SetNode",
                    "inputs": [{"name": "value", "link": 1}],
                    "widgets_values": ["prompt bus"],
                },
                {"id": 3, "type": "GetNode", "widgets_values": ["prompt bus"]},
                {
                    "id": 4,
                    "type": "Text Concatenate",
                    "mode": 4,
                    "inputs": [{"name": "text", "link": 2, "type": "STRING"}],
                    "outputs": [{"name": "text", "type": "STRING"}],
                },
                {
                    "id": 6,
                    "type": "CLIPTextEncode",
                    "inputs": [{"name": "text", "link": 3}],
                    "widgets_values": ["stale cat"],
                },
                {"id": 7, "type": "CLIPTextEncode", "widgets_values": ["bad"]},
                {
                    "id": 8,
                    "type": "KSampler",
                    "inputs": [
                        {"name": "positive", "link": 4},
                        {"name": "negative", "link": 5},
                    ],
                },
            ],
            "links": [
                [1, 1, 0, 2, 0, "STRING"],
                [2, 3, 0, 4, 0, "STRING"],
                [3, 4, 0, 6, 0, "STRING"],
                [4, 6, 0, 8, 0, "CONDITIONING"],
                [5, 7, 0, 8, 1, "CONDITIONING"],
            ],
        }

        guess = infer_workflow_prompts(json.dumps(workflow), "", "auto")

        self.assertEqual(guess.positive, "cat")
        self.assertEqual(guess.negative, "bad")

    def test_api_trace_uses_ui_output_names_for_loader_polarity(self) -> None:
        prompt = {
            "4": {
                "class_type": "Efficient Loader",
                "inputs": {"positive": "cat", "negative": "bad hands"},
            },
            "8": {
                "class_type": "KSampler",
                "inputs": {"positive": ["4", 1], "negative": ["4", 2]},
            },
        }
        workflow = {
            "nodes": [
                {
                    "id": 4,
                    "type": "Efficient Loader",
                    "outputs": [
                        {"name": "MODEL", "type": "MODEL"},
                        {"name": "CONDITIONING+", "type": "CONDITIONING"},
                        {"name": "CONDITIONING-", "type": "CONDITIONING"},
                    ],
                },
            ],
            "links": [],
        }

        guess = infer_workflow_prompts(
            json.dumps(workflow),
            json.dumps(prompt),
            "auto",
        )

        self.assertEqual(guess.positive, "cat")
        self.assertEqual(guess.negative, "bad hands")

    def test_api_text_concat_supports_comfyroll_keys_and_separator(self) -> None:
        prompt = {
            "1": {"class_type": "CR Text", "inputs": {"text": "cat"}},
            "2": {"class_type": "CR Text", "inputs": {"text": "best quality"}},
            "3": {
                "class_type": "CR Text Concatenate",
                "inputs": {
                    "text1": ["1", 0],
                    "text2": ["2", 0],
                    "separator": ", ",
                },
            },
            "6": {"class_type": "CLIPTextEncode", "inputs": {"text": ["3", 0]}},
            "7": {"class_type": "CLIPTextEncode", "inputs": {"text": "bad"}},
            "8": {
                "class_type": "KSampler",
                "inputs": {"positive": ["6", 0], "negative": ["7", 0]},
            },
        }

        guess = infer_workflow_prompts("", json.dumps(prompt), "auto")

        self.assertEqual(guess.positive, "cat, best quality")
        self.assertEqual(guess.negative, "bad")

    def test_ui_node2_subgraph_supports_object_links(self) -> None:
        workflow = {
            "nodes": [],
            "links": [],
            "definitions": {
                "subgraphs": [
                    {
                        "id": "sampler-subgraph",
                        "name": "Main Sampler",
                        "nodes": [
                            {
                                "id": 6,
                                "type": "CLIPTextEncode",
                                "widgets_values": ["cat"],
                            },
                            {
                                "id": 7,
                                "type": "CLIPTextEncode",
                                "widgets_values": ["bad"],
                            },
                            {
                                "id": 8,
                                "type": "KSampler",
                                "inputs": [
                                    {"name": "positive", "link": 1},
                                    {"name": "negative", "link": 2},
                                ],
                            },
                        ],
                        "links": [
                            {
                                "id": 1,
                                "origin_id": 6,
                                "origin_slot": 0,
                                "target_id": 8,
                                "target_slot": 0,
                                "type": "CONDITIONING",
                            },
                            {
                                "id": 2,
                                "origin_id": 7,
                                "origin_slot": 0,
                                "target_id": 8,
                                "target_slot": 1,
                                "type": "CONDITIONING",
                            },
                        ],
                    },
                ],
            },
        }

        guess = infer_workflow_prompts(json.dumps(workflow), "", "auto")

        self.assertEqual(guess.positive, "cat")
        self.assertEqual(guess.negative, "bad")
        self.assertIn("Subgraph: Main Sampler", guess.details)

    def test_ui_subgraph_sampler_uses_context_and_showtext_cache(self) -> None:
        workflow = {
            "nodes": [
                {
                    "id": 1,
                    "type": "Prompt Builder",
                    "outputs": [
                        {
                            "name": "positive_prompt",
                            "type": "STRING",
                            "links": [1, 2],
                        },
                        {
                            "name": "negative_prompt",
                            "type": "STRING",
                            "links": [3, 4],
                        },
                    ],
                    "widgets_values": ["stale internal state"],
                },
                {
                    "id": 2,
                    "type": "ShowText|pysssss",
                    "inputs": [{"name": "text", "link": 1}],
                    "widgets_values": ["current cat"],
                },
                {
                    "id": 3,
                    "type": "ShowText|pysssss",
                    "inputs": [{"name": "text", "link": 3}],
                    "widgets_values": ["current bad"],
                },
                {
                    "id": 4,
                    "type": "context-subgraph",
                    "inputs": [
                        {"name": "text", "link": 2},
                        {"name": "text_1", "link": 4},
                    ],
                    "outputs": [
                        {
                            "name": "CONTEXT",
                            "type": "RGTHREE_CONTEXT",
                            "links": [5],
                        },
                    ],
                },
                {
                    "id": 5,
                    "type": "sampler-subgraph",
                    "inputs": [{"name": "base_ctx", "link": 5}],
                },
            ],
            "links": [
                [1, 1, 0, 2, 0, "STRING"],
                [2, 1, 0, 4, 0, "STRING"],
                [3, 1, 1, 3, 0, "STRING"],
                [4, 1, 1, 4, 1, "STRING"],
                [5, 4, 0, 5, 0, "RGTHREE_CONTEXT"],
            ],
            "definitions": {
                "subgraphs": [
                    {
                        "id": "context-subgraph",
                        "name": "Prompt Context",
                        "nodes": [],
                        "links": [],
                    },
                    {
                        "id": "sampler-subgraph",
                        "name": "Main KSampler",
                        "nodes": [{"id": 10, "type": "KSampler"}],
                        "links": [],
                    },
                ],
            },
        }

        guess = infer_workflow_prompts(json.dumps(workflow), "", "auto")

        self.assertEqual(guess.positive, "current cat")
        self.assertEqual(guess.negative, "current bad")

    def test_decode_delimiter_supports_newline_escape(self) -> None:
        self.assertEqual(decode_delimiter("\\n---\\n"), "\n---\n")

    def test_describe_workflow_node_by_id(self) -> None:
        prompt = {
            "6": {
                "class_type": "CLIPTextEncode",
                "inputs": {"text": "cat"},
                "_meta": {"title": "positive"},
            }
        }

        report = describe_workflow_node("", json.dumps(prompt), "6")

        self.assertIn("CLIPTextEncode", report)
        self.assertIn("cat", report)


if __name__ == "__main__":
    unittest.main()
