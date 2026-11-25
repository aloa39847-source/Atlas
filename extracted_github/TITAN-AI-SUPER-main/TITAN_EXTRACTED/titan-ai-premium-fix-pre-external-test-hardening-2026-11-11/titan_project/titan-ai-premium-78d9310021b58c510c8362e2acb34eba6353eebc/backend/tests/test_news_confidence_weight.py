from app.engines.decision import DecisionEngine


def test_news_weight_is_capped_at_eight_percent_and_uses_official_fresh_items_only():
    engine = DecisionEngine()
    positive = engine._news_confidence_adjustment({
        "ok": True,
        "items": [
            {"source_type":"official_primary","freshness":"fresh",
             "sentiment_score":1.0,"sentiment_confidence":1.0},
            {"source_type":"secondary_media","freshness":"fresh",
             "sentiment_score":-1.0,"sentiment_confidence":1.0},
            {"source_type":"official_primary","freshness":"stale",
             "sentiment_score":-1.0,"sentiment_confidence":1.0},
        ],
    })
    assert positive["adjustment"] == 0.08
    assert positive["used_items"] == 1
    assert positive["source_policy"] == "official_primary_only"


def test_negative_official_news_reduces_confidence_without_creating_trade_action():
    engine = DecisionEngine()
    negative = engine._news_confidence_adjustment({
        "ok": True,
        "items": [
            {"source_type":"official_primary","freshness":"fresh",
             "sentiment_score":-1.0,"sentiment_confidence":0.75},
        ],
    })
    assert negative["adjustment"] == -0.06
    assert negative["adjustment"] >= -0.08


def test_secondary_only_or_unavailable_news_has_zero_decision_weight():
    engine = DecisionEngine()
    secondary = engine._news_confidence_adjustment({
        "ok": True,
        "items": [{"source_type":"secondary_media","freshness":"fresh",
                   "sentiment_score":1.0,"sentiment_confidence":1.0}],
    })
    unavailable = engine._news_confidence_adjustment({"ok":False,"items":[]})
    assert secondary["adjustment"] == 0.0
    assert unavailable["adjustment"] == 0.0
