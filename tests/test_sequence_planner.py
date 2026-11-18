from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sequence_planner import PlanValidationError, extract_json_object, normalize_plan


from plan_fixture import BASE_PROMPT, REF_PROMPT, clip


class SequencePlannerTest(unittest.TestCase):
    def test_extracts_json_from_fenced_model_response(self) -> None:
        value = extract_json_object('result follows\n```json\n{"scenes": []}\n```')
        self.assertEqual(value, {"scenes": []})

    def test_prefers_json_inside_content_after_visible_thought(self) -> None:
        value = extract_json_object(
            '<thought>Ignore this legacy example: {"wrong": true}</thought>\n'
            '<content>{"scenes": []}</content>'
        )
        self.assertEqual(value, {"scenes": []})

    def test_rejects_unclosed_content_envelope(self) -> None:
        with self.assertRaises(PlanValidationError) as caught:
            extract_json_object('<thought>plan first</thought><content>{"scenes": []}')
        self.assertIn("unclosed <content>", str(caught.exception))

    def test_same_scene_continuation_and_new_scene_reset(self) -> None:
        plan = {
            "project_summary": "two scenes",
            "scenes": [
                {
                    "scene_id": "scene_001",
                    "scene_summary": "room",
                    "canonical_reference_ids": ["actor"],
                    "clips": [
                        clip("clip_001", continues=True),
                        clip(
                            "clip_002",
                            parent="clip_001",
                            from_previous=True,
                        ),
                    ],
                },
                {
                    "scene_id": "scene_002",
                    "scene_summary": "street",
                    "canonical_reference_ids": ["actor"],
                    "clips": [clip("clip_003")],
                },
            ],
        }
        normalized = normalize_plan(
            plan,
            asset_catalog=[{"asset_id": "actor", "type": "image"}],
        )
        second_scene = normalized["scenes"][1]["clips"][0]
        self.assertIsNone(second_scene["parent_clip_id"])
        self.assertFalse(second_scene["continue_from_previous"])
        continued = normalized["scenes"][0]["clips"][1]
        self.assertEqual(continued["generation_branch"], "base")
        self.assertEqual(continued["reference_assets"], [])

    def test_rejects_cross_scene_video_inheritance(self) -> None:
        bad = {
            "scenes": [{
                "scene_id": "scene_002",
                "canonical_reference_ids": [],
                "clips": [clip(
                    "clip_002",
                    parent="clip_001",
                    from_previous=True,
                )],
            }],
        }
        with self.assertRaises(PlanValidationError) as caught:
            normalize_plan(bad)
        self.assertIn("scene boundary", str(caught.exception))

    def test_rejects_unknown_asset_id(self) -> None:
        bad_clip = clip("clip_001")
        bad_clip["first_frame_asset_id"] = "missing"
        bad = {
            "scenes": [{
                "scene_id": "scene_001",
                "canonical_reference_ids": [],
                "clips": [bad_clip],
            }],
        }
        with self.assertRaises(PlanValidationError) as caught:
            normalize_plan(bad, asset_catalog=[])
        self.assertIn("must reference an image in asset_catalog", str(caught.exception))

    def test_rejects_reference_assets_in_fixed_frame_chain(self) -> None:
        bad = {
            "scenes": [{
                "scene_id": "scene_001",
                "canonical_reference_ids": [],
                "clips": [clip(
                    "clip_001",
                    references=[{
                        "asset_id": "long_video",
                        "type": "video_audio",
                        "purpose": "motion",
                    }],
                )],
            }],
        }
        catalog = [{"asset_id": "long_video", "type": "video_audio", "duration_sec": 16}]
        with self.assertRaises(PlanValidationError) as caught:
            normalize_plan(bad, asset_catalog=json.dumps(catalog))
        self.assertIn("reference_assets must be empty", str(caught.exception))

    def test_rejects_ref2va_branch(self) -> None:
        bad = {
            "scenes": [{
                "scene_id": "scene_001",
                "canonical_reference_ids": [],
                "clips": [clip("clip_001", branch="ref2va")],
            }],
        }
        with self.assertRaises(PlanValidationError) as caught:
            normalize_plan(bad)
        self.assertIn("generation_branch must be 'base'", str(caught.exception))

if __name__ == "__main__":
    unittest.main()
