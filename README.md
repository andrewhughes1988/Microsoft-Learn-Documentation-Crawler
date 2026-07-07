# Microsoft Learn Documentation Crawler

A specialized Python utility for downloading, cleaning, and aggregating Microsoft Learn documentation into a single Markdown file.

The crawler resolves the backing GitHub source for a Microsoft Learn URL, performs an efficient sparse checkout, follows documentation navigation links, and writes the collected Markdown content into one readable output file.

## Features

- **Smart source resolution**  
  Automatically detects the source GitHub repository and source path from Microsoft Learn page metadata when available.

- **Automatic output naming**  
  If `--output` is not provided, the script derives a friendly filename from the documentation source path.

  Examples:

  ```text
  https://learn.microsoft.com/en-us/azure/azure-functions/
  -> azure-functions.md

  https://learn.microsoft.com/en-us/defender-endpoint/microsoft-defender-endpoint
  -> microsoft-defender-endpoint.md

  https://learn.microsoft.com/en-us/azure/app-service/overview
  -> app-service.md

  https://learn.microsoft.com/en-us/azure/virtual-machines/linux/quick-create-cli
  -> virtual-machines.md

  https://learn.microsoft.com/en-us/entra/identity/
  -> identity.md
  ```

- **Efficient cloning**  
  Uses Git sparse checkout and blobless cloning with `--filter=blob:none` so the script downloads only the relevant documentation folder instead of the entire repository.

- **Microsoft Learn landing page support**  
  Supports normal Markdown articles as well as YAML-based Microsoft Learn landing pages such as `index.yml` hub pages.

- **TOC and hub-page parsing**  
  Parses `toc.yml`, `TOC.yml`, `href:`, and `url:` entries to follow related documentation pages in the expected documentation structure.

- **Clean Markdown output**  
  Removes YAML front matter, HTML comments, DocFX image directives, and common non-essential sections such as `Feedback`, `Next Steps`, `Additional Resources`, and `Additional Links`.

- **Scoped crawling**  
  Keeps crawling bounded to the detected documentation folder so unrelated repository content is skipped.

- **Temporary directory cleanup**  
  Deletes the temporary Git checkout directory after completion or failure, including retry handling for read-only Git files.

## Prerequisites

- **Python 3.7+**
- **Git** installed and available in your system `PATH`
- Internet access to Microsoft Learn and GitHub

No third-party Python packages are required. The script uses only Python standard library modules.

## Installation

1. Save the script as:

   ```text
   learn_crawler.py
   ```

2. Confirm Python is available:

   ```bash
   python --version
   ```

3. Confirm Git is available:

   ```bash
   git --version
   ```

## Usage

### Basic usage

Run the crawler with a Microsoft Learn URL:

```bash
python learn_crawler.py "https://learn.microsoft.com/en-us/azure/app-service/overview"
```

If `--output` is omitted, the script automatically derives the output filename from the documentation source path.

For example, the command above writes:

```text
app-service.md
```

### Specify a custom output file

```bash
python learn_crawler.py "https://learn.microsoft.com/en-us/azure/app-service/overview" --output my-docs.md
```

This writes:

```text
my-docs.md
```

### Microsoft Entra example

```bash
python learn_crawler.py "https://learn.microsoft.com/en-us/entra/identity/"
```

This writes:

```text
identity.md
```

The Entra identity URL is a Microsoft Learn landing page, so the script resolves it to the appropriate YAML hub page and queues the Markdown articles linked from that page.

## How It Works

1. **Normalize the URL**  
   Query strings and fragments are removed from the provided Microsoft Learn URL.

2. **Resolve source metadata**  
   The script tries to read Microsoft Learn metadata such as `original_content_git_url` to identify the source GitHub repository, branch, and source file path.

3. **Fallback inference**  
   If direct metadata resolution fails, the script uses known Microsoft Learn path patterns to infer the repository and source path.

4. **Sparse clone the repository**  
   The GitHub repository is cloned using blobless and sparse checkout options to limit downloaded content.

5. **Find related documents**  
   The script looks for TOC files and YAML hub-page links, then queues related Markdown files.

6. **Clean and combine Markdown**  
   Each Markdown file is cleaned and appended into a single output document.

7. **Clean up temporary files**  
   The temporary Git checkout directory is removed after the run completes or fails.

## Output Naming Rules

When `--output` is not provided, the script derives the filename from the GitHub source path.

Preferred rules:

- If the source path contains `articles/<folder>/...`, the output is `<folder>.md`.
- If the source path contains `docs/<folder>/...`, the output is `<folder>.md`.
- If neither pattern is available, the script falls back to the source file stem.
- If no useful source path is available, the fallback filename is `combined.md`.

Examples:

```text
articles/azure-functions/functions-overview.md
-> azure-functions.md

articles/app-service/overview.md
-> app-service.md

articles/virtual-machines/linux/quick-create-cli.md
-> virtual-machines.md

docs/identity/index.yml
-> identity.md
```

## Expectations and Limitations

1. **Document scope is intentional**  
   The crawler is bounded to the detected documentation folder. Links outside that scope are skipped to avoid pulling unrelated content.

2. **Some pages are landing pages**  
   Microsoft Learn hub pages may resolve to YAML files such as `index.yml`. The script parses linked Markdown files from these YAML pages, but the YAML landing page itself is not written as Markdown content.

3. **Checkout can appear slow**  
   Git may take time during checkout, especially for larger documentation folders. This is normal.

4. **Repository layouts vary**  
   Microsoft documentation repositories are not perfectly consistent. The script includes several known mappings, but unusual repositories may require additional fallback rules.

5. **Cleaning is opinionated**  
   The output is optimized for readable consolidated Markdown, not for preserving every DocFX directive or Microsoft Learn-specific control.

6. **External links are skipped**  
   Links to external sites, other repositories, email addresses, and telephone links are ignored.

## Troubleshooting

### The output file is blank

Possible causes:

- The resolved source path does not exist in the public repository.
- The URL points to a landing page whose linked articles could not be resolved.
- Git sparse checkout did not retrieve the expected documentation folder.
- Microsoft Learn metadata was unavailable and the fallback path needs to be updated.

Try running the script again with status output visible and check the lines beginning with `[status]`.

### The filename is not what you expected

Use `--output` to force the filename:

```bash
python learn_crawler.py "https://learn.microsoft.com/en-us/entra/identity/" --output entra-identity.md
```

### Git clone fails

Check that:

- Git is installed.
- Your network allows access to GitHub.
- The resolved repository is public.
- The branch exists.

## Example Commands

```bash
python learn_crawler.py "https://learn.microsoft.com/en-us/azure/azure-functions/"
```

```bash
python learn_crawler.py "https://learn.microsoft.com/en-us/azure/app-service/overview"
```

```bash
python learn_crawler.py "https://learn.microsoft.com/en-us/azure/virtual-machines/linux/quick-create-cli"
```

```bash
python learn_crawler.py "https://learn.microsoft.com/en-us/entra/identity/"
```

```bash
python learn_crawler.py "https://learn.microsoft.com/en-us/defender-endpoint/microsoft-defender-endpoint" --output defender-endpoint.md
```

## License

Use and adapt this utility as needed for your own documentation workflows.
