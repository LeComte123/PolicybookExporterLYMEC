"""Shared helpers for the Policy Book scripts: paths, schema constants and file I/O."""

from __future__ import annotations

import datetime as dt
import re
from pathlib import Path

import frontmatter
import yaml

ROOT = Path(__file__).resolve().parent.parent

STATUSES = ("in_force", "repealed", "superseded", "expired")
SOURCES = ("openslides", "migration")

# Order of the front matter fields when a resolution file is written.
FIELD_ORDER = (
    "id",
    "number",
    "title",
    "congress",
    "adopted",
    "category",
    "status",
    "replaces",
    "superseded_by",
    "amends",
    "submitted_by",
    "co_signers",
    "openslides_number",
    "source",
)
REQUIRED_FIELDS = ("id", "number", "title", "congress", "adopted", "category", "status", "source")
LIST_FIELDS = ("replaces", "amends", "submitted_by", "co_signers")

NUMBER_RE = re.compile(r"^(\d)\.(\d{2,3})$")
CONGRESS_ID_RE = re.compile(r"^\d{4}-[A-Z0-9]{2,8}$")


class Resolution:
    """One resolution file: front matter (meta) + Markdown body."""

    def __init__(self, path: Path, meta: dict, body: str):
        self.path = path
        self.meta = meta
        self.body = body

    @property
    def id(self) -> str:
        return str(self.meta.get("id", ""))

    def save(self) -> None:
        write_resolution(self.path, self.meta, self.body)


def resolutions_dir(root: Path = ROOT) -> Path:
    return root / "resolutions"


def load_yaml_list(path: Path) -> list[dict]:
    """Load a YAML file that holds a list of mappings (categories.yml, congresses.yml)."""
    if not path.exists():
        return []
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return data or []


def load_categories(root: Path = ROOT) -> list[dict]:
    return load_yaml_list(root / "categories.yml")


def load_congresses(root: Path = ROOT) -> list[dict]:
    return load_yaml_list(root / "congresses.yml")


def resolution_paths(root: Path = ROOT) -> list[Path]:
    return sorted(resolutions_dir(root).glob("*.md"))


def load_resolution(path: Path) -> Resolution:
    """Load one resolution file. Raises ValueError if the front matter is not valid YAML."""
    try:
        post = frontmatter.load(path)
    except yaml.YAMLError as exc:
        raise ValueError(f"the header between the '---' lines is not valid YAML ({exc})") from exc
    return Resolution(path, dict(post.metadata), post.content)


def load_resolutions(root: Path = ROOT) -> list[Resolution]:
    """Load all resolutions/*.md, sorted by filename."""
    return [load_resolution(path) for path in resolution_paths(root)]


def dump_front_matter(meta: dict) -> str:
    """Serialise front matter in FIELD_ORDER (unknown fields last), deterministic.

    Lists are written one item per line and nothing is wrapped, so the files are easy to edit
    in the GitHub web editor.
    """
    ordered = {key: meta[key] for key in FIELD_ORDER if key in meta}
    ordered.update({key: value for key, value in meta.items() if key not in ordered})
    return yaml.safe_dump(ordered, sort_keys=False, allow_unicode=True, default_flow_style=False, width=10**6)


def write_resolution(path: Path, meta: dict, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = "---\n" + dump_front_matter(meta) + "---\n\n" + body.strip() + "\n"
    path.write_text(text, encoding="utf-8")


def parse_date(value) -> dt.date | None:
    """Return a date for a YAML date or an ISO string, None if it can't be parsed."""
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    if isinstance(value, str):
        try:
            return dt.date.fromisoformat(value.strip())
        except ValueError:
            return None
    return None


def next_number(chapter: int, categories: list[dict], resolutions: list[Resolution]) -> str:
    """Next free Policy Book number in a chapter: after categories.yml last_number and all existing numbers."""
    highest = 0
    for cat in categories:
        if cat.get("chapter") == chapter:
            highest = int(cat.get("last_number") or 0)
    for res in resolutions:
        match = NUMBER_RE.match(str(res.meta.get("number", "")))
        if match and int(match.group(1)) == chapter:
            highest = max(highest, int(match.group(2)))
    return f"{chapter}.{highest + 1:02d}"
