import argparse
import re
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
    def __init__(self, output_path: Optional[str] = None):
        self.output_path = Path(output_path or "combined.md")
        self.repo_root: Optional[Path] = None
        self.visited: Set[str] = set()
        self.results: List[Tuple[str, str]] = []
        self.start_path: Optional[str] = None

    def crawl(self, start_url: str) -> str:
        start_url = normalize_url(start_url)
        emit_status(f"Normalizing URL: {start_url}")
        
        repo_info = resolve_repo_from_learn_url(start_url)
        if not repo_info:
            raise RuntimeError("Could not resolve a GitHub repository for the provided Learn URL.")

        emit_status(f"Resolved repository: {repo_info['repo']}@{repo_info['branch']}")
        source_path = repo_info.get("source_path")
        if not source_path:
            raise RuntimeError("Could not determine the source file path for the provided Learn URL.")

        self.repo_root = clone_repo(repo_info, source_path)
        self.start_path = source_path
        emit_status(f"Cloned repository to {self.repo_root}")
        emit_status(f"Starting from source file: {source_path}")
        
        queue = [source_path]

        # Look for Table of Contents and add it to the queue
        toc_path_rel = find_toc_path(self.repo_root, source_path)
        if toc_path_rel:
            emit_status(f"Found TOC at {toc_path_rel}. Queuing related articles...")
            toc_entries = parse_toc_entries(self.repo_root / toc_path_rel, self.repo_root)
            for entry in toc_entries:
                if entry not in queue:
                    queue.append(entry)

        emit_status(f"Beginning markdown document scan...")
        
        # Use a safe while loop for processing the queue
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

            # Grab explicit links in the markdown as a fallback
            for target in extract_repo_links(markdown):
                candidate = resolve_repo_path(self.repo_root, rel_path, target)
                if not candidate or not candidate.endswith(".md"):
                    continue
                if not candidate.startswith("articles/"):
                    continue
                if candidate == rel_path:
                    continue
                if self.start_path and not candidate.startswith(Path(self.start_path).parent.as_posix() + "/"):
                    continue
                if candidate not in self.visited and candidate not in queue:
                    queue.append(candidate)

        emit_status(f"Collected {len(self.results)} document(s); writing output")
        combined = build_combined_markdown(self.results)
        self.output_path.write_text(combined, encoding="utf-8")
        emit_status(f"Wrote output to {self.output_path}")

        # Clean up the massive temporary git repository
        if self.repo_root and self.repo_root.exists():
            emit_status(f"Cleaning up temporary repository files...")
            shutil.rmtree(self.repo_root, ignore_errors=True)

        return str(self.output_path)


def normalize_url(url: str) -> str:
    url = url.strip()
    if not url:
        raise ValueError("Empty URL")
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError(f"Unsupported URL: {url}")
    return parsed._replace(query="", fragment="").geturl()


