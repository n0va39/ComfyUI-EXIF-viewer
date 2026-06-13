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
