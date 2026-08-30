"""Guards against injecting untrusted data into JS event-handler attributes.

Discovery concepts are derived from Amazon book titles, Reddit post titles
and YouTube autocomplete — all of which anyone can publish. Those strings
reached the page as:

    onclick="validateConcept('${esc(phrase).replace(/'/g, "\\'")}')"

The escaping order made that exploitable. `esc()` turns ' into &#39;, so the
`.replace(/'/g, ...)` that follows finds no raw quote to escape. The HTML
parser then decodes &#39; back into ' *before* the JS is parsed, so a title
like `Cook'); alert(document.domain); //` breaks out of the string literal
and runs.

HTML-escaping is the wrong tool for a JavaScript context. The fix is to
keep data out of code entirely: put it in a data- attribute and bind the
handler in script. These tests assert the whole class stays gone rather
than just the one line that was reported.
"""

import os
import re
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

INDEX = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     "server", "static", "index.html")

# Any inline handler attribute in the rendered markup.
HANDLER = re.compile(r'\bon[a-z]+\s*=\s*"([^"]*)"')


@pytest.fixture(scope="module")
def page() -> str:
    with open(INDEX, encoding="utf-8") as fh:
        return fh.read()


class TestNoDataInEventHandlerAttributes:
    def test_no_inline_handler_interpolates_a_template_value(self, page):
        offenders = [h for h in HANDLER.findall(page) if "${" in h]
        assert offenders == [], (
            "inline handlers must not be built from interpolated data; "
            f"found {len(offenders)}: {offenders[:3]}")

    def test_the_reported_validate_button_no_longer_builds_js(self, page):
        assert "validateConcept('${" not in page
        assert 'onclick="validateConcept(' not in page

    def test_the_concept_phrase_travels_as_a_data_attribute(self, page):
        assert "data-phrase=" in page, "the phrase should ride in a data- attribute"

    def test_handlers_are_bound_in_script_not_markup(self, page):
        assert "addEventListener" in page


class TestEscapingIsStillAppliedToTextContent:
    """Removing inline handlers must not remove HTML escaping elsewhere."""

    def test_the_escape_helper_still_exists(self, page):
        assert "const esc =" in page

    def test_the_escape_helper_covers_every_dangerous_character(self, page):
        helper = page[page.index("const esc ="):page.index("const fmt =")]
        for char in ("&", "<", ">", '"', "'"):
            assert char in helper, f"esc() no longer handles {char!r}"

    def test_untrusted_concept_text_is_still_escaped_where_rendered(self, page):
        assert "esc(c.concept)" in page


class TestPayloadWouldNotEscapeTheStringLiteral:
    """A direct check of the escaping order that made this exploitable."""

    def test_html_escape_then_quote_replace_is_not_used_anywhere(self, page):
        # esc() emits &#39; so a following .replace(/'/g, ...) is always a no-op
        assert not re.search(r"esc\([^)]*\)\.replace\(/'", page), \
            "HTML-escaping before quote-escaping is the bug that caused this"
