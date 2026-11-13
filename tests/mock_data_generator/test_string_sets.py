"""Generated DynamoDB sets never repeat a member.

DynamoDB rejects a string or number set that repeats a member, and one bad item fails the whole
batch write. The seed then fails, and with it the CI deploy job that runs it. An article's tags mix
two random words with fixed tags, so a random word that matches a fixed tag ("news") used to break
the seed at random.
"""

from unittest.mock import patch

import pytest
from faker import Faker

from mock_data_generator import generator as generator_module
from mock_data_generator.config import GeneratorConfig
from mock_data_generator.generator import MockDataGenerator, _number_set, _string_set


@pytest.fixture
def generator():
    # Articles only: the tag collision this module guards against is in article tags, and with
    # the five default types a run can draw no article at all. Seeding can't prevent that, because
    # pytest-randomly reseeds ``random`` again right before each test body runs.
    config = GeneratorConfig(
        content_types=["article"],
        content_count=12,
        partner_count=1,
        access_log_count=1,
        job_count=1,
        date_range_months=1,
        aws_profile="test",
        aws_region="us-west-2",
    )
    with patch("boto3.Session"):
        return MockDataGenerator(config)


class _NewsFaker(Faker):
    """A Faker whose every random word is "news", the fixed article tag it used to collide with."""

    def word(self, *args, **kwargs):
        return "news"


def test_string_set_keeps_each_value_once_in_first_seen_order():
    assert _string_set(["a", "news", "b", "news", "a"]) == {"SS": ["a", "news", "b"]}


def test_an_empty_list_is_not_written_as_an_empty_set():
    assert _string_set([]) == {"L": []}


def test_article_tags_that_repeat_a_random_word_are_stored_once(generator):
    with patch.object(generator_module, "Faker", _NewsFaker):
        items = generator.generate_content_data()

    assert items and all(item["content_type"]["S"] == "article" for item in items)
    for item in items:
        tags = item["tags"]["SS"]
        assert len(tags) == len(set(tags)), f"{item['content_id']['S']} repeats a tag: {tags}"
        assert tags.count("news") == 1


def test_converted_lists_never_repeat_a_set_member(generator):
    converted = generator._convert_dict_to_dynamodb_format({"ids": ["x", "x", "y"], "sizes": [1, 1, 2], "none": []})

    assert converted == {"ids": {"SS": ["x", "y"]}, "sizes": {"NS": ["1", "2"]}, "none": {"L": []}}


def test_number_set_members_are_compared_by_value():
    """DynamoDB treats 1 and 1.0 as the same member, so the set keeps the first form only."""
    assert _number_set([1, 1.0, 2.50, 2.5, 3]) == {"NS": ["1", "2.5", "3"]}
    assert _number_set([]) == {"L": []}
