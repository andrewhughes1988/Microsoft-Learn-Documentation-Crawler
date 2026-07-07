import argparse
import os
import re
import stat
import time
import shutil
import subprocess
import tempfile
import urllib.parse
import urllib.request
from pathlib import Path
from typing import List, Optional, Set, Tuple


def emit_status(message: str) -> None:
    print(f"[status] {message}")


class LearnCrawler:
    def __init__(self, output_path: str):
        self.output_path = Path(output_path)
        self.repo_root: Optional[Path] = None
        self.visited: Set[str] = set()
        self.results: List[Tuple[str, str]] = []
        self.start_path: Optional[str] = None

    def crawl(self, start_url: str, repo_info: Optional[dict] = None) -> str:
        start_url = normalize_url(start_url)
        emit_status(f"Normalizing URL: {start_url}")

        if repo_info is None:
            repo_info = resolve_repo_from_learn_url(start_url)

        if not repo_info:
            raise RuntimeError("Could not resolve a GitHub repository for the provided Learn URL.")

        emit_status(f"Resolved repository: {repo_info['repo']}@{repo_info['branch']}")

        source_path = repo_info.get("source_path")
        if not source_path:
            raise RuntimeError("Could not determine the source file path for the provided Learn URL.")

        self.repo_root = clone_repo(repo_info, source_path)
        self.start_path = source_path

        try:
            emit_status(f"Cloned repository to {self.repo_root}")
            emit_status(f"Starting from source file: {source_path}")

            queue: List[str] = []

            if source_path.lower().endswith((".md", ".markdown")):
                queue.append(source_path)
            elif source_path.lower().endswith((".yml", ".yaml")):
                emit_status(f"Source path is a YAML landing page: {source_path}. Queuing linked articles...")
                source_entries = parse_toc_entries(self.repo_root / source_path, self.repo_root)
                queue.extend(source_entries)

            toc_path_rel = find_toc_path(self.repo_root, source_path)
            if toc_path_rel:
                emit_status(f"Found TOC at {toc_path_rel}. Queuing related articles...")
                toc_entries = parse_toc_entries(self.repo_root / toc_path_rel, self.repo_root)
                for entry in toc_entries:
                    if entry not in queue:
                        queue.append(entry)

            emit_status("Beginning markdown document scan...")

            while queue:
                rel_path = queue.pop(0)

                if not rel_path.endswith(".md"):
                    continue

                if rel_path in self.visited:
                    continue

                self.visited.add(rel_path)
                doc_path = self.repo_root / rel_path

                if not doc_path.exists():
                    emit_status(f"Skipping missing file: {rel_path}")
                    continue

                emit_status(f"Processing {rel_path}")

                markdown = doc_path.read_text(encoding="utf-8", errors="ignore")
                cleaned = clean_markdown(markdown)

                if not cleaned.strip():
                    continue

                self.results.append((rel_path, cleaned))

                for target in extract_repo_links(markdown):
                    candidate = resolve_repo_path(self.repo_root, rel_path, target)

                    if not candidate or not candidate.endswith(".md"):
                        continue

                    if candidate == rel_path:
                        continue

                    crawl_root = get_docset_root(self.start_path) if self.start_path else None
                    if crawl_root and not candidate.startswith(crawl_root + "/"):
                        continue

                    if candidate not in self.visited and candidate not in queue:
                        queue.append(candidate)

            emit_status(f"Collected {len(self.results)} document(s); writing output")

            combined = build_combined_markdown(self.results)
            self.output_path.write_text(combined, encoding="utf-8")

            emit_status(f"Wrote output to {self.output_path}")
            return str(self.output_path)

        finally:
            if self.repo_root and self.repo_root.exists():
                emit_status("Cleaning up temporary repository directory...")
                remove_tree(self.repo_root)


