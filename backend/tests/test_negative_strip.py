import pytest

from app.providers.images import IMAGE_POSITIVE_SUFFIX, strip_negative_blocks


@pytest.mark.parametrize("prompt,expected", [
    # Same line: the block runs to the end of the line, even across sentences.
    ("Kitchen. NEGATIVE: no blur. Camera locked, eye level.", "Kitchen."),
    # The next line is a separate thought and is kept.
    ("Kitchen.\nNEGATIVE: no blur.\nCamera locked, eye level.", "Kitchen.\nCamera locked, eye level."),
    ("Kitchen. NEGATIVE: no blur.\n\nCamera locked, eye level.", "Kitchen.\n\nCamera locked, eye level."),
    # Label alone on its line: the following list is removed up to a blank line.
    ("Kitchen.\n\nNEGATIVE:\n- no blur\n- no text\n\nCamera locked.", "Kitchen.\n\nCamera locked."),
    # ...or up to the next labelled section.
    ("SCENE: kitchen\nNEGATIVE:\nno blur\nno watermark\nLIGHTING: warm", "SCENE: kitchen\nLIGHTING: warm"),
    ("Wide shot! NEGATIVE: split image, collage", "Wide shot!"),
    ("NEGATIVE: no people", ""),
    ("  NEGATIVE : no people\nA calm lake.", "A calm lake."),
    # Not a label: lowercase, or mid-sentence.
    ("A photo negative: inverted colours.", "A photo negative: inverted colours."),
    ("A strip of NEGATIVE: film on a table.", "A strip of NEGATIVE: film on a table."),
    ("Plain prompt with  double  spaces.", "Plain prompt with double spaces."),
])
def test_strip_negative_blocks(prompt, expected):
    assert strip_negative_blocks(prompt) == expected


def test_multiple_blocks():
    prompt = ("Image 1: empty garage. NEGATIVE: no cars.\n"
              "Workers in overalls.\n"
              "NEGATIVE:\n* no logos\n\n"
              "Golden hour light.")
    assert strip_negative_blocks(prompt) == "Image 1: empty garage.\nWorkers in overalls.\n\nGolden hour light."


def test_positive_suffix_has_no_negative_wording():
    lowered = IMAGE_POSITIVE_SUFFIX.lower()
    for word in (" no ", "not ", "without", "never", "absolutely no", "divisions", "borders", "panels"):
        assert word not in lowered
