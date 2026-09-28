# AI personalization

Everyone is getting the same email. The point of this feature is to change one
sentence per recipient — and only that sentence.

## The guarantee

**The model fills labelled gaps and can never touch anything else.** That is
structural, not a matter of prompting:

- The backend receives only the slot's instruction and that contact's variables.
  It never sees the rest of your email.
- Output is stripped of markup, capped to your word limit, and checked against
  your banned phrases.
- Anything that fails becomes your fallback text. A flaky API cannot block a
  send or corrupt a message.
- **Review is the default.** Nothing generated is sent until you have seen it,
  and your edits are used verbatim.

## Syntax

```
Hi {{ first_name }},

{% ai "opener" max_words=25 fallback="Hope your week is going well." %}
One warm sentence about {{ company }}, where {{ first_name }} is a {{ role }}.
Be specific and factual. No greeting, no sign-off, no exclamation marks.
{% endai %}

Everything from here down is byte-identical for every recipient.
```

| Option | Meaning |
|---|---|
| `fallback` | Used when generation fails or is rejected. **Always set one.** |
| `max_words` / `max_chars` | Hard cap, trimmed at a sentence boundary where possible. |
| `temperature` | Lower is more consistent. Default 0.3. |
| `model` | Override the model for this slot — a cheap one for easy gaps. |
| `banned_phrases` | Reject output containing any of these. |

Slot names must be unique; they are how approved text is matched back.

## Providers

| Provider | Notes |
|---|---|
| **OpenAI** | `gpt-4.1-mini` and friends. |
| **OpenAI-compatible** | Ollama, LM Studio, vLLM, llama.cpp, Groq, OpenRouter, Together. Point `base_url` at it. |
| **Offline demo** | Predictable placeholder text. Try the whole flow with no key. |

### Running fully local

```
Provider:  Local or other (OpenAI-compatible)
Base URL:  http://localhost:11434/v1
Model:     llama3.1
```

No API key, no cloud call. The "nothing leaves your machine" promise stays
literally true.

## Cost

Three things keep it small:

1. **Caching** — keyed on the provider, model, instruction, variables and
   constraints. Re-running a 500-contact campaign after an unrelated typo costs
   nothing.
2. **Prompt caching** — the instruction is identical for every recipient and
   only the variables change, which is exactly what prompt caching is for.
3. **Tight token budgets** — a 25-word slot is not allocated 400 tokens.

Ask for an estimate before you spend anything:

```bash
sahajmails ai estimate contacts.csv -t email.md
```

## From the command line

```bash
sahajmails ai fill contacts.csv -t email.md -o review.csv
$EDITOR review.csv          # read it, fix anything you dislike
sahajmails send contacts.csv -t email.md --filled review.csv
```

## Turning it off

A template with `{% ai %}` blocks renders perfectly with no provider configured
— every slot uses its fallback. Nothing breaks.
