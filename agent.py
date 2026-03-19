#!/usr/bin/env python3
"""
Executive Travel & Job Search Secretary Agent
Mayank Gaur | Senior Java Lead | Germany / Dubai Relocation
"""

import os
import sys
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.text import Text
from rich.rule import Rule

load_dotenv()

# Verify API key before importing agent
if not os.getenv("ANTHROPIC_API_KEY"):
    print("Error: ANTHROPIC_API_KEY not set. Copy .env.example to .env and add your key.")
    sys.exit(1)

from secretary_agent import SecretaryAgent
from config import WORKSPACE, DOCS

console = Console()

BANNER = """\
[bold cyan]Executive Travel & Job Search Secretary[/bold cyan]
[dim]Mayank Gaur | Senior Java Lead | 17+ Years[/dim]
[dim]Target: Germany €90–100k | Dubai AED 22–28k/mo[/dim]
"""

HELP_TEXT = """\
## Slash Commands

| Command | Action |
|---------|--------|
| `/help` | Show this help |
| `/clear` | Clear conversation history |
| `/profile` | Show Mayank's profile summary |
| `/docs` | List all working documents |
| `/doc <name>` | Show a working document (e.g. `/doc resume_ats`) |
| `/status` | Analyze what is implemented locally |
| `/save <note>` | Save current response to a file |
| `/quit` or `/exit` | Exit the agent |

## Quick-start prompts
- "Write a cover letter for Zalando"
- "Help me answer: Tell me about yourself"
- "What should I do this week to advance my Germany job search?"
- "Give me a mock system design interview question"
- "Draft a message to a recruiter at SAP"
- "How do I set up my Sperrkonto?"
- "What freelance rate should I charge for a 3-service microservices POC?"
"""

DOCS_LIST = "\n".join(f"  **{k}** — {v.name}" for k, v in DOCS.items())


def save_output(text: str, label: str = "") -> Path:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    slug = label.strip().replace(" ", "_")[:40] if label else "output"
    filename = WORKSPACE / f"saved_{slug}_{ts}.md"
    filename.write_text(text, encoding="utf-8")
    return filename


def handle_slash(cmd: str, agent: SecretaryAgent) -> bool:
    """Handle slash commands. Returns True if handled, False to pass to LLM."""
    parts = cmd.strip().split(maxsplit=1)
    verb = parts[0].lower()
    arg = parts[1] if len(parts) > 1 else ""

    if verb in ("/quit", "/exit"):
        console.print("\n[dim]Goodbye. Good luck with the Germany move![/dim]")
        sys.exit(0)

    if verb == "/help":
        console.print(Markdown(HELP_TEXT))
        return True

    if verb == "/clear":
        agent.clear_history()
        console.print("[green]✓ Conversation history cleared.[/green]")
        return True

    if verb == "/profile":
        console.print(Panel(
            Text.from_markup(
                "[bold]Mayank Gaur[/bold] | Senior Java Lead | 17+ Years\n"
                "[cyan]Location:[/cyan] Bengaluru, India → Germany / Dubai\n"
                "[cyan]Visa:[/cyan] German Chancenkarte applied\n"
                "[cyan]Target Salary:[/cyan] Germany €90–100k | Dubai AED 22–28k/mo\n"
                "[cyan]Stack:[/cyan] Java 17, Spring Boot, Microservices, Kafka, AWS, Docker/K8s, React\n"
                "[cyan]Email:[/cyan] mayankgaur.8@gmail.com | [cyan]Phone:[/cyan] +91 9620439138\n"
                "[cyan]LinkedIn:[/cyan] linkedin.com/in/mayank-gaur8/"
            ),
            title="[bold cyan]Profile[/bold cyan]",
            border_style="cyan",
        ))
        return True

    if verb == "/docs":
        console.print(Panel(
            DOCS_LIST,
            title="[bold cyan]Working Documents[/bold cyan]",
            border_style="cyan",
        ))
        return True

    if verb == "/status":
        console.print(Markdown(agent.project_status()))
        return True

    if verb == "/doc":
        if not arg:
            console.print("[yellow]Usage: /doc <name>  e.g. /doc resume_ats[/yellow]")
            return True
        content = agent.get_doc(arg)
        console.print(Markdown(content))
        return True

    # /save — let the LLM respond first, then save the last assistant message
    if verb == "/save":
        if agent.history:
            last = next(
                (m["content"] for m in reversed(agent.history) if m["role"] == "assistant"),
                None,
            )
            if last:
                path = save_output(last, arg)
                console.print(f"[green]✓ Saved to:[/green] {path}")
            else:
                console.print("[yellow]Nothing to save yet.[/yellow]")
        else:
            console.print("[yellow]No conversation history yet.[/yellow]")
        return True

    return False  # Not a recognised slash command — pass to LLM


def stream_and_print(agent: SecretaryAgent, user_input: str) -> None:
    """Stream LLM response with live rendering."""
    console.print()
    console.print("[bold cyan]Secretary[/bold cyan]", end=" ")

    buffer = ""
    try:
        for chunk in agent.stream_response(user_input):
            buffer += chunk
            console.print(chunk, end="", highlight=False)
        console.print()  # newline after stream ends
    except KeyboardInterrupt:
        console.print("\n[yellow](interrupted)[/yellow]")


def main() -> None:
    console.print(Panel(BANNER, border_style="cyan", padding=(0, 2)))
    console.print("[dim]Type your request, or /help for commands. Ctrl+C or /quit to exit.[/dim]\n")

    agent = SecretaryAgent()

    while True:
        try:
            console.print(Rule(style="dim"))
            user_input = console.input("[bold green]You >[/bold green] ").strip()
        except (KeyboardInterrupt, EOFError):
            console.print("\n[dim]Exiting.[/dim]")
            break

        if not user_input:
            continue

        if user_input.startswith("/"):
            handled = handle_slash(user_input, agent)
            if handled:
                continue
            # Unrecognised slash — pass to LLM as-is

        stream_and_print(agent, user_input)


if __name__ == "__main__":
    main()
