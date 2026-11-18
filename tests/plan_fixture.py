"""Synthetic data shared by validator unit tests and the independent wire oracle."""
BASE_PROMPT = """integrated_multimodal_description:
[Shot 1] The actor crosses the room in one continuous shot.

overall_soundscape:
Footsteps and quiet room tone.

non_diegetic_music:
N/A"""


REF_PROMPT = """subject_definitions:
<Subject 1> is the actor.

summary:
[reference generation] The actor crosses the room.

retention_analysis:
<Subject 1>: fully_preserved - identity remains stable.

detailed_description:
[Shot 1] The actor crosses the room in one continuous shot.

overall_soundscape:
Footsteps and quiet room tone.

non_diegetic_music:
N/A"""


def clip(
    clip_id: str,
    *,
    parent: str | None = None,
    continues: bool = False,
    from_previous: bool = False,
    branch: str = "base",
    references: list[dict] | None = None,
) -> dict:
    return {
        "clip_id": clip_id,
        "parent_clip_id": parent,
        "scene_continues": continues,
        "continue_from_previous": from_previous,
        "continuation_reason": "same action continues" if from_previous else "",
        "generation_branch": branch,
        "target_duration_sec": 5,
        "first_frame_asset_id": None,
        "last_frame_asset_id": None,
        "reference_assets": references or [],
        "h3_prompt": REF_PROMPT if branch == "ref2va" else BASE_PROMPT,
        "beats": ["actor crosses room"],
        "reserved_beats": ["actor opens door"] if continues else [],
    }


