# Microsoft Learn Documentation Crawler

A specialized Python utility designed to download and aggregate Microsoft Learn documentation into a single, clean Markdown file.

## Features

* **Smart Resolution:** Automatically detects the source GitHub repository from a Microsoft Learn URL.

* **Efficient Cloning:** Uses Git sparse-checkout and blobless filters (`--filter=blob:none`) to download only the specific documentation files you need, avoiding multi-gigabyte repository downloads.

* **Intelligent Aggregation:** Parses `toc.yml` files to follow the official documentation reading order.

* **Clean Output:** Strips out "noise" (like "Feedback" sections and metadata) and normalizes Markdown formatting for a readable, unified document.

## Prerequisites

* **Python 3.7+** installed.

* **Git** installed and available in your system's PATH.

## Usage

1. **Install Requirements:** (No external dependencies are required; it uses standard Python libraries).

2. **Run the Script:**

   ```bash
   python learn_crawler.py "[https://learn.microsoft.com/en-us/azure/](https://learn.microsoft.com/en-us/azure/)..." --output my-docs.md

## Expectations & Limitations
1. Document Scope: The crawler is strictly bounded to the folder structure of your provided URL.
2. Performance: Uses "lazy fetching" via Git; may appear to hang during the final checkout phase. Be patient.
3. Cleaning Logic: Automatically removes metadata (YAML headers) and non-essential sections like "Feedback" and "Next Steps."
4. Known Issues: Links pointing outside the current repository's scope are automatically skipped.
