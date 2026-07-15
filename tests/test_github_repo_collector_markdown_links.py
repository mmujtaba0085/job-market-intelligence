"""
tests/test_github_repo_collector_markdown_links.py
─────────────────────────────────────────────────────
Regression test for a real bug: _strip_markdown_links() used a regex
(\\[([^\\]]+)\\]\\(...\\)) that stops at the FIRST ']', so a link whose own
text contains a nested [tag] (e.g. Jobright's "[Online Assessment]"/"[US]"
prefixes) never matched at all - the whole [text](url) was left completely
unstripped, verbatim, in the job title and description. Confirmed live in
production data for github_jobright_2026 before this fix.
"""
from src.collectors.github_repo_collector import GitHubJobright2026Collector

_collector = GitHubJobright2026Collector.__new__(GitHubJobright2026Collector)


def test_nested_bracket_link_text_is_stripped_correctly():
    raw = "[[Online Assessment] Software Engineer Intern (NoSQL Databases) - 2026 Summer (BS/MS)](https://jobright.ai/jobs/info/68c8)"
    result = _collector._strip_markdown_links(raw)
    assert result == "[Online Assessment] Software Engineer Intern (NoSQL Databases) - 2026 Summer (BS/MS)"
    assert "https://" not in result


def test_simple_link_still_strips_correctly():
    assert _collector._strip_markdown_links("[Senior Engineer](https://example.com/job)") == "Senior Engineer"


def test_bold_wrapped_link_still_strips_correctly():
    assert _collector._strip_markdown_links("**[Senior Engineer](https://example.com/job)**") == "Senior Engineer"


def test_non_link_brackets_are_left_untouched():
    assert _collector._strip_markdown_links("Some [US] Remote text") == "Some [US] Remote text"


def test_empty_string_returns_empty():
    assert _collector._strip_markdown_links("") == ""


def test_plain_bold_text_still_stripped():
    assert _collector._strip_markdown_links("**Senior Engineer**") == "Senior Engineer"
