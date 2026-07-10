import re

from ats.base import ATSParser
from ats.greenhouse import GreenhouseParser
from ats.generic import GenericParser

PARSER_REGISTRY: list[tuple[str, type[ATSParser]]] = [
    (r"boards\.greenhouse\.io", GreenhouseParser),
]

_parser_cache: dict[str, ATSParser] = {}


def detect(url: str) -> ATSParser:
    if url in _parser_cache:
        return _parser_cache[url]
    for pattern, parser_cls in PARSER_REGISTRY:
        if re.search(pattern, url):
            parser = parser_cls()
            _parser_cache[url] = parser
            return parser
    parser = GenericParser()
    _parser_cache[url] = parser
    return parser