def remove_tree(path: Path) -> None:
    """
    Remove a directory tree reliably, including Git checkout folders that may
    contain read-only files on some platforms.
    """
    if not path.exists():
        return

    def make_writable_and_retry(func, item_path, exc_info):
        try:
            os.chmod(item_path, stat.S_IWRITE | stat.S_IREAD | stat.S_IEXEC)
            func(item_path)
        except Exception:
            pass

    # Retry a few times because Git can briefly keep file handles open.
    for attempt in range(3):
        try:
            shutil.rmtree(path, onerror=make_writable_and_retry)
            break
        except Exception as exc:
            if attempt == 2:
                emit_status(f"Warning: unable to delete temporary directory {path}: {exc}")
                return
            time.sleep(0.5)

    if path.exists():
        emit_status(f"Warning: temporary directory still exists after cleanup: {path}")



def normalize_url(url: str) -> str:
    url = url.strip()

    if not url:
        raise ValueError("Empty URL")

    parsed = urllib.parse.urlparse(url)

    if parsed.scheme not in {"http", "https"}:
        raise ValueError(f"Unsupported URL: {url}")

    return parsed._replace(query="", fragment="").geturl()


def fetch_url(url: str) -> str:
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "Mozilla/5.0"},
    )

    with urllib.request.urlopen(req, timeout=30) as response:
        return response.read().decode("utf-8", "ignore")


def extract_meta(html: str, name: str) -> Optional[str]:
    patterns = [
        rf'<meta\s+name=["\']{re.escape(name)}["\']\s+content=["\']([^"\']+)["\']',
        rf'<meta\s+content=["\']([^"\']+)["\']\s+name=["\']{re.escape(name)}["\']',
    ]

    for pattern in patterns:
        match = re.search(pattern, html, flags=re.I)
        if match:
            return match.group(1)

    return None


def resolve_repo_from_learn_url(start_url: str) -> Optional[dict]:
    emit_status(f"Fetching metadata for {start_url}...")

    try:
        html = fetch_url(start_url)
        git_url = extract_meta(html, "original_content_git_url")

        if git_url:
            match = re.search(
                r"github\.com/([^/]+)/([^/]+)/blob/([^/]+)/(.*)",
                git_url,
            )

            if match:
                org, repo, branch, source_path = match.groups()

                if repo.endswith("-pr"):
                    emit_status(f"Detected private staging repo ({repo}). Redirecting to public repo.")
                    repo = repo[:-3]
                    branch = "main"

                return {
                    "repo": f"{org}/{repo}",
                    "branch": branch,
                    "source_path": source_path,
                    "git_url": git_url,
                }

    except Exception as exc:
        emit_status(f"Warning: Failed to fetch metadata directly. Falling back to inferred paths. ({exc})")

    path = urllib.parse.urlparse(start_url).path.strip("/")

    if path.startswith("en-us/"):
        path = path[len("en-us/"):]

    repo_hint = None

    if path.startswith("azure/architecture/"):
        repo_hint = "MicrosoftDocs/architecture-docs"
    elif path.startswith("azure/azure-monitor/"):
        repo_hint = "MicrosoftDocs/azure-monitor-docs"
    elif path.startswith("azure-ai/"):
        repo_hint = "MicrosoftDocs/azure-ai-docs"
    elif path.startswith("azure/"):
        repo_hint = "MicrosoftDocs/azure-docs"
    elif path.startswith("defender-endpoint/"):
        repo_hint = "MicrosoftDocs/defender-docs"
    elif path.startswith("entra/"):
        repo_hint = "MicrosoftDocs/entra-docs"
    elif path.startswith("windows-server/"):
        repo_hint = "MicrosoftDocs/windowsserverdocs"
    elif path.startswith("windows/"):
        repo_hint = "MicrosoftDocs/windows-docs"

    if repo_hint:
        source_path = infer_source_path(start_url)
        return {
            "repo": repo_hint,
            "branch": "main",
            "source_path": source_path,
        }

    return None


def derive_output_filename(repo_info: dict) -> str:
    return derive_output_filename_from_source_path(repo_info.get("source_path"))


def derive_output_filename_from_source_path(source_path: Optional[str]) -> str:
    if not source_path:
        return "combined.md"

    normalized = source_path.strip("/")
    parts = Path(normalized).parts

    for root_name in ("articles", "docs"):
        if root_name in parts:
            root_index = parts.index(root_name)
            if len(parts) > root_index + 1:
                folder_name = parts[root_index + 1]
                if folder_name:
                    return f"{folder_name}.md"

    if len(parts) > 1:
        return f"{parts[0]}.md"

    stem = Path(normalized).stem
    if stem and stem.lower() not in {"index", "toc"}:
        return f"{stem}.md"

    return "combined.md"


