"""
Claude Computer Use — macOS.

When the form filler gets stuck, this module:
  1. Opens the ATS URL in the default system browser (visible to user).
  2. Takes real Mac screenshots via `screencapture`.
  3. Sends them to Claude with the computer-use beta tool.
  4. Executes every action Claude requests (click, type, scroll, key, drag).
  5. Repeats until Claude says "done" or max_turns is exhausted.

The user can watch every mouse move and keystroke happen live on their screen.

Requirements: pyautogui, Pillow  (pip install pyautogui pillow)
macOS permission: System Settings → Privacy & Security → Accessibility → grant Terminal/iTerm
                                                        → Screen Recording → grant Terminal/iTerm
"""

import asyncio
import base64
import json
import logging
import os
import subprocess
import tempfile
import time
import webbrowser
from pathlib import Path

log = logging.getLogger("jobsearch")

# Candidate answers used in the prompt so Claude knows what to type
_CANDIDATE = {
    "full_name":            "Aidarbek Abdyk",
    "first_name":           "Aidarbek",
    "last_name":            "Abdyk",
    "email":                "aidarbek.a@yahoo.com",
    "phone":                "773-757-2279",
    "city":                 "Chicago",
    "location":             "Chicago, IL",
    "country":              "United States",
    "linkedin":             "",
    "github":               "",
    "years_experience":     "7",
    "authorized_to_work":   "Yes",
    "requires_sponsorship": "No",
    "salary_expectation":   "130000",
    "availability":         "2 weeks",
    "visa_status":          "Green Card",
    "gender":               "Prefer not to say",
    "ethnicity":            "Prefer not to say",
    "veteran":              "No",
    "disability":           "No",
}


def _screenshot_b64() -> str:
    """Take a real Mac screenshot and return as base64 PNG."""
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
        tmp = f.name
    try:
        # -x = no sound, -t png = format
        subprocess.run(["screencapture", "-x", "-t", "png", tmp],
                       check=True, capture_output=True)
        data = Path(tmp).read_bytes()
        return base64.standard_b64encode(data).decode()
    finally:
        try:
            Path(tmp).unlink()
        except Exception:
            pass


def _execute_action(action: dict, resume_pdf_path: str) -> None:
    """Execute a single computer-use action using pyautogui."""
    import pyautogui
    pyautogui.FAILSAFE = False   # don't abort on corner-move
    pyautogui.PAUSE = 0.05

    atype = action.get("type", "")

    if atype == "screenshot":
        return  # handled by caller

    elif atype in ("left_click", "click"):
        c = action.get("coordinate", [0, 0])
        pyautogui.click(c[0], c[1])
        time.sleep(0.3)

    elif atype == "right_click":
        c = action.get("coordinate", [0, 0])
        pyautogui.rightClick(c[0], c[1])
        time.sleep(0.3)

    elif atype == "double_click":
        c = action.get("coordinate", [0, 0])
        pyautogui.doubleClick(c[0], c[1])
        time.sleep(0.3)

    elif atype == "mouse_move":
        c = action.get("coordinate", [0, 0])
        pyautogui.moveTo(c[0], c[1], duration=0.3)

    elif atype == "left_click_drag":
        start = action.get("start_coordinate", [0, 0])
        end   = action.get("coordinate", [0, 0])
        pyautogui.moveTo(start[0], start[1], duration=0.2)
        pyautogui.dragTo(end[0], end[1], duration=0.4, button="left")
        time.sleep(0.2)

    elif atype == "type":
        text = action.get("text", "")
        # Use clipboard paste for speed and unicode safety
        import pyperclip  # installed with pyautogui
        try:
            pyperclip.copy(text)
            import platform
            if platform.system() == "Darwin":
                pyautogui.hotkey("command", "v")
            else:
                pyautogui.hotkey("ctrl", "v")
        except Exception:
            pyautogui.typewrite(text, interval=0.04)
        time.sleep(0.2)

    elif atype == "key":
        key = action.get("text", "")
        # Claude returns key names like "Return", "Tab", "ctrl+a"
        # Map to pyautogui key names
        key_map = {
            "Return": "enter", "Enter": "enter",
            "Tab": "tab", "Escape": "esc", "Esc": "esc",
            "BackSpace": "backspace", "Delete": "delete",
            "space": "space", "Space": "space",
            "Page_Down": "pagedown", "Page_Up": "pageup",
            "End": "end", "Home": "home",
            "ctrl+a": ["ctrl", "a"], "ctrl+c": ["ctrl", "c"],
            "ctrl+v": ["ctrl", "v"], "ctrl+z": ["ctrl", "z"],
            "cmd+a": ["command", "a"],
        }
        mapped = key_map.get(key, key.lower())
        if isinstance(mapped, list):
            pyautogui.hotkey(*mapped)
        else:
            pyautogui.press(mapped)
        time.sleep(0.2)

    elif atype == "scroll":
        c = action.get("coordinate", [0, 0])
        direction = action.get("direction", "down")
        amount = action.get("amount", 3)
        pyautogui.moveTo(c[0], c[1], duration=0.2)
        if direction == "down":
            pyautogui.scroll(-amount)
        else:
            pyautogui.scroll(amount)
        time.sleep(0.2)

    elif atype == "cursor_position":
        pass  # informational only

    log.debug("[CU-mac] Executed action: %s", atype)


