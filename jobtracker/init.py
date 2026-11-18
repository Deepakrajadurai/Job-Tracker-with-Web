"""Create a profile: python -m jobtracker.init

Interactive when run without arguments. Every question can also be answered with a flag:

    python -m jobtracker.init --name anna --field mechanical --level working_student thesis \\
        --location Germany

    python -m jobtracker.init --name sam --field custom --keywords "Architektur, BIM, Bauingenieur"

    python -m jobtracker.init --name kim --describe "biotech lab work, cell culture, QA in pharma"
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml

from .ai import AIClient
from .config import DEFAULTS, load_config, load_env_file
from .presets import FIELDS, LEVELS, build_profile

HEADER = """\
# Job tracker profile for "{name}" ({field}).
# Created by `python -m jobtracker.init`. Edit any list freely.
# Keys that are missing here use the defaults from jobtracker/config.py
# (see config.example.yaml for every available key).
#
# Run it with:  python -m jobtracker.run --profile {name}

"""


def _split(text: str) -> list[str]:
    return [part.strip() for part in (text or "").replace(";", ",").split(",") if part.strip()]


def _ask(prompt: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    answer = input(f"{prompt}{suffix}: ").strip()
    return answer or default


def _choose(title: str, options: dict[str, str], default: str, multiple: bool = False) -> list[str]:
    keys = list(options)
    print(f"\n{title}")
    for number, key in enumerate(keys, start=1):
        print(f"  {number}. {options[key]}")
    hint = "numbers separated by commas" if multiple else "number"
    while True:
        raw = _ask(f"Choose ({hint})", str(keys.index(default) + 1))
        try:
            picked = [keys[int(part) - 1] for part in _split(raw)]
        except (ValueError, IndexError):
            print("  Please enter valid numbers from the list.")
            continue
        if picked and (multiple or len(picked) == 1):
            return picked


def suggest_from_description(description: str, ai: AIClient | None = None) -> dict:
    """Ask the AI model to turn a plain description into search terms and title keywords."""
    ai = ai or AIClient(DEFAULTS["ai"], str(Path("data") / "ai_cache.json"))
    return ai.suggest_profile(description)


def interactive(args) -> dict:
    print("Job tracker profile setup. Press Enter to accept the value in brackets.")
    name = args.name or _ask("Profile name (one per person or search)", "me")
    fields = {key: value["label"] for key, value in FIELDS.items()}
    fields["custom"] = "Something else (you describe it)"
    field = args.field or _choose("Which field are you looking in?", fields, "software_ai")[0]
    keywords, search_terms, label = _split(args.keywords), _split(args.search_terms), None
    if field == "custom" and not keywords:
        description = args.describe or _ask(
            "Describe the jobs you want in a few words (or leave empty to type keywords yourself)")
        if description:
            try:
                suggestion = suggest_from_description(description)
                keywords, search_terms, label = suggestion["keywords"], suggestion["search_terms"], description
                print(f"  AI suggested keywords: {', '.join(keywords)}")
            except Exception as exc:  # noqa: BLE001 - fall back to manual input
                print(f"  AI suggestion not available ({exc}).")
        if not keywords:
            keywords = _split(_ask("Title keywords, comma separated (e.g. Architektur, BIM, Bauingenieur)"))
            label = label or ", ".join(keywords[:3])
    levels = args.level or _choose("Which kind of position?", {k: v["label"] for k, v in LEVELS.items()},
                                   "working_student", multiple=True)
    locations = args.location or _split(_ask("\nCountries or cities, comma separated", "Germany"))
    return build_profile(name, field, levels, locations, keywords, search_terms, label)


def from_flags(args) -> dict:
    keywords, search_terms, label = _split(args.keywords), _split(args.search_terms), None
    field = args.field or ("custom" if (keywords or args.describe) else "software_ai")
    if args.describe and not keywords:
        suggestion = suggest_from_description(args.describe)
        keywords, search_terms, label = suggestion["keywords"], suggestion["search_terms"], args.describe
        field = "custom"
    return build_profile(args.name, field, args.level or ["experienced"],
                         args.location or ["Germany"], keywords, search_terms, label)


def write_profile(profile: dict, directory: str = "profiles", force: bool = False) -> Path:
    path = Path(directory) / f"{profile['profile']['name']}.yaml"
    if path.exists() and not force:
        raise FileExistsError(f"{path} already exists, use --force to overwrite it")
    path.parent.mkdir(parents=True, exist_ok=True)
    body = yaml.safe_dump(profile, sort_keys=False, allow_unicode=True, width=100)
    path.write_text(HEADER.format(name=profile["profile"]["name"], field=profile["profile"]["field"]) + body,
                    encoding="utf-8")
    load_config(path)  # fail now, not at 04:00, if the file is unusable
    return path


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Create a job tracker profile.")
    parser.add_argument("--name", help="profile name, one per person or search")
    parser.add_argument("--field", choices=[*FIELDS, "custom"], help="field preset")
    parser.add_argument("--level", nargs="+", choices=list(LEVELS), help="one or more position levels")
    parser.add_argument("--location", nargs="+", help="countries or cities")
    parser.add_argument("--keywords", help="comma separated title keywords (added to the preset)")
    parser.add_argument("--search-terms", help="comma separated search phrases for LinkedIn, Xing, boards")
    parser.add_argument("--describe", help="plain description, the AI model turns it into keywords")
    parser.add_argument("--dir", default="profiles", help="folder for profile files")
    parser.add_argument("--force", action="store_true", help="overwrite an existing profile")
    args = parser.parse_args(argv)
    load_env_file()
    try:
        profile = from_flags(args) if args.name and (args.field or args.keywords or args.describe) \
            else interactive(args)
        path = write_profile(profile, args.dir, args.force)
    except (ValueError, FileExistsError, RuntimeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    print(f"\nWrote {path}")
    print(f"  searches: {', '.join(profile['search']['queries'])}")
    print(f"  run it:   python -m jobtracker.run --profile {profile['profile']['name']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
