"""MindChart's chat: an AI assistant with the knowledge and judgment of an experienced psychiatric nurse practitioner.

It runs on the local model (through Ollama), so nothing leaves the Mac. Three things make it a specialist rather than a
general chatbot:
- its instructions (SYSTEM_PROMPT): how a child and adolescent PMHNP assesses, prescribes, keeps youth safe and writes
  documentation, and strict rules against inventing patient details;
- tools for what language models get wrong when they do it in their heads: ages, time between dates and rating-scale
  scores (TOOLS, run_tool);
- the clinician's own writing preferences (vault.writing_preferences), followed in everything it writes.
The instructions are kept short on purpose: on an 8 GB Mac every extra word slows each answer down.
"""
import calendar
import datetime
import json
import re
import urllib.error
import urllib.request

import fields as fieldtypes
import history

SYSTEM_PROMPT = """You are MindChart, an assistant with the knowledge and judgment of an experienced psychiatric-mental health
nurse practitioner (PMHNP-BC) who treats children, adolescents and their families, including in residential treatment.
You work alongside the clinician, who makes every clinical decision. Today is {today}.

What you help with:
- Assessment: interviews, history, mental status exams, DSM-5-TR criteria and differential diagnoses (including medical,
  substance and trauma-related causes), rating scales, collateral from families, schools and agencies.
- Safety: suicide, self-harm and violence risk, safety planning, abuse or neglect concerns and mandated reporting.
- Psychopharmacology: evidence-based options for youth, FDA approvals and boxed warnings, starting doses and titration,
  side effects, interactions and monitoring (such as metabolic labs for antipsychotics).
- Treatment: therapies (TF-CBT, CBT, DBT, PCIT, motivational interviewing), care in residential settings, and working with
  foster care, schools (IEPs, 504 plans) and courts.
- Documentation: psychiatric evaluations, progress notes (SOAP), medication management notes, treatment plans,
  discharge summaries and letters, with standard headings and concise clinical wording.

Rules:
- Facts about a patient come only from what the clinician writes or attaches. Never invent names, dates, ages, doses,
  scores, quotes, history or findings. In a draft, write [not documented] where something is missing.
- Use the tools for every age, time between dates and rating-scale score. Never work them out yourself.
- Keep what was reported apart from your own clinical reasoning, and label suggestions as things to consider.
- Offer diagnoses as differentials: the criteria that are met, what's missing and what to assess next.
- For a medication, say whether it's FDA-approved for the youth's age and condition or used off-label. Doses are general
  information: end with "Check doses against current prescribing information and the youth's age and weight."
- Put any sign of risk to the youth or others, abuse or neglect at the top of your answer.
- Say when you're unsure. Never make up guidelines, studies or citations.
- Attached files were read on this Mac. Text read from handwriting can have misread words: point them out rather than guess.
- Be concise and clinical: headings and short bullet points. If a request is unclear, ask one short question first."""

PREFERENCES_NOTE = """

How this clinician likes things written (follow these unless they conflict with the rules):
{preferences}"""

FILE_CHARS = 24000  # the most of one file the model sees at once (about 6,000 words)

TOOLS = [
    {"type": "function", "function": {
        "name": "calculate_age",
        "description": "Exact age in years and months on a date, from a date of birth. Use it for every age.",
        "parameters": {"type": "object", "required": ["date_of_birth"], "properties": {
            "date_of_birth": {"type": "string", "description": "MM/DD/YYYY"},
            "on_date": {"type": "string", "description": "MM/DD/YYYY; leave empty for today"}}}}},
    {"type": "function", "function": {
        "name": "time_between",
        "description": "Exact time between two dates (say, since admission or since a medication was started).",
        "parameters": {"type": "object", "required": ["start_date"], "properties": {
            "start_date": {"type": "string", "description": "MM/DD/YYYY"},
            "end_date": {"type": "string", "description": "MM/DD/YYYY; leave empty for today"}}}}},
    {"type": "function", "function": {
        "name": "score_rating_scale",
        "description": "Total and published severity band of a rating scale, from its item scores or its total. "
                       "Scales: PHQ-A, PHQ-9, GAD-7, SCARED, CRAFFT.",
        "parameters": {"type": "object", "required": ["scale"], "properties": {
            "scale": {"type": "string", "enum": ["PHQ-A", "PHQ-9", "GAD-7", "SCARED", "CRAFFT"]},
            "item_scores": {"type": "array", "items": {"type": "integer"}, "description": "each item's score, in order"},
            "total": {"type": "integer", "description": "the total, if the item scores aren't given"}}}}},
]