def fetch_url(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as response:
        return response.read().decode("utf-8", "ignore")


def extract_meta(html: str, name: str) -> Optional[str]:
    match = re.search(rf'<meta\s+name="{re.escape(name)}"\s+content="([^"]+)"', html, flags=re.I)
    if match:
        return match.group(1)
    return None


def resolve_repo_from_learn_url(start_url: str) -> Optional[dict]:
    emit_status(f"Fetching metadata for {start_url}...")
    try:
        html = fetch_url(start_url)
        git_url = extract_meta(html, "original_content_git_url")
        if git_url:
            match = re.search(r"github\.com/([^/]+)/([^/]+)/blob/([^/]+)/(.*)", git_url)
            if match:
                org, repo, branch, source_path = match.groups()
                
                # Handle Microsoft's private "-pr" staging repositories
                if repo.endswith("-pr"):
                    emit_status(f"Detected private staging repo ({repo}). Redirecting to public repo.")
                    repo = repo[:-3]
                    branch = "main"
                
                return {
                    "repo": f"{org}/{repo}",
                    "branch": branch,
                    "source_path": source_path
                }
    except Exception as e:
        emit_status(f"Warning: Failed to fetch metadata directly. Falling back to hardcoded paths. ({e})")

    path = urllib.parse.urlparse(start_url).path.strip("/")
    if path.startswith("en-us/"):
        path = path[len("en-us/"):]

    repo_hint = None
    if path.startswith("azure/"):
        repo_hint = "MicrosoftDocs/azure-docs"
    elif path.startswith("azure-ai/"):
        repo_hint = "MicrosoftDocs/azure-ai-docs"
    elif path.startswith("azure/architecture/"):
        repo_hint = "MicrosoftDocs/architecture-docs"
    elif path.startswith("azure/azure-monitor/"):
        repo_hint = "MicrosoftDocs/azure-monitor-docs"
    elif path.startswith("entra/"):
        repo_hint = "MicrosoftDocs/entra-docs"
    elif path.startswith("windows-server/"):
        repo_hint = "MicrosoftDocs/windowsserverdocs"
    elif path.startswith("windows/"):
        repo_hint = "MicrosoftDocs/windows-docs"

    if repo_hint:
        return {"repo": repo_hint, "branch": "main", "source_path": infer_source_path(start_url)}

    return None


def clone_repo(repo_info: dict, source_path: Optional[str] = None) -> Path:
    repo_slug = repo_info["repo"].split("/")[-1]
    target_dir = Path(tempfile.mkdtemp(prefix=f"learn-{repo_slug}-", dir=str(Path.cwd())))
    repo_url = f"https://github.com/{repo_info['repo']}.git"
    emit_status(f"Cloning {repo_url} (branch {repo_info['branch']})")

    # FIX: Added --no-checkout to strictly prevent Git from downloading all files upfront
    cmd = ["git", "clone", "--depth", "1", "--filter=blob:none", "--no-checkout", "--branch", repo_info["branch"], "--progress", repo_url, str(target_dir)]
    completed = subprocess.run(cmd)
    
    if completed.returncode != 0:
        emit_status("Primary clone failed; trying default branch")
        fallback = ["git", "clone", "--depth", "1", "--filter=blob:none", "--no-checkout", "--progress", repo_url, str(target_dir)]
        completed = subprocess.run(fallback)
        if completed.returncode != 0:
            raise RuntimeError(f"Failed to clone repository {repo_url}. Check your internet connection.")

    if source_path:
        sparse_paths = build_sparse_checkout_paths(source_path)
        if sparse_paths:
            # Set the sparse checkout paths BEFORE triggering the checkout
            sparse_cmd = ["git", "-C", str(target_dir), "sparse-checkout", "set", *sparse_paths]
            subprocess.run(sparse_cmd)

    # NOW trigger the checkout with the --progress flag so it doesn't look frozen
    checkout_cmd = ["git", "-C", str(target_dir), "checkout", "--progress"]
    subprocess.run(checkout_cmd)

    return target_dir


def build_sparse_checkout_paths(source_path: Optional[str]) -> List[str]:
    if not source_path:
        return []

    normalized = source_path.strip("/")
    if not normalized:
        return []

    # FIX: Only target the specific article's folder and the global includes folder.
    # Previously, this was accidentally targeting the entire "articles" root directory!
    target_dir = Path(normalized).parent.as_posix()
    
    paths = [target_dir, "includes"]

    return dedupe_preserve_order(paths)


def infer_source_path(start_url: str) -> Optional[str]:
    path = urllib.parse.urlparse(start_url).path.strip("/")
    if not path:
        return None
    if path.startswith("en-us/"):
        path = path[len("en-us/"):]
    if path.endswith("/"):
        path = path.rstrip("/")

    if path.endswith(".md"):
        normalized = path
    else:
        normalized = f"{path}.md"

    candidates = []
    if normalized.startswith("azure/app-service/"):
        candidates.append("articles/app-service/" + normalized[len("azure/app-service/"):])
    if not normalized.startswith("articles/"):
        candidates.append(f"articles/{normalized}")
    candidates.append(normalized)

    for candidate in dedupe_preserve_order(candidates):
        if candidate and candidate.endswith(".md"):
            return candidate
    return None


def find_toc_path(repo_root: Path, source_path: str) -> Optional[str]:
    source_dir = Path(source_path).parent.as_posix() if "/" in source_path else ""
    candidates = []
    if source_dir:
        current = source_dir
        while current:
            candidates.append(f"{current}/toc.yml")
            current = current.rsplit("/", 1)[0] if "/" in current else ""
    candidates.extend(["toc.yml", f"{source_dir}/toc.yml" if source_dir else "toc.yml"])
    
    for candidate in dedupe_preserve_order(candidates):
        if (repo_root / candidate).exists():
            return candidate
    return None


def parse_toc_entries(toc_path: Path, repo_root: Optional[Path] = None) -> List[str]:
    entries: List[str] = []
    if not toc_path.exists():
        return entries

    toc_dir = toc_path.parent
    for line in toc_path.read_text(encoding="utf-8", errors="ignore").splitlines():
        match = re.search(r"href:\s*['\"]?([^'\"\s#]+)", line)
        if not match:
            continue
        value = match.group(1).strip()
        if not value or value.startswith("#"):
            continue
        lowered = value.lower()
        if lowered.endswith((".yml", ".yaml", ".json", ".png", ".jpg", ".jpeg", ".gif")):
            continue
        if lowered.endswith((".md", ".markdown")) or value.startswith(("http://", "https://", "./", "../", "/")):
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

    base_dir = source_obj.parent
    candidate = (base_dir / target)
    try:
        rel = candidate.resolve().relative_to(repo_root.resolve())
    except ValueError:
        return None
    if rel.suffix.lower() in {".md", ".markdown"}:
        return rel.as_posix()
    return None


def resolve_path_from_base(base_dir: str, target: str, repo_root: Optional[Path] = None) -> Optional[str]:
    target = target.split("#", 1)[0].split("?", 1)[0]
    if not target:
        return None

    candidate = (Path(base_dir) / target).resolve()
    if repo_root is not None:
        try:
            rel = candidate.relative_to(repo_root.resolve())
        except ValueError:
            return None
        if rel.suffix.lower() in {".md", ".markdown"}:
            return rel.as_posix()
        return None

    if candidate.suffix.lower() in {".md", ".markdown"}:
        return candidate.as_posix()
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
    # Fixed regex to preserve all markdown structure while removing YAML and HTML comments
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
    parser = argparse.ArgumentParser(description="Download and combine Microsoft Learn documentation")
    parser.add_argument("url", help="A Microsoft Learn article URL")
    parser.add_argument("--output", default="combined.md", help="Destination Markdown file")
    args = parser.parse_args()

    try:
        crawler = LearnCrawler(output_path=args.output)
        path = crawler.crawl(args.url)
        print(f"\nCompleted! File saved to: {path}")
    except KeyboardInterrupt:
        print("\n[status] Script cancelled by user. Exiting...")
        raise SystemExit(1)
    except Exception as exc:
        print(f"\nUnable to resolve documentation source: {exc}")
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