async def run_computer_use(
    url: str,
    resume_pdf_path: str,
    cover_letter: str,
    candidate_info: dict | None = None,
    max_turns: int = 50,
    open_browser: bool = True,
) -> bool:
    """
    Open `url` in the system browser and let Claude Computer Use fill the form.
    Returns True if Claude reports the application was submitted.

    This runs entirely on the real Mac screen — the user can watch every action.
    """
    import anthropic as _anthropic

    client = _anthropic.Anthropic()   # sync client — we'll call from thread

    info = {**_CANDIDATE, **(candidate_info or {})}
    info["cover_letter_excerpt"] = cover_letter[:600] if cover_letter else ""

    candidate_summary = json.dumps(
        {k: v for k, v in info.items()
         if k in ("full_name", "first_name", "last_name", "email", "phone",
                  "city", "location", "linkedin", "github", "years_experience",
                  "authorized_to_work", "requires_sponsorship", "salary_expectation",
                  "availability", "visa_status", "cover_letter_excerpt")},
        indent=2,
    )

    resume_path_str = str(Path(resume_pdf_path).absolute()) if resume_pdf_path else ""

    system_prompt = f"""You are filling out a job application form on behalf of Aidarbek.
The browser is already open on the correct page.

Candidate information (use these exact values):
{candidate_summary}

Resume PDF path (for file upload dialogs): {resume_path_str}

Instructions:
- Fill every REQUIRED field (marked * or clearly required).
- Skip optional fields unless they are cover letter or LinkedIn.
- For file upload buttons: click the button, wait for the system file dialog, then type the resume path and press Enter.
- For login/registration pages: fill the form with the candidate's email and create or enter the password.
- When you see a confirmation / thank-you page: call the computer tool with action=screenshot one final time, then stop.
- Work methodically: scroll down after filling visible fields to check for more.
- Do not navigate away from the application form unless following a required link.
"""

    display_w, display_h = 1496, 967
    try:
        import pyautogui as _pag
        display_w, display_h = _pag.size()
    except Exception:
        pass

    tools = [{
        "type": "computer_20250124",
        "name": "computer",
        "display_width_px":  display_w,
        "display_height_px": display_h,
        "display_number": 1,
    }]

    if open_browser:
        log.info("[CU-mac] Opening URL in system browser: %s", url)
        webbrowser.open(url)
        await asyncio.sleep(3)   # give browser time to load

    messages = []

    # Seed with an initial screenshot
    initial_b64 = await asyncio.to_thread(_screenshot_b64)
    messages.append({
        "role": "user",
        "content": [
            {
                "type": "image",
                "source": {"type": "base64", "media_type": "image/png", "data": initial_b64},
            },
            {
                "type": "text",
                "text": (
                    f"Please fill out the job application form at this URL: {url}\n"
                    "Use the computer tool to take screenshots and perform actions.\n"
                    "When the application is fully submitted, reply with DONE."
                ),
            },
        ],
    })

    log.info("[CU-mac] Starting Claude Computer Use loop (max %d turns)", max_turns)
    _notify_telegram_start(url)

    for turn in range(max_turns):
        try:
            response = await asyncio.to_thread(
                _call_claude, client, system_prompt, messages, tools
            )
        except Exception as e:
            log.error("[CU-mac] Claude API error at turn %d: %s", turn + 1, e)
            break

        # Collect assistant message content
        assistant_content = []
        done = False
        has_tool_use = False

        for block in response.content:
            assistant_content.append(block)

            if block.type == "text":
                text = block.text.strip()
                log.info("[CU-mac] Claude says: %s", text[:200])
                if "DONE" in text.upper() or "submitted" in text.lower() or "thank you" in text.lower():
                    done = True

            elif block.type == "tool_use" and block.name == "computer":
                has_tool_use = True
                action = block.input
                log.info("[CU-mac] Action: %s", json.dumps(action)[:120])

                # Execute action on real screen
                if action.get("type") != "screenshot":
                    await asyncio.to_thread(_execute_action, action, resume_pdf_path)
                    await asyncio.sleep(0.5)

        if done:
            log.info("[CU-mac] Application submitted (turn %d).", turn + 1)
            _notify_telegram_done(url, success=True)
            return True

        if response.stop_reason == "end_turn" and not has_tool_use:
            # Claude finished without any tool calls — check if it said done
            log.info("[CU-mac] Claude stopped without tool use at turn %d.", turn + 1)
            break

        # Add assistant message to history
        messages.append({"role": "assistant", "content": assistant_content})

        # Take a fresh screenshot and give it back to Claude
        await asyncio.sleep(1.0)
        new_b64 = await asyncio.to_thread(_screenshot_b64)
        messages.append({
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": next(
                        (b.id for b in assistant_content if getattr(b, "type", "") == "tool_use"),
                        "screenshot",
                    ),
                    "content": [
                        {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": new_b64}},
                    ],
                }
            ],
        })

    log.warning("[CU-mac] Exhausted %d turns without completing.", max_turns)
    _notify_telegram_done(url, success=False)
    return False


