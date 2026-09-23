import pytest

from app.pipelines.pack import video_frame_indices, videos_affected_by_image, videos_affected_by_video
from app.views import compute_steps


def test_chain_frames():
    assert video_frame_indices(4, 3, "veo31_frame") == [(0, 1), (1, 2), (2, 3)]


def test_chain_hero_reveal_frames():
    assert video_frame_indices(4, 4, "veo31_frame") == [(0, 1), (1, 2), (2, 3), (3, 3)]


def test_paired_bookend_frames():
    assert video_frame_indices(6, 3, "veo31_frame") == [(0, 1), (2, 3), (4, 5)]


def test_extend_frames():
    assert video_frame_indices(1, 3, "veo_extend") == [(0, None), (None, None), (None, None)]


@pytest.mark.parametrize("legacy", ["grok_sequential_extend", "veo31_sequential_extend"])
def test_legacy_extend_systems_are_veo_extend(legacy):
    assert video_frame_indices(1, 2, legacy) == [(0, None), (None, None)]
    assert videos_affected_by_video(3, legacy, 0) == [0, 1, 2]


def test_legacy_null_video_system_is_frame_mode():
    assert video_frame_indices(3, 2, None) == [(0, 1), (1, 2)]


def test_affected_videos():
    assert videos_affected_by_image(4, 3, "veo31_frame", 1) == [0, 1]
    assert videos_affected_by_image(4, 3, "veo31_frame", 0) == [0]
    assert videos_affected_by_image(4, 4, "veo31_frame", 3) == [2, 3]
    assert videos_affected_by_image(6, 3, "veo31_frame", 3) == [1]
    assert videos_affected_by_image(1, 3, "veo_extend", 0) == [0, 1, 2]
    assert videos_affected_by_video(3, "veo31_frame", 1) == [1]
    assert videos_affected_by_video(4, "veo_extend", 1) == [1, 2, 3]


def _statuses(p):
    return [s["status"] for s in compute_steps(p)]


def test_pack_steps():
    base = {"pipeline_kind": "paste", "nextcloud_url": None}
    assert _statuses({**base, "status": "running", "current_step": "generating_videos",
                      "resume_from_step": "generating_videos"}) == ["done", "active", "pending", "pending"]
    assert _statuses({**base, "status": "failed", "current_step": "combining_videos",
                      "resume_from_step": "combining_videos"}) == ["done", "done", "failed", "pending"]
    assert _statuses({**base, "status": "paused", "current_step": "paused",
                      "resume_from_step": "generating_images"}) == ["paused", "pending", "pending", "pending"]
    assert _statuses({**base, "status": "completed", "current_step": "done"}) == ["done", "done", "done", "skipped"]
    assert _statuses({**base, "status": "completed", "nextcloud_url": "https://x"}) == ["done"] * 4
    assert _statuses({**base, "status": "queued", "current_step": "queued",
                      "resume_from_step": "uploading_nextcloud"}) == ["done", "done", "done", "pending"]
    assert _statuses({**base, "status": "cancelled", "current_step": "cancelled",
                      "resume_from_step": "generating_videos"}) == ["done", "failed", "pending", "pending"]
    keys = [s["key"] for s in compute_steps({**base, "status": "pending"})]
    assert keys == ["generating_images", "generating_videos", "combining_videos", "uploading"]


def test_script_steps():
    p = {"pipeline_kind": "script", "status": "running", "current_step": "generating_scene_images",
         "resume_from_step": "generating_scene_images"}
    steps = compute_steps(p)
    assert [s["key"] for s in steps] == [
        "generating_tts", "estimating_word_timings", "splitting_scenes", "generating_scene_prompts",
        "generating_scene_images", "building_scene_clips", "final_mux", "uploading"]
    assert [s["status"] for s in steps] == ["done"] * 4 + ["active"] + ["pending"] * 3
    # Legacy script rows used 'generating_images' and never updated resume_from_step on failure.
    legacy = {"pipeline_kind": "script", "status": "failed", "current_step": "generating_images",
              "resume_from_step": "generating_tts"}
    assert _statuses(legacy)[4] == "failed"
    assert all(s["label"] for s in steps)