def get_docset_root(source_path: str) -> Optional[str]:
    normalized = source_path.strip("/")
    parts = Path(normalized).parts

    if not parts:
        return None

    for root_name in ("articles", "docs"):
        if root_name in parts:
            root_index = parts.index(root_name)
            if len(parts) > root_index + 1:
                return "/".join(parts[: root_index + 2])

    if len(parts) > 1:
        return parts[0]

    return None


def clone_repo(repo_info: dict, source_path: Optional[str] = None) -> Path:
    repo_slug = repo_info["repo"].split("/")[-1]
    target_dir = Path(tempfile.mkdtemp(prefix=f"learn-{repo_slug}-", dir=str(Path.cwd())))
    repo_url = f"https://github.com/{repo_info['repo']}.git"

    try:
        emit_status(f"Cloning {repo_url} (branch {repo_info['branch']})")

        cmd = [
            "git",
            "clone",
            "--depth",
            "1",
            "--filter=blob:none",
            "--no-checkout",
            "--branch",
            repo_info["branch"],
            "--progress",
            repo_url,
            str(target_dir),
        ]

        completed = subprocess.run(cmd)

        if completed.returncode != 0:
            emit_status("Primary clone failed; trying default branch")
            remove_tree(target_dir)
            target_dir = Path(tempfile.mkdtemp(prefix=f"learn-{repo_slug}-", dir=str(Path.cwd())))

            fallback = [
                "git",
                "clone",
                "--depth",
                "1",
                "--filter=blob:none",
                "--no-checkout",
                "--progress",
                repo_url,
                str(target_dir),
            ]
            completed = subprocess.run(fallback)

            if completed.returncode != 0:
                raise RuntimeError(f"Failed to clone repository {repo_url}. Check your internet connection.")

        if source_path:
            sparse_paths = build_sparse_checkout_paths(source_path)
            if sparse_paths:
                sparse_cmd = [
                    "git",
                    "-C",
                    str(target_dir),
                    "sparse-checkout",
                    "set",
                    *sparse_paths,
                ]
                sparse_completed = subprocess.run(sparse_cmd)
                if sparse_completed.returncode != 0:
                    emit_status("Warning: sparse-checkout setup failed. Continuing with checkout attempt.")

        checkout_cmd = [
            "git",
            "-C",
            str(target_dir),
            "checkout",
            "--progress",
        ]
        checkout_completed = subprocess.run(checkout_cmd)

        if checkout_completed.returncode != 0:
            raise RuntimeError("Git checkout failed.")

        return target_dir

    except Exception:
        remove_tree(target_dir)
        raise


def build_sparse_checkout_paths(source_path: Optional[str]) -> List[str]:
    if not source_path:
        return []

    normalized = source_path.strip("/")
    if not normalized:
        return []

    docset_root = get_docset_root(normalized)
    target_dir = docset_root if docset_root else Path(normalized).parent.as_posix()

    paths = [
        target_dir,
        "includes",
        "articles/includes",
        "docs/includes",
    ]

    return dedupe_preserve_order(paths)


