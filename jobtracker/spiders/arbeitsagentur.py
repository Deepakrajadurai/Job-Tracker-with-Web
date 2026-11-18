"""Bundesagentur für Arbeit: Jobsuche search endpoint (Method C, network/API).

The endpoint is the one the agency's own job search website uses. It is documented by the
community (github.com/bundesAPI/jobsuche-api), not by the agency as a supported developer API,
so it can change without notice. robots.txt is therefore obeyed for it.
"""
from __future__ import annotations

from urllib.parse import urlencode

from ..presets import REGIONS
from .base import SourceSpider

BASE = "https://rest.arbeitsagentur.de/jobboerse/jobsuche-service/pc/{version}/jobs"
DETAIL = "https://www.arbeitsagentur.de/jobsuche/jobdetail/{refnr}"
API_KEY = "jobboerse-jobsuche"       # public client id of the agency's own web search
GERMANY = {"germany", "deutschland", "de"}
OTHER_COUNTRIES = {name for name in REGIONS if name != "germany"} | {"österreich", "schweiz", "poland", "polen",
                                                                      "france", "frankreich", "europe", "eu"}
# angebotsart: 1 = regular work (includes Werkstudent), 34 = internship, trainee, thesis
LEVEL_OFFER_TYPES = {"working_student": [1], "internship": [34], "thesis": [34, 1], "entry": [1], "experienced": [1]}


def offer_types(cfg: dict, scfg: dict) -> list[int]:
    if scfg.get("offer_types"):
        return [int(v) for v in scfg["offer_types"]]
    types: list[int] = []
    for level in cfg["profile"].get("levels") or []:
        for value in LEVEL_OFFER_TYPES.get(level, [1]):
            if value not in types:
                types.append(value)
    return types or [1, 34]


def search_places(locations: list[str]) -> list[str]:
    """'' means all of Germany. Cities are searched by name. Other countries are left out."""
    places: list[str] = []
    for location in locations or ["Germany"]:
        low = location.strip().lower()
        place = "" if low in GERMANY else (None if low in OTHER_COUNTRIES else location.strip())
        if place is not None and place not in places:
            places.append(place)
    return places


def parse_arbeitsagentur(data: dict) -> list[dict]:
    jobs = []
    for item in data.get("stellenangebote") or []:
        refnr = item.get("refnr") or item.get("referenznummer")
        title = item.get("titel") or item.get("stellenangebotsTitel") or item.get("beruf")
        if not refnr or not title:
            continue
        place = item.get("arbeitsort") or {}
        jobs.append({
            "link": DETAIL.format(refnr=refnr),
            "position": title,
            "date": item.get("aktuelleVeroeffentlichungsdatum") or item.get("modifikationsTimestamp") or "",
            "company": item.get("arbeitgeber") or "",
            "location": ", ".join(v for v in (place.get("ort"), place.get("region")) if v),
        })
    return jobs


class ArbeitsagenturSpider(SourceSpider):
    name = "arbeitsagentur"

    def url(self, query: str, place: str, offer_type: int, page: int) -> str:
        params = {"was": query, "angebotsart": offer_type, "page": page,
                  "size": int(self.scfg.get("page_size", 50)),
                  "veroeffentlichtseit": int(self.scfg.get("max_age_days", 7))}
        if place:
            params.update(wo=place, umkreis=int(self.scfg.get("radius_km", 25)))
        return f"{BASE.format(version=self.scfg.get('api_version', 'v4'))}?{urlencode(params)}"

    def request(self, query, place, offer_type, page):
        return self.req(self.url(query, place, offer_type, page), self.parse,
                        headers={"X-API-Key": API_KEY, "Accept": "application/json"},
                        cb_kwargs={"query": query, "place": place, "offer_type": offer_type, "page": page})

    async def requests(self):
        places = search_places(self.cfg["search"]["locations"])
        if not places:
            self.log.warning("no German location in this search, Bundesagentur skipped",
                             extra={"source": self.name})
        for query in self.cfg["search"]["queries"]:
            for place in places:
                for offer_type in offer_types(self.cfg, self.scfg):
                    yield self.request(query, place, offer_type, 1)

    def parse(self, response, query, place, offer_type, page):
        data = response.json()
        jobs = parse_arbeitsagentur(data)
        for job in jobs:
            yield self.posting(job["link"], job["position"], job["date"], job["company"], job["location"],
                               apply_filter=bool(self.scfg.get("title_filter", False)))
        size = int(self.scfg.get("page_size", 50))
        total = int(data.get("maxErgebnisse") or 0)
        if len(jobs) >= size and page * size < total and page < int(self.scfg.get("max_pages", 2)):
            yield self.request(query, place, offer_type, page + 1)
