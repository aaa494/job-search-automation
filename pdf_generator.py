"""
Renders resume HTML → PDF using Playwright's headless Chromium.
No external dependencies beyond playwright itself.
"""

import asyncio
import json
import os
from pathlib import Path

from jinja2 import Environment, FileSystemLoader
from playwright.async_api import async_playwright

from config import PATHS


def _render_html(resume: dict) -> str:
    template_path = Path(PATHS["resume_template"])
    env = Environment(loader=FileSystemLoader(str(template_path.parent)))
    template = env.get_template(template_path.name)
    return template.render(**resume)


async def generate_pdf(resume: dict, output_path: str) -> str:
    """
    Render resume dict to PDF. Returns the output_path on success.
    """
    html_content = _render_html(resume)

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.set_content(html_content, wait_until="networkidle")
        await page.pdf(
            path=output_path,
            format="A4",
            margin={"top": "0", "right": "0", "bottom": "0", "left": "0"},
            print_background=True,
        )
        await browser.close()

    return output_path


def save_cover_letter(text: str, output_path: str) -> str:
    Path(output_path).write_text(text, encoding="utf-8")
    return output_path
