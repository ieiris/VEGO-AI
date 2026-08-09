"""
Prompt loader for Agent 1 — Language Advisor.

All system prompts live in prompts.xml (one <prompt name="..."> block per phase/skill),
kept out of Python so they can be reviewed/edited without touching code. This module
parses that file once and hands back a string.Template per prompt name, ready for
.safe_substitute(...).
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from functools import lru_cache
from pathlib import Path
from string import Template

_PROMPTS_PATH = Path(__file__).parent / "prompts.xml"


@lru_cache(maxsize=None)
def _load_root() -> ET.Element:
    return ET.parse(_PROMPTS_PATH).getroot()


@lru_cache(maxsize=None)
def get_prompt_template(name: str) -> Template:
    """
    Return the system-prompt Template for the given <prompt name="..."> entry.

    Parameters
    ----------
    name : the "name" attribute of the <prompt> element, e.g. "build_language_template".

    Raises
    ------
    KeyError if no matching prompt (or no <system> body) is found.
    """
    root = _load_root()
    node = root.find(f"./prompt[@name='{name}']/system")
    if node is None or node.text is None:
        raise KeyError(f"No prompt named '{name}' found in {_PROMPTS_PATH}")
    # CDATA opens with a newline right after <![CDATA[ for readability in the XML file;
    # strip only that single leading newline and keep the trailing one, so the resulting
    # text matches the original inline triple-quoted templates exactly.
    text = node.text
    if text.startswith("\n"):
        text = text[1:]
    return Template(text)


def get_prompt_description(name: str) -> str | None:
    """Return the human-readable <description> for a prompt, if present."""
    root = _load_root()
    node = root.find(f"./prompt[@name='{name}']/description")
    return node.text.strip() if node is not None and node.text else None