def _call_claude(client, system_prompt: str, messages: list, tools: list):
    """Synchronous Claude API call for computer use (run via asyncio.to_thread)."""
    return client.beta.messages.create(
        model="claude-opus-4-5",   # computer use requires claude-3-5-sonnet+ or claude-opus-4-5+
        max_tokens=4096,
        system=system_prompt,
        tools=tools,
        messages=messages,
        betas=["computer-use-2025-01-24"],
    )


def _notify_telegram_start(url: str) -> None:
    import urllib.request
    token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "")
    if not token or not chat_id:
        return
    text = (
        "🖥️ <b>Claude Computer Use started</b>\n"
        "Filling out job application on your screen.\n"
        f"<code>{url[:100]}</code>"
    )
    _tg_send(token, chat_id, text)


def _notify_telegram_done(url: str, success: bool) -> None:
    import urllib.request
    token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "")
    if not token or not chat_id:
        return
    if success:
        text = "✅ <b>Computer Use: Application submitted!</b>"
    else:
        text = (
            "⚠️ <b>Computer Use could not complete the application.</b>\n"
            "Please finish it manually:\n"
            f"<code>{url[:100]}</code>"
        )
    _tg_send(token, chat_id, text)


def _tg_send(token: str, chat_id: str, text: str) -> None:
    import urllib.request
    payload = json.dumps({"chat_id": chat_id, "text": text, "parse_mode": "HTML"}).encode()
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    try:
        urllib.request.urlopen(req, timeout=10)
    except Exception:
        pass
