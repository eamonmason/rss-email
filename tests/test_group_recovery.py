"""Groups the model leaves out of a batch reply are re-requested once, not dropped."""
# pylint: disable=missing-function-docstring,redefined-outer-name

import json
import re
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from rss_email.retrieve_and_send_email import (
    MAX_RECOVERY_GROUPS,
    _recover_missing_groups,
    build_processed_articles_from_groups,
    lambda_handler,
)


def _articles(count):
    return [
        {"title": f"Story {i}", "link": f"https://x/{i}", "description": "d",
         "pubDate": "Mon, 10 Jan 2026 08:00:00 GMT", "sourceName": "Feed"}
        for i in range(count)
    ]


def _reply(group_ids, category="Technology"):
    entries = [
        {"group_id": g, "title": f"Story {g.split('_')[1]}", "summary": "s", "category": category}
        for g in group_ids
    ]
    return json.dumps({"categories": {category: entries}})


def _client(text):
    client = MagicMock()
    client.messages.create.return_value = SimpleNamespace(
        content=[SimpleNamespace(type="text", text=text)], stop_reason="end_turn",
        usage=SimpleNamespace(input_tokens=10, output_tokens=5),
    )
    return client


def test_covered_gids_are_collected_by_the_builder():
    covered = set()
    build_processed_articles_from_groups(json.loads(_reply(["group_0"])), _articles(2), [[0], [1]],
                                         seen_gids_out=covered)
    assert covered == {"group_0"}


def test_missing_group_is_recovered_and_merged():
    articles, groups = _articles(3), [[0], [1], [2]]
    covered, categories = {"group_0"}, {}
    client = _client(_reply(["group_1", "group_2"], "Business"))

    _recover_missing_groups(client, "claude-haiku-4-5", articles, groups, covered, categories)

    assert covered == {"group_0", "group_1", "group_2"}
    assert [a.title for a in categories["Business"]] == ["Story 1", "Story 2"]
    prompt = client.messages.create.call_args.kwargs["messages"][0]["content"]
    assert '"group_id": "group_1"' in prompt and '"group_id": "group_0"' not in prompt


def test_nothing_missing_means_no_call():
    client = _client("{}")
    _recover_missing_groups(client, "claude-haiku-4-5", _articles(2), [[0], [1]],
                            {"group_0", "group_1"}, {})
    client.messages.create.assert_not_called()


def test_recovery_is_bounded():
    count = MAX_RECOVERY_GROUPS + 30
    client = _client("not json")
    _recover_missing_groups(client, "claude-haiku-4-5", _articles(count),
                            [[i] for i in range(count)], set(), {})
    prompts = [c.kwargs["messages"][0]["content"] for c in client.messages.create.call_args_list]
    asked = sum(len(re.findall(r'"group_id": "group_\d+"', p)) for p in prompts)
    assert asked == MAX_RECOVERY_GROUPS


def test_recovery_never_raises():
    client = MagicMock()
    client.messages.create.side_effect = RuntimeError("API down")
    categories = {}
    _recover_missing_groups(client, "claude-haiku-4-5", _articles(2), [[0], [1]], set(), categories)
    assert not categories


def test_recovery_sends_effort_for_haiku_5_only():
    for model, expected in (("claude-haiku-5-5", {"effort": "medium"}), ("claude-haiku-4-5", None)):
        client = _client(_reply(["group_0"]))
        _recover_missing_groups(client, model, _articles(1), [[0]], set(), {})
        assert client.messages.create.call_args.kwargs.get("output_config") == expected


@patch("rss_email.retrieve_and_send_email.set_last_run")
@patch("rss_email.retrieve_and_send_email.send_via_ses")
@patch("rss_email.retrieve_and_send_email.create_html", return_value="<html/>")
@patch("rss_email.retrieve_and_send_email.retrieve_batch_metadata")
@patch("rss_email.retrieve_and_send_email.anthropic.Anthropic")
@patch("rss_email.retrieve_and_send_email.boto3.client")
def test_handler_recovers_a_group_the_batch_omitted(
    mock_boto3, mock_anthropic, mock_metadata, _html, _send, _last_run, monkeypatch
):
    for key, value in {
        "ANTHROPIC_API_KEY_PARAMETER": "p", "LAST_RUN_PARAMETER": "l",
        "SOURCE_EMAIL_ADDRESS": "from@x", "TO_EMAIL_ADDRESS": "to@x", "RSS_BUCKET": "b",
        "BRIEF_ENABLED": "false",
    }.items():
        monkeypatch.setenv(key, value)
    mock_boto3.return_value.get_parameter.return_value = {"Parameter": {"Value": "k"}}
    mock_metadata.return_value = (_articles(2), [[0], [1]])

    batch_result = MagicMock()
    batch_result.result.type = "succeeded"
    batch_result.result.message.content = [SimpleNamespace(type="text", text=_reply(["group_0"]))]
    batch_result.custom_id = "email-batch-0"
    client = mock_anthropic.return_value
    client.messages.batches.results.return_value = [batch_result]
    client.messages.create.return_value = SimpleNamespace(
        content=[SimpleNamespace(type="text", text=_reply(["group_1"], "Business"))],
        stop_reason="end_turn", usage=SimpleNamespace(input_tokens=10, output_tokens=5),
    )

    result = lambda_handler({"batch_id": "b", "metadata_key": "m", "request_counts": {}}, None)

    assert result["status"] == "success"
    assert result["categories_count"] == 2  # Technology (batch) + Business (recovered)
    client.messages.create.assert_called_once()