# Each scale: its bands (fields.MEASURE_BANDS), number of items and highest item score. CRAFFT counts its 6 yes/no items.
SCALES = {"PHQ-A": ("PHQ-A (depression)", 9, 3), "PHQ-9": ("PHQ-A (depression)", 9, 3), "GAD-7": ("GAD-7 (anxiety)", 7, 3),
          "SCARED": ("SCARED (anxiety)", 41, 2), "CRAFFT": ("CRAFFT (substance use)", 6, 1)}
TOOL_LABELS = {"calculate_age": "age calculator", "time_between": "date calculator", "score_rating_scale": "rating scale scorer"}


def parse(text, field):
    if not str(text or "").strip():
        return datetime.date.today()
    found = fieldtypes.parse_date(text)
    if not found:
        raise ValueError(f"{field} isn't a date MindChart can read; write it as MM/DD/YYYY")
    return found


def span(start, end):
    """Whole years, months and days from start to end (end not before start)."""
    months = (end.year - start.year) * 12 + end.month - start.month - (end.day < start.day)
    year, month = divmod(start.month - 1 + months, 12)
    year, month = start.year + year, month + 1
    anniversary = datetime.date(year, month, min(start.day, calendar.monthrange(year, month)[1]))  # (Jan 31 + 1 month: Feb 28)
    return months // 12, months % 12, (end - anniversary).days


def run_tool(name, arguments):
    """Run one of TOOLS. Returns a dict for the model; mistakes in its arguments come back as {"error"} so it can fix them."""
    try:
        if name == "calculate_age":
            born, on = parse(arguments.get("date_of_birth"), "date_of_birth"), parse(arguments.get("on_date"), "on_date")
            if on < born:
                raise ValueError("on_date is before the date of birth")
            years, months, _ = span(born, on)
            return {"age_years": years, "and_months": months, "on": on.strftime("%m/%d/%Y")}
        if name == "time_between":
            start, end = parse(arguments.get("start_date"), "start_date"), parse(arguments.get("end_date"), "end_date")
            if end < start:
                start, end = end, start
            years, months, days = span(start, end)
            return {"years": years, "months": months, "days": days, "total_days": (end - start).days,
                    "total_weeks": round((end - start).days / 7, 1), "from": start.strftime("%m/%d/%Y"), "to": end.strftime("%m/%d/%Y")}
        if name == "score_rating_scale":
            scale = str(arguments.get("scale") or "").upper().replace(" ", "")
            if scale not in SCALES:
                raise ValueError(f"Unknown scale; use one of {', '.join(SCALES)}")
            bands, count, highest = SCALES[scale]
            items = arguments.get("item_scores") or []
            if items:
                if len(items) != count or any(not isinstance(i, int) or not 0 <= i <= highest for i in items):
                    raise ValueError(f"{scale} has {count} items, each scored 0 to {highest}")
                total = sum(items)
            elif isinstance(arguments.get("total"), int) and 0 <= arguments["total"] <= count * highest:
                total = arguments["total"]
            else:
                raise ValueError(f"Give the {count} item scores or a total from 0 to {count * highest}")
            result = {"scale": scale, "total": total, "band": fieldtypes.measure_band(bands, total)}
            if scale in ("PHQ-A", "PHQ-9") and items and items[8] > 0:
                result["warning"] = "Item 9 (thoughts of death or self-harm) is above 0: assess suicide risk now."
            return result
        return {"error": f"There's no tool called {name}"}
    except ValueError as error:
        return {"error": str(error)}


# Rating-scale scores in the clinician's message are scored by MindChart itself before the AI answers (score_scales), so an
# item 9 above 0 on the PHQ-A is never missed, however the model reads the message.
SCALE_NAME = re.compile(r"\b(PHQ-?A|PHQ-?9|GAD-?7|SCARED|CRAFFT)\b", re.I)
CANONICAL = {"PHQA": "PHQ-A", "PHQ9": "PHQ-9", "GAD7": "GAD-7", "SCARED": "SCARED", "CRAFFT": "CRAFFT"}


def score_scales(text):
    """The rating scales a message gives scores for ("PHQ-A today, item scores 1,2,1,2,1,1,1,0,1" or "GAD-7 of 12"), scored
    with score_rating_scale. A number that can't be read for sure (a date, say) is left alone rather than guessed."""
    found = []
    for match in SCALE_NAME.finditer(text or ""):
        scale = CANONICAL[match.group(1).upper().replace("-", "")]
        _, count, _ = SCALES[scale]
        after = text[match.end():match.end() + 160].split("\n")[0]
        items = re.search(r"(?<![\d/])(\d{1,2}(?:\s*[,;]\s*|\s+)){%d}\d{1,2}(?![\d/])" % (count - 1), after)
        total = re.match(r"\D{0,30}?(?<![\d/])(\d{1,2})(?![\d/])", after)
        if items:
            result = run_tool("score_rating_scale", {"scale": scale, "item_scores": [int(n) for n in re.findall(r"\d+", items.group(0))]})
        elif total:
            result = run_tool("score_rating_scale", {"scale": scale, "total": int(total.group(1))})
        else:
            continue
        if "error" not in result and result not in found:
            found.append(result)
    return found