def infer_source_path(start_url: str) -> Optional[str]:
    path = urllib.parse.urlparse(start_url).path.strip("/")

    if not path:
        return None

    if path.startswith("en-us/"):
        path = path[len("en-us/"):]

    if path.endswith("/"):
        path = path.rstrip("/")

    normalized = path if path.endswith(".md") else f"{path}.md"
    candidates = []

    if normalized.startswith("azure/app-service/"):
        candidates.append("articles/app-service/" + normalized[len("azure/app-service/"):])

    if normalized.startswith("azure/virtual-machines/"):
        candidates.append("articles/virtual-machines/" + normalized[len("azure/virtual-machines/"):])

    if normalized.startswith("azure/azure-functions/"):
        candidates.append("articles/azure-functions/" + normalized[len("azure/azure-functions/"):])

    if normalized.startswith("defender-endpoint/"):
        candidates.append("articles/microsoft-defender-endpoint/" + normalized[len("defender-endpoint/"):])

    # Microsoft Entra docs use docs/, not articles/. Landing-page URLs often
    # resolve to index.yml hub files rather than Markdown articles.
    if normalized == "entra/identity.md":
        candidates.append("docs/identity/index.yml")
    elif normalized.startswith("entra/identity/"):
        rest = normalized[len("entra/identity/"):].removesuffix(".md")
        candidates.append(f"docs/identity/{rest}.md")
        candidates.append(f"docs/identity/{rest}/index.md")
        candidates.append(f"docs/identity/{rest}/index.yml")
    elif normalized.startswith("entra/"):
        rest = normalized[len("entra/"):].removesuffix(".md")
        candidates.append(f"docs/{rest}.md")
        candidates.append(f"docs/{rest}/index.md")
        candidates.append(f"docs/{rest}/index.yml")

    if not normalized.startswith(("articles/", "docs/")):
        candidates.append(f"articles/{normalized}")

    candidates.append(normalized)

    for candidate in dedupe_preserve_order(candidates):
        if candidate and candidate.lower().endswith((".md", ".markdown", ".yml", ".yaml")):
            return candidate

    return None


def find_toc_path(repo_root: Path, source_path: str) -> Optional[str]:
    source_dir = Path(source_path).parent.as_posix() if "/" in source_path else ""
    candidates = []

    def add_toc_candidates(directory: str) -> None:
        if directory:
            candidates.append(f"{directory}/toc.yml")
            candidates.append(f"{directory}/TOC.yml")
        else:
            candidates.append("toc.yml")
            candidates.append("TOC.yml")

    if source_dir:
        current = source_dir
        while current:
            add_toc_candidates(current)
            current = current.rsplit("/", 1)[0] if "/" in current else ""

    add_toc_candidates("")

    for candidate in dedupe_preserve_order(candidates):
        if (repo_root / candidate).exists():
            return candidate

    return None


def parse_toc_entries(toc_path: Path, repo_root: Optional[Path] = None) -> List[str]:
    entries: List[str] = []

    if not toc_path.exists():
        return entries

    toc_dir = toc_path.parent
    text = toc_path.read_text(encoding="utf-8", errors="ignore")

    # DocFX YAML files use both `href:` and `url:`. Landing pages such as
    # docs/identity/index.yml mostly use `url:`, while TOC files usually use
    # `href:`. Scan the whole file instead of only line-by-line so compact YAML
    # also works.
    link_pattern = re.compile(
        r"(?:^|\s)(?:href|url):\s*['\"]?([^'\"\s#]+)",
        flags=re.I | re.M,
    )

    for match in link_pattern.finditer(text):
        value = match.group(1).strip()

        if not value or value.startswith("#"):
            continue

        lowered = value.lower()

        if lowered.startswith(("http://", "https://", "mailto:", "tel:")):
            continue

        if lowered.endswith((".yml", ".yaml", ".json", ".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp")):
            continue

        normalized = resolve_path_from_base(str(toc_dir), value, repo_root)
        if normalized:
            entries.append(normalized)

    return dedupe_preserve_order(entries)


def dedupe_preserve_order(items: List[str]) -> List[str]:
    seen = set()
    result = []

    for item in items:
        if item not in seen:
            seen.add(item)
            result.append(item)

    return result


def extract_repo_links(markdown: str) -> List[str]:
    links = []

    for match in re.finditer(r"\[[^\]]+\]\(([^)]+)\)", markdown):
        target = match.group(1).strip()

        if not target or target.startswith("#"):
            continue

        if target.startswith(("http://", "https://", "mailto:", "tel:")):
            continue

        links.append(target)

    return dedupe_preserve_order(links)


def resolve_repo_path(repo_root: Path, source_path: str, target: str) -> Optional[str]:
    target = target.split("#", 1)[0].split("?", 1)[0]

    if not target:
        return None

    source_obj = Path(source_path)
    if not source_obj.is_absolute():
        source_obj = repo_root / source_obj

    candidate = source_obj.parent / target

    try:
        rel = candidate.resolve().relative_to(repo_root.resolve())
    except ValueError:
        return None

    if rel.suffix.lower() in {".md", ".markdown"}:
        return rel.as_posix()

    return None


