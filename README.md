# MindChart

A private, offline app for keeping child and adolescent psychiatric records and drafting the **history section of a psychiatric evaluation** from source records: past psychiatric and psychological evaluations, clinical notes, IEPs and court orders, in PDF or Word format.

Everything runs on the Mac. The app, its server and the AI model (through [Ollama](https://ollama.com)) never send record contents over the internet.

> **This repository contains code only.** Patient data is never stored here. MindChart keeps it in an encrypted store on each Mac, outside the app folder. `sample-records/` holds **fictional** records for testing.

## Installing

See [TESTING.md](TESTING.md). In short, paste this into Terminal on an Apple Silicon Mac with 16 GB or more:

```
curl -fsSL https://raw.githubusercontent.com/ChamSW93/mindchart/main/install.sh | bash
```

This puts **MindChart.app** in the Applications folder (and Launchpad), downloads the AI model that suits the Mac, and opens it. MindChart runs in its own window, with no Terminal or browser. Its code lives in `~/Library/Application Support/MindChart/App`.

**Developing.** `scripts/build_app.sh --dev` builds `~/Applications/MindChart.app` to run this working copy instead. `Start MindChart.command` also still works, and opens MindChart in a browser.

## Using it

- **Sign in.** The first launch asks for your name (shown in the activity log) and a password, which encrypts all of MindChart's data. Every launch after that asks for the password. MindChart locks itself after 15 minutes without activity, when 🔒 Lock is clicked, and when the app quits or crashes. After 5 wrong passwords, each further attempt has to wait. **A lost password can't be recovered.** It can be changed in **Settings**.
- **Records, like a CRM.** Patients, Documents, Facts, Drafts and Activity each have a list view and record pages. Every record has its own ID with a prefix for its type, such as `PT-7K3M9Q` (patient), `DOC-…`, `FCT-…`, `DRF-…` and `ACT-…`. Each type has its own fields, which can be edited on its record page.
- **The command bar** (top of every page) takes plain English:
  - *Questions* such as "Which patients have an order of protection?" become a read-only database lookup, and the results show as a table. They can never change data.
  - *Logging* such as "Add a note for Jordan: started sertraline 25 mg today", "New patient Maria Lopez, DOB 03/04/2012" or "Change Jordan's status to Discharged" comes back as a proposal to check and edit. **Nothing is saved until Save is clicked.**
- **Drafting a history.** On a patient's page: Upload documents, then Build history. Each document is read once, and its facts are saved with exact quotes and page numbers. Later builds reuse them, so only new documents take time. Corrections made to facts are used in future drafts. The draft opens for review: every sentence cites its sources (`[D2 p.4]`), red boxes flag where documents disagree, and Export to Word saves a .docx.

## How it avoids made-up content

- The model lists facts with **exact quotes**. MindChart checks every quote against the source text and leaves out any fact whose quote can't be found ("not verified").
- Each section is written only from verified facts, and **every sentence must name the facts behind it**. MindChart adds the citations itself.
- This lowers the risk but doesn't remove it. Models can miss facts or put them in the wrong section. In testing on the fictional records, drafts contained 27 to 32 of 32 key facts. **The clinician must check the draft against the source records.**

## Privacy and HIPAA

**[HIPAA.md](HIPAA.md)** is the full security and HIPAA overview, written for compliance reviewers: each HIPAA technical safeguard, how MindChart meets it, how that was tested, and what the organization is responsible for.

Running offline removes the biggest risk, which is sending records to a cloud AI service. **That alone doesn't make it HIPAA-compliant.** Before using real records:

- Get approval from the organization's privacy/compliance officer and IT. Records may also fall under NY Mental Hygiene Law §33.13, 42 CFR Part 2 (substance use), FERPA (IEPs) and Family Court confidentiality rules.
- Use an approved Mac with FileVault on, a strong login password and automatic screen lock.
- Store exported Word drafts and the original files only where the organization allows. Inside MindChart, everything is in the encrypted store.
- Don't point AI coding assistants (which use the cloud) at real records. Develop with the fictional samples only.

**Where the data is:** `~/Library/Application Support/MindChart/Patient Records.sparsebundle`, an AES-256 encrypted disk image built into macOS. Its password is never stored. Time Machine backs it up still encrypted. Every sign-in, view, edit (with old and new values), build, export and deletion is recorded under Activity, in a tamper-evident log that Settings can verify and export.

**Error log:** `~/Library/Logs/MindChart`. It records what failed and where, never record contents, so it's safe to send when reporting a problem.

## Hardware and models

MindChart reads the Mac's memory and picks a model that fits the GPU's share of it (about two thirds of memory). It also sets how rich the animations are.

| Mac memory | Default model | Effects |
|---|---|---|
| 24 GB or more | gpt-oss:20b | Rich |
| 16 GB (e.g. MacBook Air M2) | ornith-1.5:9b | Standard |
| 8 GB | bonsai-2:9b | Minimal |

macOS "Reduce motion" turns animations off. Other downloaded models can be picked at the bottom of the sidebar, and ones too big for the Mac are labelled. To compare a new model, run it on `sample-records/` first.

**Models for 16 GB Macs.** A 16 GB Mac gives the GPU about 10.7 GB, which has to hold the model and its working memory. Tested on the five fictional records, scoring 32 key facts known to be in them:

| Model | Key facts found | Caught the planted contradiction | Time* | Memory used |
|---|---|---|---|---|
| **ornith-1.5:9b** | 32 / 32 | Yes | 17 min | 5.3 GB |
| bonsai-2:27b | 31 / 32 | Yes | 37 min | 8.6 GB |
| mistral-nemo | 28 / 32 | No | 18 min | 9.7 GB |
| bonsai-2:9b | 28 / 32 | Yes | 11 min | 3.1 GB |

**On 24 GB or more**, gpt-oss:20b (13 GB) found 27 to 32 of 32 key facts across six runs, caught the contradiction every time, and took 11 to 15 minutes.

\*Measured on an M3. An M2 has the same memory speed, which mostly sets how fast these models run, so timings should be similar. Results vary a little between runs.

## Tests

```
scripts/test.sh
```

45 automated tests cover document reading (including OCR), quote checking and citations, the drafting flow, the encrypted store, the audit trail, sign-in limits, password changes, data upgrades, the command bar, the server's security checks and the updater. They run on **Apple's built-in Python** (the one every Mac has), so code that only works with a newer Python can't slip through. They also run automatically on GitHub for every change. They don't need the AI model: stand-in answers take its place.

## Updates

Installed copies check for a newer release each time they start, download it, verify its checksum and replace only the app's files (and the app in Applications). The data store isn't touched. If the recommended AI model changed, MindChart downloads it in the background. To publish a version:

```
scripts/release.sh 0.3.1 "What changed"
```

This runs the tests (and stops if any fail), builds the PDF reader and the Mac app, commits, tags, pushes and creates the GitHub release.

**Changing how data is stored:** add a step to `MIGRATIONS` in `vault.py` and raise `SCHEMA_VERSION`. Saved data is backed up and upgraded on its next unlock, all or nothing. Renaming the app means changing `APP_NAME` in `server.py`. The data location doesn't depend on the name.

## Files

| File | What it does |
|---|---|
| `server.py` | Local web server (listens only to this Mac, refuses other web pages), API, error log |
| `vault.py` | Encrypted store, objects and fields, record IDs, tamper-evident activity log, data upgrades |
| `models.py` | Which AI model suits this Mac, and downloading it |
| `history.py` | Fact extraction, quote checking, section drafting, Word export |
| `assistant.py` | The command bar: questions and logging proposals |
| `documents.py` | Reads PDF (with OCR for scans), Word, text and spreadsheet files |
| `app.html`, `static/` | The app's page, styles and script |
| `chat.html` | General chat with the local model |
| `updater.py` | Self-updates from GitHub releases |
| `install.sh` | One-line installer |
| `tools/MindChartApp.swift` | The Mac app: its window, menus, file pickers and Save dialogs |
| `tools/make_icon.swift` | Draws the app icon |
| `tools/pdftext.swift` | PDF reader with OCR, using built-in macOS frameworks |
| `scripts/build_app.sh` | Builds MindChart.app |
| `scripts/test.sh`, `tests/` | The automated tests |
| `scripts/release.sh` | Publishes a new version (only if the tests pass) |
| `HIPAA.md` | Security and HIPAA overview for compliance reviewers |
| `sample-records/` | **Fictional** test records. Not real patients. |
