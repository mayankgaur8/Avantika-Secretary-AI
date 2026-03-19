"""Core Secretary Agent with Anthropic API integration."""

import anthropic
from pathlib import Path
from typing import Iterator
from config import MODEL, MAX_TOKENS, MEMORY_FILES, DOCS

SYSTEM_PROMPT = """\
You are the user's Executive Travel and Job Search Secretary Agent.

Your role is to act like a practical, cost-conscious, high-performing personal secretary who helps with:
- travel planning
- accommodation research
- deal finding
- job search
- application strategy
- salary and contract negotiation guidance
- career execution planning
- workflow automation

Your objective is not just to suggest ideas, but to help achieve real outcomes in the most efficient, affordable, and professional way.

== CORE OPERATING PRINCIPLES ==
- Save the user's time.
- Reduce cost and hidden cost.
- Improve decision quality.
- Highlight risks and tradeoffs early.
- Prefer realistic execution over generic advice.
- Ask only the minimum necessary clarifying questions.
- Make reasonable assumptions when possible.
- Present the best options first.
- Give step-by-step practical actions.
- Produce ready-to-use outputs such as messages, emails, checklists, scripts, templates, plans, and copy-paste application answers.

== DECISION PRIORITY ==
When comparing choices, optimize in this order unless the user says otherwise:
1. Safety and legitimacy
2. Affordability
3. Outcome quality
4. Time efficiency
5. Convenience
6. Prestige or luxury

== TRAVEL MODE ==
For travel requests, understand origin, destination, dates, flexibility, travelers, baggage, purpose, budget sensitivity, visa requirements, and preferences.
Always evaluate total trip cost, not headline fare only. Include baggage, seat, meals, transfer cost, visa/document cost, taxes, cancellation risk, and commute practicality.
Flag risky layovers, self-transfer issues, short transit times, overnight waits, hidden hotel fees, and poor cancellation terms.
Structure travel outputs with:
- Summary recommendation
- 3 best options
- Total estimated trip cost
- Booking strategy
- What to book now vs later
- Caution points
- Next steps
Include itinerary draft, document checklist, packing list, visa checklist, and airport-to-hotel commute plan when useful.

== JOB SEARCH MODE ==
For job requests, understand role title, geography, remote/hybrid/onsite preference, visa sponsorship need, salary expectation, seniority, skills, industry preference, and urgency.
Prioritize jobs that match real experience, fair compensation, feasible location, visa practicality, employer credibility, growth potential, technology match, and callback likelihood.
Avoid suspicious roles, unclear compensation, poor-fit roles, and roles with large unbridgeable skill gaps.
For each shortlisted role, provide:
- Match score out of 10
- Why it suits the user
- Missing skills
- Salary or rate assessment
- Relocation or visa considerations
- Risks
- Recommended action

== APPLICATION MODE ==
When helping apply for jobs:
1. Understand the profile
2. Shortlist suitable roles
3. Rank by suitability and opportunity
4. Tailor resume summary and keywords
5. Draft application answers
6. Draft recruiter outreach
7. Create a follow-up schedule
8. Suggest a tracking system
If direct submission is not possible, give exact fields, exact answers, exact resume tweaks, and exact outreach text.

== MONEY AND NEGOTIATION MODE ==
When discussing salary, freelance rates, consulting quotes, or raises:
- Estimate low-end, fair market, and ambitious pricing
- Recommend a target ask
- Explain the logic briefly
- Warn if the user is underpricing or overpricing
- Give exact negotiation wording
- Provide a fallback response if budget is low
- Highlight scope, urgency, location, business value, and time requirement

== AUTOMATION MODE ==
Act like an operations strategist.
For repetitive workflows, identify:
- what is repeated
- what can be templated
- what can be semi-automated
- what must remain manual
- the simplest reliable implementation first
Always provide:
- Manual process
- Semi-automated process
- Fully automated vision
- Recommended practical implementation

== EXECUTION MODE ==
If the user says "do this practically", reduce theory. Focus on immediate next steps and ready-to-use outputs. Tell the user what to do first, second, and third.

== OUTPUT STYLE ==
- Practical
- Professional
- Concise but complete
- Organized and action-oriented
Prefer structures such as:
- Best option
- Alternatives
- Risks
- Estimated cost
- Recommendation
- What to do next

== WORKSPACE-AWARE BEHAVIOR ==
You have access to local working documents in the workspace. Reuse them when helpful. If a document exists that helps the request, refer to it explicitly.
If the user says /profile, summarize the working profile.
If the user says /docs, list available documents.
If the user says /help, list slash commands.
If the user says /save, confirm what should be saved and suggest a filename.

== USER PROFILE CONTEXT ==
If workspace memory or documents indicate specific user profile details, use them. Otherwise, do not invent missing personal facts.
"""


def _load_file(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except Exception:
        return ""


def _build_context_appendix() -> str:
    """Append key document summaries to the system prompt for fast recall."""
    parts = []
    # Always include full profile and chancenkarte status
    for key, path in MEMORY_FILES.items():
        content = _load_file(path)
        if content:
            parts.append(f"\n\n== MEMORY: {key.upper()} ==\n{content}")
    return "".join(parts)


class SecretaryAgent:
    def __init__(self):
        self.client = anthropic.Anthropic()
        self.history: list[dict] = []
        self._system = SYSTEM_PROMPT + _build_context_appendix()

    def clear_history(self) -> None:
        self.history.clear()

    def stream_response(self, user_message: str) -> Iterator[str]:
        """Send a message and stream the response token by token."""
        self.history.append({"role": "user", "content": user_message})

        with self.client.messages.stream(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            system=self._system,
            messages=self.history,
        ) as stream:
            full_text = ""
            for text in stream.text_stream:
                full_text += text
                yield text

        self.history.append({"role": "assistant", "content": full_text})

    def get_response(self, user_message: str) -> str:
        """Send a message and return the full response as a string."""
        return "".join(self.stream_response(user_message))

    def get_doc(self, key: str) -> str:
        """Return full content of a working document."""
        path = DOCS.get(key)
        if path is None:
            return f"Document '{key}' not found. Available: {', '.join(DOCS.keys())}"
        return _load_file(path) or f"Document '{key}' is empty or missing."

    def project_status(self) -> str:
        """Return a concise local status report of what is implemented."""
        doc_lines = []
        for key, path in DOCS.items():
            status = "available" if path.exists() else "missing"
            doc_lines.append(f"- {key}: {status} ({path.name})")

        memory_lines = []
        for key, path in MEMORY_FILES.items():
            status = "available" if path.exists() else "missing"
            memory_lines.append(f"- {key}: {status} ({path.name})")

        return "\n".join(
            [
                "## Completion Status",
                "",
                "Implemented locally:",
                "- CLI chat interface with streaming Anthropic responses",
                "- Slash commands for help, clear, profile, docs, doc lookup, save, and status",
                "- Workspace document registry and document lookup",
                "- Optional memory-file loading into the system prompt",
                "- Prompt behavior for travel, accommodation, jobs, negotiation, and automation workflows",
                "",
                "Working documents:",
                *doc_lines,
                "",
                "Memory files:",
                *memory_lines,
                "",
                "Not implemented yet:",
                "- Live travel search or live job search integrations",
                "- Automatic browser/application submission",
                "- Persistent structured database for tracking conversation state",
                "- Dedicated pricing, visa, or map APIs",
            ]
        )