def resolve_path_from_base(base_dir: str, target: str, repo_root: Optional[Path] = None) -> Optional[str]:
    target = target.split("#", 1)[0].split("?", 1)[0].strip()

    if not target:
        return None

    lowered = target.lower()
    if lowered.startswith(("http://", "https://", "mailto:", "tel:")):
        return None

    if lowered.endswith((".yml", ".yaml", ".json", ".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp")):
        return None

    base = Path(base_dir)

    if target.startswith("/"):
        target_path = target.lstrip("/")
        if repo_root is not None:
            # Learn absolute URLs like /entra/identity/users/... map to repo paths
            # under docs/, for example docs/identity/users/....
            if target_path.startswith("entra/"):
                candidate = repo_root / "docs" / target_path[len("entra/"):]
            elif target_path.startswith(("docs/", "articles/")):
                candidate = repo_root / target_path
            else:
                candidate = repo_root / target_path
        else:
            candidate = Path(target_path)
    else:
        candidate = base / target

    candidates = [candidate]

    if candidate.suffix.lower() not in {".md", ".markdown"}:
        candidates.append(candidate.with_suffix(".md"))
        candidates.append(candidate / "index.md")

    for item in candidates:
        candidate_resolved = item.resolve()

        if repo_root is not None:
            try:
                rel = candidate_resolved.relative_to(repo_root.resolve())
            except ValueError:
                continue

            if rel.suffix.lower() in {".md", ".markdown"} and (repo_root / rel).exists():
                return rel.as_posix()
        else:
            if candidate_resolved.suffix.lower() in {".md", ".markdown"}:
                return candidate_resolved.as_posix()

    return None


def remove_noise(text: str) -> str:
    lines = [line.rstrip() for line in text.splitlines()]
    filtered = []
    skip_section = False

    for line in lines:
        stripped = line.strip()

        if not stripped:
            if skip_section:
                continue
            filtered.append("")
            continue

        lower = stripped.lower()

        if lower in {"next steps", "feedback", "additional resources", "additional links"}:
            skip_section = True
            continue

        if skip_section and re.match(r"^#{1,6}\s", stripped):
            skip_section = False

        if skip_section:
            continue

        if stripped.startswith(":::image") or stripped.startswith(":::"):
            continue

        filtered.append(stripped)

    cleaned = "\n\n".join(filtered)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)

    return cleaned.strip()


def normalize_markdown(text: str) -> str:
    text = re.sub(r"^---\s*\n.*?\n---\s*", "", text, flags=re.S)
    text = re.sub(r"<!--.*?-->\s*", "", text, flags=re.S)
    return text.strip()


def clean_markdown(markdown: str) -> str:
    text = remove_noise(markdown)
    text = normalize_markdown(text)
    return text


def build_combined_markdown(items: List[Tuple[str, str]]) -> str:
    sections = []

    for path, text in items:
        title = Path(path).stem.replace("-", " ").replace("_", " ").title()
        sections.append(f"# {title}\n\n{text}\n")

    return "\n\n".join(sections)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Download and combine Microsoft Learn documentation"
    )

    parser.add_argument(
        "url",
        help="A Microsoft Learn article URL",
    )

    parser.add_argument(
        "--output",
        help="Destination Markdown file. If omitted, filename is derived from GitHub metadata.",
    )

    args = parser.parse_args()

    try:
        normalized_url = normalize_url(args.url)
        repo_info = resolve_repo_from_learn_url(normalized_url)

        if not repo_info:
            raise RuntimeError("Could not resolve GitHub metadata from the provided Learn URL.")

        output_file = args.output or derive_output_filename(repo_info)
        emit_status(f"Using output filename: {output_file}")

        crawler = LearnCrawler(output_path=output_file)
        path = crawler.crawl(normalized_url, repo_info=repo_info)

        print(f"\nCompleted! File saved to: {path}")

    except KeyboardInterrupt:
        print("\n[status] Script cancelled by user. Exiting...")
        raise SystemExit(1)

    except Exception as exc:
        print(f"\nUnable to resolve documentation source: {exc}")
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
