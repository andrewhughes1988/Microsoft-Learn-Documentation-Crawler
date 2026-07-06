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
