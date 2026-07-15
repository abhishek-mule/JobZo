import re

from ats.base import ATSParser
from ats.greenhouse import GreenhouseParser
from ats.lever import LeverParser
from ats.ashby import AshbyParser
from ats.workday import WorkdayParser
from ats.smartrecruiters import SmartRecruitersParser
from ats.bamboohr import BambooHRParser
from ats.teamtailor import TeamtailorParser
from ats.personio import PersonioParser
from ats.generic import GenericParser

# Ordered by specificity (most specific pattern first)
PARSER_REGISTRY: list[tuple[str, type[ATSParser]]] = [
    (r"boards\.greenhouse\.io", GreenhouseParser),
    (r"job-boards\.eu\.greenhouse\.io", GreenhouseParser),
    (r"jobs\.lever\.co", LeverParser),
    (r"ashbyhq\.com", AshbyParser),
    (r"(?:myworkdayjobs|wd1|wd3|wd5)\.(?:com|myworkdayjobs)", WorkdayParser),
    (r"smartrecruiters\.com", SmartRecruitersParser),
    (r"bamboohr\.(?:com|eu)", BambooHRParser),
    (r"teamtailor\.com", TeamtailorParser),
    (r"personio\.(?:de|com)", PersonioParser),
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
