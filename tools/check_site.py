"""Check generated pages and assets without a browser or extra dependencies."""

from __future__ import annotations

import json
import posixpath
import tomllib
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]


class PageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.ids: set[str] = set()
        self.links: set[tuple[str, str]] = set()
        self.article = False
        self.headings: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs = dict(attrs)
        if attrs.get("id"):
            self.ids.add(attrs["id"])
        if tag == "article":
            self.article = True
        if tag == "a" and self.article and attrs.get("href") and not attrs["href"].startswith("#__codelineno-"):
            self.links.add(("link", attrs["href"]))
        if tag in {"img", "script"} and attrs.get("src"):
            self.links.add(("asset", attrs["src"]))
        if tag == "link" and attrs.get("href") and attrs.get("rel") == "stylesheet":
            self.links.add(("asset", attrs["href"]))

    def handle_endtag(self, tag: str) -> None:
        if tag == "article":
            self.article = False


def audit(site: Path, base_path: str = "") -> dict:
    base_path = "/" + base_path.strip("/") if base_path.strip("/") else ""
    files = {file.relative_to(site).as_posix() for file in site.rglob("*") if file.is_file()}
    pages = {}
    for name in sorted(files):
        if name.endswith(".html"):
            page = PageParser()
            page.feed((site / name).read_text(encoding="utf-8"))
            pages[name] = page
    missing = {}
    anchors = {}
    external_assets = set()
    checked = 0
    for name, page in pages.items():
        for kind, href in page.links:
            parsed = urlsplit(href)
            if parsed.scheme or parsed.netloc:
                if kind == "asset" and parsed.scheme in {"http", "https"}:
                    external_assets.add(href)
                continue
            decoded = unquote(parsed.path)
            if decoded.startswith("/"):
                if base_path and (decoded == base_path or decoded.startswith(base_path + "/")):
                    decoded = decoded[len(base_path):]
                target = posixpath.normpath(decoded.lstrip("/"))
            elif decoded:
                target = posixpath.normpath(posixpath.join(posixpath.dirname(name), decoded))
            else:
                target = name
            if target in {".", ""}:
                target = "index.html"
            if target not in files and target.rstrip("/") + "/index.html" in files:
                target = target.rstrip("/") + "/index.html"
            checked += 1
            if target not in files:
                key = (kind, target)
                missing.setdefault(key, {"kind": kind, "target": target, "examples": []})
                if len(missing[key]["examples"]) < 3:
                    missing[key]["examples"].append({"page": name, "href": href})
            elif parsed.fragment and target in pages:
                anchor = unquote(parsed.fragment)
                if anchor not in pages[target].ids:
                    key = (target, anchor)
                    anchors.setdefault(key, {"target": target, "anchor": anchor, "page": name})
    return {"html_pages": len(pages), "checked_references": checked, "missing_files": list(missing.values()), "missing_anchors": list(anchors.values()), "external_assets": sorted(external_assets)}


def main() -> None:
    site = ROOT / "site"
    if not (site / "index.html").is_file():
        raise SystemExit("Build the website before running the audit.")
    config = tomllib.loads((ROOT / "zensical.toml").read_text(encoding="utf-8"))
    base_path = unquote(urlsplit(config["project"].get("site_url", "")).path)
    try:
        result = audit(site, base_path)
    except FileNotFoundError:
        raise SystemExit("The preview is rebuilding the website. Run the check again after the rebuild finishes.") from None
    baseline = json.loads((ROOT / "known-source-issues.json").read_text(encoding="utf-8"))
    known = {(issue["kind"], issue["target"]) for issue in baseline}
    result["known_source_issues"] = [issue for issue in result["missing_files"] if (issue["kind"], issue["target"]) in known]
    result["unexpected_missing_files"] = [issue for issue in result["missing_files"] if (issue["kind"], issue["target"]) not in known]
    output = ROOT / "test-results"
    output.mkdir(exist_ok=True)
    (output / "link-audit.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"HTML pages: {result['html_pages']}; checked references: {result['checked_references']}")
    print(f"Missing files: {len(result['missing_files'])}; missing anchors: {len(result['missing_anchors'])}; external assets: {len(result['external_assets'])}")
    print(f"Known source issues: {len(result['known_source_issues'])}; newly introduced missing files: {len(result['unexpected_missing_files'])}")
    for issue in result["missing_files"][:20]:
        print(json.dumps(issue, ensure_ascii=True))
    if result["unexpected_missing_files"] or result["missing_anchors"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