def scored_note(scored):
    """How MindChart's own scoring reads, for the model and on the page."""
    return "; ".join(f"{s['scale']} total {s['total']} ({s['band']}){': ' + s['warning'] if s.get('warning') else ''}" for s in scored)


def model_messages(messages, preferences=(), context_chars=40000):
    """The conversation as the model sees it: the instructions, then the newest messages that fit in context_chars (the
    oldest are left out first). Attached files go in front of the message they came with, cut to FILE_CHARS each."""
    system = SYSTEM_PROMPT.format(today=datetime.date.today().strftime("%A, %B %d, %Y"))
    if preferences:
        system += PREFERENCES_NOTE.format(preferences="\n".join(f"- {p}" for p in preferences))
    turns, limit = [], min(FILE_CHARS, context_chars)
    for m in messages:
        content = m.get("content") or ""
        if m["role"] == "user":
            files = "".join(
                f'<file name="{f["name"]}">\n'
                + ("[Read from handwriting or a poor copy: some words may be misread.]\n" if f.get("unclear") else "")
                + (f["text"][:limit] + ("\n[The rest of this file was too long to include.]" if len(f["text"]) > limit else ""))
                + "\n</file>\n\n" for f in m.get("files") or [])
            content = files + (content or "Please summarize the attached file.")
            if m.get("scored"):
                content += f"\n\n[Scored by MindChart: {scored_note(m['scored'])}]"
        turns.append({"role": m["role"], "content": content})
    kept, used = [], 0
    for turn in reversed(turns):
        if kept and used + len(turn["content"]) > context_chars:
            break
        kept.insert(0, turn)
        used += len(turn["content"])
    while kept and kept[0]["role"] != "user":  # the conversation starts with the clinician
        kept.pop(0)
    return [{"role": "system", "content": system}] + kept


def reply(model, thinking, messages, on_text, on_thinking=None, should_stop=lambda: False, context_length=None, max_tokens=3000,
          max_rounds=4):
    """Answer the conversation (from model_messages), streaming the answer to on_text as it's written and running any
    tools the model asks for. thinking: whether the model can think (it's turned off, or kept low for gpt-oss, so it
    answers sooner). context_length and max_tokens: how much the model reads and writes (smaller on an 8 GB Mac).
    Returns {"text", "tools": [names used], "stopped"}."""
    conversation, text, used = list(messages), "", []
    for round_ in range(max_rounds):
        request = {"model": model, "messages": conversation, "stream": True,
                   "options": {"num_ctx": context_length or history.CONTEXT_LENGTH, "temperature": 0.3, "num_predict": max_tokens}}
        if round_ < max_rounds - 1:  # (the last round must answer)
            request["tools"] = TOOLS
        if thinking:
            request["think"] = "low" if model.startswith("gpt-oss") else False
        calls, said = [], ""
        try:
            with urllib.request.urlopen(urllib.request.Request(history.OLLAMA_URL, data=json.dumps(request).encode(),
                                                               headers={"Content-Type": "application/json"}), timeout=900) as response:
                for line in response:
                    if should_stop():
                        return {"text": text, "tools": used, "stopped": True}
                    part = json.loads(line)
                    if part.get("error"):
                        raise ValueError(part["error"])
                    message = part.get("message", {})
                    if message.get("thinking") and on_thinking:
                        on_thinking(message["thinking"])
                    if message.get("content"):
                        chunk = message["content"].translate(history.PLAIN)
                        said += chunk
                        text += chunk
                        on_text(chunk)
                    calls += message.get("tool_calls") or []
        except urllib.error.URLError as error:
            if isinstance(error.reason, ConnectionRefusedError):
                raise history.AIUnavailable() from error
            raise
        if not calls:
            return {"text": text, "tools": used, "stopped": False}
        conversation.append({"role": "assistant", "content": said, "tool_calls": calls})
        for call in calls:
            name = call.get("function", {}).get("name", "")
            arguments = call.get("function", {}).get("arguments") or {}
            if isinstance(arguments, str):
                try:
                    arguments = json.loads(arguments)
                except ValueError:
                    arguments = {}
            result = run_tool(name, arguments)
            if name in TOOL_LABELS and TOOL_LABELS[name] not in used:
                used.append(TOOL_LABELS[name])
            conversation.append({"role": "tool", "tool_name": name, "content": json.dumps(result)})
    return {"text": text, "tools": used, "stopped": False}
