import asyncio
from datetime import datetime, timedelta

import server


def _comment(comment_id: str, created: str, content: str) -> dict:
    return {
        "id": comment_id,
        "created": created,
        "author": "阿峙",
        "kind": "feel",
        "content": content,
    }


def _bucket(comments: list[dict]) -> dict:
    return {
        "id": "bucket-1",
        "content": "宿主桶正文",
        "metadata": {
            "name": "宿主记忆",
            "comments": comments,
            "created": "2026-08-01T00:00:00+12:00",
        },
    }


def test_latest_year_rings_are_selected_by_created_time():
    bucket = _bucket(
        [
            _comment("old", "2026-08-01T09:00:00+12:00", "最老年轮"),
            _comment("new", "2026-08-03T09:00:00+12:00", "最新年轮"),
            _comment("middle", "2026-08-02T09:00:00+12:00", "中间年轮"),
        ]
    )

    selected = server._latest_year_ring_comments(bucket, limit=2)

    assert [item["id"] for item in selected] == ["new", "middle"]


def test_context_year_rings_use_newest_two_instead_of_oldest():
    seed = {
        "bucket_id": "bucket-1",
        "moment_id": "body",
        "section": "body",
        "ordinal": 0,
        "text": "正文",
    }
    moments = [
        seed,
        {"bucket_id": "bucket-1", "moment_id": "old", "section": "comment", "ordinal": 1, "created_at": "2026-08-01"},
        {"bucket_id": "bucket-1", "moment_id": "new", "section": "comment", "ordinal": 3, "created_at": "2026-08-03"},
        {"bucket_id": "bucket-1", "moment_id": "middle", "section": "comment", "ordinal": 2, "created_at": "2026-08-02"},
    ]

    contexts = server._context_moments_for_seed(seed, {"bucket-1": moments})
    comment_ids = [item["moment_id"] for item in contexts if item["section"] == "comment"]

    assert comment_ids == ["new", "middle"]


def test_dehydration_excludes_year_rings_but_embedding_keeps_them():
    bucket = _bucket([_comment("new", "2026-08-03T09:00:00+12:00", "只在年轮里的检索暗号")])

    assert "只在年轮里的检索暗号" in server._bucket_text_for_embedding(bucket)
    assert "只在年轮里的检索暗号" not in server._bucket_text_for_dehydration(bucket)
    assert "comments" not in server._bucket_metadata_for_dehydration(bucket)


def test_direct_bucket_appends_latest_year_rings_with_metadata():
    bucket = _bucket(
        [
            _comment("old", "2026-08-01T09:00:00+12:00", "最老年轮"),
            _comment("middle", "2026-08-02T09:00:00+12:00", "中间年轮"),
            _comment("new", "2026-08-03T09:00:00+12:00", "最新年轮"),
        ]
    )
    moment = {
        "bucket_id": "bucket-1",
        "moment_id": "body",
        "section": "body",
        "ordinal": 0,
        "text": "宿主桶正文",
        "metadata": {"bucket_name": "宿主记忆"},
    }

    rendered = asyncio.run(
        server._format_direct_bucket(
            bucket,
            moment,
            {"bucket-1": [moment]},
            2000,
            query_text="宿主记忆",
        )
    )

    assert "最新年轮" in rendered
    assert "中间年轮" in rendered
    assert "最老年轮" not in rendered
    assert "2026-08-03 · 阿峙 · feel" in rendered


def test_handoff_year_rings_use_comment_time_and_backfill_older_comments():
    now = datetime.now(server._handoff_timezone())
    recent = now - timedelta(hours=2)
    older_recent = now - timedelta(days=2)
    stale = now - timedelta(days=9)
    buckets = [
        {
            "id": "old-host",
            "content": "很早以前的正文",
            "metadata": {
                "name": "旧宿主",
                "created": "2025-01-01T00:00:00+12:00",
                "comments": [
                    _comment("recent", recent.isoformat(), "今天补给旧桶的新年轮"),
                    _comment("stale", stale.isoformat(), "九天前的年轮"),
                ],
            },
        },
        {
            "id": "other-host",
            "content": "另一个正文",
            "metadata": {
                "name": "另一个宿主",
                "comments": [_comment("older", older_recent.isoformat(), "两天前的年轮")],
            },
        },
    ]

    rendered = server._format_handoff_recent_year_rings(buckets, limit=3, max_chars=90)

    assert rendered.index("今天补给旧桶的新年轮") < rendered.index("两天前的年轮")
    assert "[bucket_id:old-host]" in rendered
    assert rendered.index("两天前的年轮") < rendered.index("九天前的年轮")


def test_handoff_year_ring_section_keeps_three_compact_rows_within_budget():
    now = datetime.now(server._handoff_timezone())
    buckets = []
    for index in range(3):
        buckets.append(
            {
                "id": f"host-{index}",
                "content": "宿主正文",
                "metadata": {
                    "name": f"这是一个很长的宿主标题{index}",
                    "comments": [
                        _comment(
                            f"ring-{index}",
                            (now - timedelta(hours=index + 1)).isoformat(),
                            f"第{index}条年轮" + "很长的回看内容" * 20,
                        )
                    ],
                },
            }
        )

    rendered = server._format_handoff_recent_year_rings(
        buckets,
        limit=3,
        max_chars=90,
        token_budget=180,
    )

    assert len(rendered.splitlines()) == 3
    assert all(f"[bucket_id:host-{index}]" in rendered for index in range(3))
    assert server.count_tokens_approx(rendered) <= 180


def test_handoff_year_rings_include_comments_on_permanent_buckets():
    now = datetime.now(server._handoff_timezone())
    bucket = _bucket([_comment("permanent-ring", now.isoformat(), "钉选桶里的年轮")])
    bucket["metadata"]["type"] = "permanent"

    rendered = server._format_handoff_recent_year_rings([bucket])

    assert "钉选桶里的年轮" in rendered


def test_handoff_budget_keeps_continuity_and_recent_year_rings():
    recent_continuity = "\n".join(f"- 连续性{i}：" + "仍在继续" * 50 for i in range(3))
    recent_year_rings = "\n".join(f"- 🌀 年轮{i}：" + "新的回看" * 30 for i in range(3))
    sections = [
        ("Recent Continuity", recent_continuity, 650, True),
        ("最近年轮", recent_year_rings, 180, True),
        ("照顾备忘", "- 记得照顾小兔", 180, True),
    ]

    rendered = server._format_budgeted_handoff_sections("=== Handoff Context ===", sections, 1600)

    assert "=== Recent Continuity ===" in rendered
    assert "=== 最近年轮 ===" in rendered
    assert "年轮0" in rendered
    assert server.count_tokens_approx(rendered) <= 1600
