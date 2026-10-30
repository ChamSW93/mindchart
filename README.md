# MindChart

A private, offline app for keeping child and adolescent psychiatric records and drafting the **history section of a psychiatric evaluation** from source records: past psychiatric and psychological evaluations, clinical notes, IEPs and court orders, in PDF or Word format.

Everything runs on the Mac. The app, its server and the AI model (through [Ollama](https://ollama.com)) never send record contents over the internet.

> **This repository contains code only.** Patient data is never stored here. MindChart keeps it in an encrypted store on each Mac, outside the app folder. `sample-records/` holds **fictional** records for testing.

## Installing

See [TESTING.md](TESTING.md). In short, paste this into Terminal on an Apple Silicon Mac with 16 GB or more:

```
curl -fsSL https://raw.githubusercontent.com/ChamSW93/mindchart/main/install.sh | bash
```

## Using it

- **Sign in.** The first launch asks for a password, which encrypts all of MindChart's data. Every launch after that asks for it again. MindChart locks itself after 15 minutes without activity, when 🔒 Lock is clicked, or when its Terminal window is closed. **A lost password can't be recovered.**
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

Running offline removes the biggest risk, which is sending records to a cloud AI service. **That alone doesn't make it HIPAA-compliant.** Before using real records:

- Get approval from the organization's privacy/compliance officer and IT. Records may also fall under NY Mental Hygiene Law §33.13, 42 CFR Part 2 (substance use), FERPA (IEPs) and Family Court confidentiality rules.
- Use an approved Mac with FileVault on, a strong login password and automatic screen lock.
- Store exported Word drafts and the original files only where the organization allows. Inside MindChart, everything is in the encrypted store.
- Don't point AI coding assistants (which use the cloud) at real records. Develop with the fictional samples only.

**Where the data is:** `~/Library/Application Support/MindChart/Patient Records.sparsebundle`, an AES-256 encrypted disk image built into macOS. Its password is never stored. Time Machine backs it up still encrypted. Every sign-in, view, edit, build and deletion is recorded under Activity.

## Hardware and models

MindChart reads the Mac's memory and picks a model that fits the GPU's share of it (about two thirds of memory). It also sets how rich the animations are.

| Mac memory | Default model | Effects |
|---|---|---|
| 24 GB or more | gpt-oss:20b | Rich |
| 16 GB (e.g. MacBook Air M2) | bonsai-2:27b (being evaluated) | Standard |
| 8 GB | bonsai-2:9b | Minimal |

macOS "Reduce motion" turns animations off. Other downloaded models can be picked at the bottom of the sidebar, and ones too big for the Mac are labelled. To compare a new model, run it on `sample-records/` first.

Results on an M3 with 24 GB, scoring 32 key facts known to be in the fictional records:

| Model | Key facts found | Caught the planted contradiction | Time |
|---|---|---|---|
| gpt-oss:20b | 27–32 / 32 | Yes | 11–15 min |
| bonsai-2:27b | 31 / 32 (earlier version) | No | 33 min |
| mistral-nemo | 23 / 32 (earlier version) | Partly | 11 min |

## Updates

Installed copies check for a newer release each time they start, download it, verify its checksum and replace only the app's files. The data store isn't touched. To publish a version:

```
scripts/release.sh 0.2.0 "What changed"
```

This builds the package, commits, tags, pushes and creates the GitHub release. Renaming the app means changing `APP_NAME` in `server.py`. The data location doesn't depend on the name.

## Files

| File | What it does |
|---|---|
| `server.py` | Local web server (listens only to this Mac), API, hardware detection |
| `vault.py` | Encrypted store, objects and fields, record IDs, activity log |
| `history.py` | Fact extraction, quote checking, section drafting, Word export |
| `assistant.py` | The command bar: questions and logging proposals |
| `documents.py` | Reads PDF (with OCR for scans), Word, text and spreadsheet files |
| `app.html`, `static/` | The app's page, styles and script |
| `chat.html` | General chat with the local model |
| `updater.py` | Self-updates from GitHub releases |
| `install.sh` | One-line installer |
| `scripts/release.sh` | Publishes a new version |
| `tools/pdftext.swift` | PDF reader with OCR, using built-in macOS frameworks |
| `sample-records/` | **Fictional** test records. Not real patients. |
