from __future__ import annotations

import json
from typing import Any


PLAN_SCHEMA_VERSION = "1.0"
H3_BASE_PROMPT_SECTIONS = (
    "integrated_multimodal_description",
    "overall_soundscape",
    "non_diegetic_music",
)
H3_REF_PROMPT_SECTIONS = (
    "subject_definitions",
    "summary",
    "retention_analysis",
    "detailed_description",
    "overall_soundscape",
    "non_diegetic_music",
)
ASSET_TYPES = {"image", "video", "video_audio", "audio"}


class PlanValidationError(ValueError):
    def __init__(self, errors: list[str]) -> None:
        self.errors = errors
        super().__init__("; ".join(errors))


def _decode_first_json_object(text: str) -> dict[str, Any] | None:
    decoder = json.JSONDecoder()
    for index, char in enumerate(text):
        if char != "{":
            continue
        try:
            value, _end = decoder.raw_decode(text[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    return None


def extract_json_object(text: str) -> dict[str, Any]:
    """Extract the plan JSON, preferring the explicit content response envelope."""
    content_start = text.find("<content>")
    if content_start >= 0:
        content_start += len("<content>")
        content_end = text.find("</content>", content_start)
        if content_end < 0:
            raise PlanValidationError(["model response contains an unclosed <content> block"])
        value = _decode_first_json_object(text[content_start:content_end])
        if value is None:
            raise PlanValidationError(["model response <content> block does not contain a valid JSON object"])
        return value

    value = _decode_first_json_object(text)
    if value is not None:
        return value
    raise PlanValidationError(["model response does not contain a valid JSON object"])


def parse_asset_catalog(raw: Any) -> dict[str, dict[str, Any]]:
    if raw in (None, ""):
        return {}
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise PlanValidationError([f"asset_catalog is invalid JSON: {exc.msg}"]) from exc
    if isinstance(raw, dict) and isinstance(raw.get("assets"), list):
        raw = raw["assets"]
    if not isinstance(raw, list):
        raise PlanValidationError(["asset_catalog must be a JSON array or an object with an assets array"])

    errors: list[str] = []
    assets: dict[str, dict[str, Any]] = {}
    for index, item in enumerate(raw):
        label = f"asset_catalog[{index}]"
        if not isinstance(item, dict):
            errors.append(f"{label} must be an object")
            continue
        asset_id = str(item.get("asset_id") or item.get("id") or "").strip()
        kind = str(item.get("type") or "").strip()
        if not asset_id:
            errors.append(f"{label}.asset_id is required")
            continue
        if asset_id in assets:
            errors.append(f"duplicate asset_id: {asset_id}")
            continue
        if kind not in ASSET_TYPES:
            errors.append(f"{label}.type must be one of {sorted(ASSET_TYPES)}")
            continue
        duration = item.get("duration_sec")
        if duration is not None:
            try:
                duration = float(duration)
            except (TypeError, ValueError):
                errors.append(f"{label}.duration_sec must be a number")
                continue
            if duration <= 0:
                errors.append(f"{label}.duration_sec must be positive")
                continue
        assets[asset_id] = {
            "asset_id": asset_id,
            "type": kind,
            "description": str(item.get("description") or "").strip(),
            "duration_sec": duration,
        }
    if errors:
        raise PlanValidationError(errors)
    return assets


def _string_list(value: Any, path: str, errors: list[str]) -> list[str]:
    if not isinstance(value, list):
        errors.append(f"{path} must be an array")
        return []
    result: list[str] = []
    for index, item in enumerate(value):
        text = str(item or "").strip()
        if not text:
            errors.append(f"{path}[{index}] must be a non-empty string")
        else:
            result.append(text)
    return result


def _validate_h3_prompt(prompt: str, branch: str, path: str, errors: list[str]) -> None:
    sections = H3_REF_PROMPT_SECTIONS if branch == "ref2va" else H3_BASE_PROMPT_SECTIONS
    positions: list[int] = []
    for section in sections:
        position = prompt.find(f"{section}:")
        if position < 0:
            errors.append(f"{path} is missing H3 section {section}:")
        positions.append(position)
    present = [position for position in positions if position >= 0]
    if present and present != sorted(present):
        errors.append(f"{path} H3 sections are not in the official order")


def _validate_reference_limits(
    references: list[dict[str, Any]],
    assets: dict[str, dict[str, Any]],
    path: str,
    errors: list[str],
) -> None:
    image_count = 0
    video_count = 0
    audio_count = 0
    video_seconds = 0.0
    audio_seconds = 0.0
    has_visual = False
    for index, reference in enumerate(references):
        ref_path = f"{path}[{index}]"
        asset_id = str(reference.get("asset_id") or "").strip()
        kind = str(reference.get("type") or "").strip()
        if not asset_id:
            errors.append(f"{ref_path}.asset_id is required")
            continue
        generated = asset_id.startswith("generated:")
        asset = assets.get(asset_id)
        if not generated and asset is None:
            errors.append(f"{ref_path}.asset_id is not present in asset_catalog: {asset_id}")
            continue
        expected_kind = "video_audio" if generated else str(asset["type"])
        if kind not in ASSET_TYPES:
            errors.append(f"{ref_path}.type must be one of {sorted(ASSET_TYPES)}")
            continue
        if kind != expected_kind:
            errors.append(
                f"{ref_path}.type {kind!r} does not match asset {asset_id!r} type {expected_kind!r}"
            )
            continue
        duration = None if generated else asset.get("duration_sec")
        if generated:
            try:
                duration = float(reference.get("duration_sec"))
            except (TypeError, ValueError):
                errors.append(f"{ref_path}.duration_sec is required for generated video references")
                continue
        if kind == "image":
            image_count += 1
            has_visual = True
        elif kind == "video":
            video_count += 1
            has_visual = True
            if duration is not None:
                video_seconds += float(duration)
        elif kind == "video_audio":
            video_count += 1
            audio_count += 1
            has_visual = True
            if duration is not None:
                video_seconds += float(duration)
                audio_seconds += float(duration)
        elif kind == "audio":
            audio_count += 1
            if duration is not None:
                audio_seconds += float(duration)

    if len(references) > 12:
        errors.append(f"{path} exceeds the 12 mixed-reference limit")
    if image_count > 9:
        errors.append(f"{path} exceeds the 9-image limit")
    if video_count > 3:
        errors.append(f"{path} exceeds the 3-video limit")
    if audio_count > 3:
        errors.append(f"{path} exceeds the 3-audio limit")
    if video_seconds > 15.001:
        errors.append(f"{path} exceeds the 15-second total reference-video limit")
    if audio_seconds > 15.001:
        errors.append(f"{path} exceeds the 15-second total reference-audio limit")
    if audio_count and not has_visual:
        errors.append(f"{path} cannot contain audio as the only reference type")


def normalize_plan(
    raw: str | dict[str, Any],
    *,
    asset_catalog: Any = None,
    min_clip_seconds: float = 5.0,
    max_clip_seconds: float = 15.0,
) -> dict[str, Any]:
    plan = extract_json_object(raw) if isinstance(raw, str) else raw
    if not isinstance(plan, dict):
        raise PlanValidationError(["plan must be a JSON object"])
    assets = parse_asset_catalog(asset_catalog)
    errors: list[str] = []
    scenes = plan.get("scenes")
    if not isinstance(scenes, list) or not scenes:
        raise PlanValidationError(["plan.scenes must be a non-empty array"])

    seen_scene_ids: set[str] = set()
    seen_clip_ids: set[str] = set()
    normalized_scenes: list[dict[str, Any]] = []
    project_clip_index = 0
    for scene_index, scene in enumerate(scenes):
        scene_path = f"scenes[{scene_index}]"
        if not isinstance(scene, dict):
            errors.append(f"{scene_path} must be an object")
            continue
        scene_id = str(scene.get("scene_id") or "").strip()
        if not scene_id:
            errors.append(f"{scene_path}.scene_id is required")
        elif scene_id in seen_scene_ids:
            errors.append(f"duplicate scene_id: {scene_id}")
        seen_scene_ids.add(scene_id)
        canonical_ids = _string_list(
            scene.get("canonical_reference_ids", []),
            f"{scene_path}.canonical_reference_ids",
            errors,
        )
        for asset_id in canonical_ids:
            if asset_id not in assets:
                errors.append(
                    f"{scene_path}.canonical_reference_ids contains unknown asset_id: {asset_id}"
                )
        clips = scene.get("clips")
        if not isinstance(clips, list) or not clips:
            errors.append(f"{scene_path}.clips must be a non-empty array")
            continue

        normalized_clips: list[dict[str, Any]] = []
        previous_clip_id: str | None = None
        for clip_index, clip in enumerate(clips):
            clip_path = f"{scene_path}.clips[{clip_index}]"
            if not isinstance(clip, dict):
                errors.append(f"{clip_path} must be an object")
                continue
            project_clip_index += 1
            clip_id = str(clip.get("clip_id") or "").strip()
            if not clip_id:
                errors.append(f"{clip_path}.clip_id is required")
            elif clip_id in seen_clip_ids:
                errors.append(f"duplicate clip_id: {clip_id}")
            seen_clip_ids.add(clip_id)

            parent = clip.get("parent_clip_id")
            parent = str(parent).strip() if parent is not None else None
            continue_previous = clip.get("continue_from_previous")
            if not isinstance(continue_previous, bool):
                errors.append(f"{clip_path}.continue_from_previous must be boolean")
                continue_previous = False
            if clip_index == 0:
                if parent is not None:
                    errors.append(f"{clip_path}.parent_clip_id must be null at a scene boundary")
                if continue_previous:
                    errors.append(f"{clip_path}.continue_from_previous must be false at a scene boundary")
            else:
                if parent != previous_clip_id:
                    errors.append(f"{clip_path}.parent_clip_id must equal {previous_clip_id!r}")
                if not continue_previous:
                    errors.append(f"{clip_path}.continue_from_previous must be true inside an unfinished scene")

            scene_continues = clip.get("scene_continues")
            expected_continues = clip_index < len(clips) - 1
            if not isinstance(scene_continues, bool):
                errors.append(f"{clip_path}.scene_continues must be boolean")
                scene_continues = expected_continues
            elif scene_continues != expected_continues:
                errors.append(
                    f"{clip_path}.scene_continues must be {str(expected_continues).lower()} for its position"
                )

            branch = str(clip.get("generation_branch") or "").strip().lower()
            if branch != "base":
                errors.append(f"{clip_path}.generation_branch must be 'base'")

            try:
                duration = float(clip.get("target_duration_sec"))
            except (TypeError, ValueError):
                errors.append(f"{clip_path}.target_duration_sec must be a number")
                duration = min_clip_seconds
            if not min_clip_seconds <= duration <= max_clip_seconds:
                errors.append(
                    f"{clip_path}.target_duration_sec must be between {min_clip_seconds:g} and {max_clip_seconds:g}"
                )

            first_frame_id = clip.get("first_frame_asset_id")
            first_frame_id = str(first_frame_id).strip() if first_frame_id is not None else None
            last_frame_id = clip.get("last_frame_asset_id")
            last_frame_id = str(last_frame_id).strip() if last_frame_id is not None else None
            for field, asset_id in (
                ("first_frame_asset_id", first_frame_id),
                ("last_frame_asset_id", last_frame_id),
            ):
                if asset_id and (asset_id not in assets or assets[asset_id]["type"] != "image"):
                    errors.append(f"{clip_path}.{field} must reference an image in asset_catalog")
            if project_clip_index > 1 and first_frame_id:
                errors.append(
                    f"{clip_path}.first_frame_asset_id must be null after the first project clip; "
                    "the executor injects the previous H3 last frame"
                )

            raw_references = clip.get("reference_assets", [])
            if not isinstance(raw_references, list):
                errors.append(f"{clip_path}.reference_assets must be an array")
            elif raw_references:
                errors.append(
                    f"{clip_path}.reference_assets must be empty; continuity uses the previous H3 last frame"
                )
            references: list[dict[str, Any]] = []

            h3_prompt = str(clip.get("h3_prompt") or "").strip()
            if not h3_prompt:
                errors.append(f"{clip_path}.h3_prompt is required")
            else:
                _validate_h3_prompt(h3_prompt, branch, f"{clip_path}.h3_prompt", errors)
            beats = _string_list(clip.get("beats", []), f"{clip_path}.beats", errors)
            reserved = _string_list(
                clip.get("reserved_beats", []), f"{clip_path}.reserved_beats", errors
            )
            reason = str(clip.get("continuation_reason") or "").strip()
            if continue_previous and not reason:
                errors.append(f"{clip_path}.continuation_reason is required for continuation")

            normalized_clips.append({
                "clip_id": clip_id,
                "parent_clip_id": parent,
                "scene_continues": scene_continues,
                "continue_from_previous": continue_previous,
                "continuation_reason": reason,
                "generation_branch": branch,
                "target_duration_sec": duration,
                "first_frame_asset_id": first_frame_id,
                "last_frame_asset_id": last_frame_id,
                "reference_assets": references,
                "h3_prompt": h3_prompt,
                "beats": beats,
                "reserved_beats": reserved,
            })
            previous_clip_id = clip_id

        normalized_scenes.append({
            "scene_id": scene_id,
            "scene_summary": str(scene.get("scene_summary") or "").strip(),
            "canonical_reference_ids": canonical_ids,
            "clips": normalized_clips,
        })

    if errors:
        raise PlanValidationError(errors)
    return {
        "schema_version": PLAN_SCHEMA_VERSION,
        "project_summary": str(plan.get("project_summary") or "").strip(),
        "scenes": normalized_scenes,
    }
