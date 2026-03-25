# 🤖 Jarvis — Local AI Personal Assistant

**Zero-cost, fully local, modular agentic AI assistant via Telegram.**

## Architecture

```
Telegram Message
      │
      ▼
[1] security.py ──── is_authorized() → DROP if not owner
      │
      ▼
[2] security.py ──── sanitize_input() → clean raw text
      │
      ▼
[3] router.py ─────── Ollama (temp=0.0) → classify intent
      │
      ├─► "OS_COMMAND"   → skills/os_controller.py
      │       ├─ Ollama translates NL → PowerShell
      │       ├─ is_safe_command() blacklist check
      │       └─ subprocess.run(powershell.exe ...)
      │
      └─► "CONVERSATION" → skills/conversational.py
              └─ Ollama (temp=0.7) → natural response
                    │
                    ▼
             Reply to Telegram
                    │
                    ▼ (background thread)
        skills/notion_logger.py → Notion Database
```

## Setup (5 minutes)

### 1. Prerequisites
- Python 3.11+
- [Ollama](https://ollama.ai) installed and running
- A Telegram bot token from [@BotFather](https://t.me/BotFather)
- A Notion integration token + database

### 2. Install dependencies
```powershell
cd C:\Jarvis
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

### 3. Configure .env
```powershell
copy .env.example .env
notepad .env   # Fill in your actual values
```

### 4. Create Notion Database
Create a database in Notion with these **exact** property names:
| Property | Type |
|---|---|
| Input | Title |
| Skill | Select |
| Result | Rich text |
| Timestamp | Date |

Share the database with your integration (... → Connections → Add).

### 5. Pull your Ollama model
```powershell
ollama pull llama3
ollama serve   # Keep this running in a separate terminal
```

### 6. Run Jarvis
```powershell
python main.py
```

## Security Model

| Layer | Mechanism | Blocks |
|---|---|---|
| Auth | `is_authorized()` chat_id whitelist | All unauthorized users |
| Input | `sanitize_input()` control char strip + length limit | Prompt injection |
| Command | `is_safe_command()` regex blacklist | Destructive OS commands |
| Execution | `subprocess` with timeout=30s | Hanging processes |

## Adding New Skills

1. Create `skills/my_skill.py` with an `execute(user_input: str) -> str` function
2. Add `"MY_SKILL"` to `VALID_SKILLS` in `router.py`
3. Update the router system prompt with a description of the skill
4. Add `"MY_SKILL": my_skill.execute` to `SKILL_MAP` in `main.py`
