# Executive Travel & Job Search Secretary Agent

Personal AI secretary for travel planning, accommodation research, job search, application support, negotiation guidance, and workflow automation. The current workspace is preloaded with Mayank Gaur's relocation and job-search materials.

---

## Setup (one-time)

```bash
cd /Users/mayankgaur/Documents/Travel-Job-search-Agent

# Install dependencies
pip install -r requirements.txt

# Set API key
cp .env.example .env
# Edit .env and paste your Anthropic API key
```

---

## Run

```bash
python agent.py
```

## Web Dashboard

```bash
python3 -m pip install -r requirements.txt
python3 -m uvicorn app.main:app --reload
```

Then open `http://127.0.0.1:8000/dashboard`.

## WhatsApp Channel

This app can also reply over WhatsApp through a Twilio webhook.

1. Run the app locally:
```bash
python3 -m uvicorn app.main:app --reload
```
2. Expose it publicly, for example with ngrok:
```bash
ngrok http 8000
```
3. In the Twilio WhatsApp Sandbox settings, set:
`When a message comes in` -> `https://<your-ngrok-domain>/whatsapp/webhook`
Method -> `POST`
4. Join the Twilio Sandbox from your WhatsApp account and send messages to the Sandbox number.

Endpoints:
- `/whatsapp/health`
- `/whatsapp/webhook`

Notes:
- `/reset` in WhatsApp clears that sender's chat session
- For testing, Twilio Sandbox is intended for development rather than production use

The dashboard currently provides:
- job lead import from `target_companies_germany_dubai.md`
- live job feed sync from env-configured RSS/JSON feeds
- application tracking in SQLite
- travel request storage and option generation
- Amadeus live flight-search integration when credentials are configured
- Playwright browser-automation hook for semi-automatic applications
- JSON APIs under `/api/...`
- WhatsApp webhook adapter for Twilio Sandbox / WhatsApp Business routing

---

## Slash Commands

| Command | What it does |
|---------|-------------|
| `/help` | Show all commands |
| `/clear` | Reset conversation history |
| `/profile` | Show Mayank's profile and targets |
| `/docs` | List all working documents |
| `/doc <name>` | Print a working document (e.g. `/doc resume_ats`) |
| `/status` | Show what is implemented and what is still missing |
| `/save <label>` | Save last response to a markdown file |
| `/quit` | Exit |

---

## Example Prompts

```
Write a personalised cover letter for Zalando Berlin
Help me answer "Tell me about yourself" for SAP
What should I do this week to advance the Germany search?
Run a mock system design interview question
Draft a recruiter outreach message for Allianz Technology
How do I set up my German Sperrkonto?
What should I charge for a Spring Boot REST API project?
Negotiate my salary — they offered €82,000
Review my answer: [paste your answer]
```

---

## Files

| File | Purpose |
|------|---------|
| `agent.py` | CLI entry point |
| `secretary_agent.py` | Anthropic API + streaming + history |
| `config.py` | Model, paths, document registry |
| `requirements.txt` | Python dependencies |
| `.env` | API key (create from `.env.example`) |

All working documents (resumes, cover letters, templates, prep guides) are `.md` files in the same directory.

---

## Current Capabilities

- General executive-secretary prompt covering travel, accommodation, jobs, negotiation, and automation
- Anthropic streaming CLI chat
- Slash commands for profile, docs, save, and implementation status
- Workspace document lookup for resumes, templates, plans, and checklists
- Optional memory loading from local Claude memory files

## Current Gaps

- Live job feeds require `JOB_FEED_URLS` to be configured
- Live travel search requires Amadeus credentials and airport-style origin/destination codes
- Browser automation requires Playwright package plus browser binaries (`python3 -m playwright install chromium`)
- Form-filling is generic; company-specific applicant portals still need tuning

## Architecture

- **Model:** `claude-opus-4-6` with adaptive thinking
- **Streaming:** `client.messages.stream()` with live terminal output
- **Context:** Full conversation history sent each turn
- **Prompting:** System prompt plus optional local memory appendices
- **Output:** Rich console with markdown rendering
