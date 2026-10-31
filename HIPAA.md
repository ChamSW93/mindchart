# MindChart: security and HIPAA overview

For privacy, compliance and IT reviewers. It describes how MindChart protects patient records, how each protection was tested, and what the organization using MindChart remains responsible for.

> **Scope.** HIPAA compliance belongs to the covered entity (the organization), not to a piece of software: no product can be "HIPAA certified". MindChart is designed to meet the HIPAA Security Rule's **technical safeguards** (45 CFR §164.312) on the Mac it runs on. The **administrative and physical safeguards** (risk analysis, policies, training, device management, agreements) are the organization's to put in place, and are listed below. This document is a technical description, not legal advice.

## What MindChart is

A Mac app that keeps child and adolescent psychiatric records (patients, source documents, extracted facts, drafted evaluation histories) and uses an AI model **running on the same Mac** to draft history sections from source documents.

**Where patient data goes:**

```
Source files (PDF/Word/photos) ──▶ MindChart on this Mac ──▶ Encrypted store on this Mac
                              │   AI model (Ollama) on this Mac, localhost only
                              └─▶ Word export: only when the clinician chooses (unencrypted file, logged)
Nothing is sent over the internet. Updates download code only, and never upload anything.
```

## AI models

MindChart runs one open-weight AI model, chosen by the Mac's memory and downloaded once through Ollama. A model is a
file of numbers that runs on the Mac: it doesn't connect to its publisher, and nothing is sent to them. The model can
be changed at the bottom of MindChart's sidebar.

| Mac memory | Model | Publisher | License | Built on |
|---|---|---|---|---|
| 24 GB or more | gpt-oss:20b | OpenAI | Apache 2.0 | (OpenAI's own) |
| 16 GB | ornith-1.5:9b | Ornith Team, a research group ([ornith.ai](https://ornith.ai), [Hugging Face](https://huggingface.co/ornith-ai/Ornith-1.5-9B)) | MIT | Alibaba's Qwen 3.5 (Apache 2.0) |
| 8 GB | bonsai-2:9b | Prism ML ([prismml.com](https://prismml.com)) | Apache 2.0 | Qwen 3.5 architecture |

All three licenses allow use within an organization, including commercial use; MIT and Apache 2.0 ask that the copyright
notice be kept. (Ollama's copy of Ornith leaves out its license text; its publisher's Hugging Face page gives MIT.)
Licenses and publishers were checked on 10/31/2026. Models from Mistral AI were tested and not chosen; Meta's
Llama models weren't tested.

Whatever the model, MindChart limits what it can do: facts need a quote found in the source document, drafts and
cottage briefings cite their sources, and any change to records is a proposal the clinician approves.

## Technical safeguards (45 CFR §164.312)

| Safeguard | How MindChart meets it | How it's verified |
|---|---|---|
| **Unique user identification** §164.312(a)(2)(i), required | The clinician enters their name at setup. Every activity entry records it together with the macOS account. | Automated test checks the name appears on logged edits |
| **Emergency access procedure** §164.312(a)(2)(ii), required | Records open only with the MindChart password, which isn't stored anywhere (unless Touch ID is turned on, below) and can't be recovered by anyone. The password can be changed in Settings. **The organization must keep the password in a controlled place** (see below) so records stay reachable if the clinician is unavailable. | Password change tested, including a wrong current password |
| **Automatic logoff** §164.312(a)(2)(iii) | Locks after 15 minutes without activity, when Lock is clicked, when the app quits, and if the app crashes (its server notices and locks). Locking removes records from the screen. | Tested: normal quit, forced quit (crash), idle lock |
| **Encryption and decryption** §164.312(a)(2)(iv) | All records are kept in an **AES-256 encrypted disk image** (built into macOS) that only the password opens. MindChart also warns if the Mac's own disk encryption (FileVault) is off. | Raw files searched for record text while locked: none found |
| **Audit controls** §164.312(b), required | An activity log records sign-ins (including failed attempts), uploads (and discarded ones), filing a document under a patient, opening patients and drafts, adding, editing and deleting records, building drafts, questions asked, and **every export**. Entries can't be edited or deleted in the app, and they're kept when a patient is deleted. The log can be exported as CSV. | Automated tests |
| **Integrity** §164.312(c)(1) | Edits record the **old and new value** of each field. The activity log is **tamper-evident**: each entry carries a SHA-256 fingerprint chained to the entry before, and Settings rechecks the whole chain. Data upgrades back up the database first and run as all-or-nothing transactions. | Tested: altering one old entry is detected; a failed upgrade leaves data unchanged |
| **Person or entity authentication** §164.312(d), required | A password of at least 10 characters opens the records. After 5 wrong attempts, each further attempt waits 30 seconds, doubling up to 15 minutes. **Touch ID (optional, off by default)** can open them instead: turning it on needs the password, which is then kept encrypted by a key in the Mac's Secure Enclave that works only on that Mac, only after a fingerprint, and stops working if fingerprints are added or removed. Each sign-in is logged with how it was done. | Automated test |
| **Transmission security** §164.312(e)(1) | Nothing is transmitted: the app, its local server and the AI model all run on the Mac. The server listens only to the Mac itself, refuses requests from other web pages (host, origin and request-header checks, including DNS-rebinding protection), and refuses uploads over 200 MB. | Automated tests. Logs searched after processing records: no record contents found. Ollama confirmed listening on localhost only |

## Further protections

- **Its own window, not a web browser.** Browser extensions can read and transmit anything on a web page. MindChart runs in its own app window, so extensions never see patient data.
- **The AI can't change records on its own.** Plain-English questions run as read-only database queries. Requests to add or change records come back as a proposal the clinician reviews and saves. Uploaded files are filed under a patient only after the clinician confirms the patient and the document type.
- **Clinical tools stay advisory.** Score bands are the scales' published cutoffs, shown alongside the score; Coming up only lists dates; medication suggestions and visit notes are drafts the clinician checks, edits and saves. Nothing is saved, prescribed or sent without the clinician.
- **Wrong-patient checks.** Each upload is matched to patients by name, date of birth and MRN. A file dropped on one patient's page that names a different child (a sibling, say) gets a warning before it can be filed.
- **Uploads and learning stay in the encrypted store.** Files waiting for review are kept inside the encrypted store, and a discarded upload is wiped from it. The examples MindChart learns document types from (each confirmed document's type and opening text) are kept there too, are deleted along with their document, and never leave the Mac.
- **Background reading doesn't keep records open.** Reading confirmed documents in the background doesn't count as activity, so MindChart still locks itself after 15 minutes alone. Reading pauses while locked.
- **Addresses stay on the Mac.** A guardian's town and drive time come from a ZIP code table built into MindChart; no address or ZIP code is looked up online.
- **Cottage briefings are experimental and advisory.** The AI reads several residents' records together, on the Mac, and every point must cite a record or it's dropped. Briefings are labelled experimental and not a clinical assessment; feedback on them stays in the encrypted store.
- **Error logs contain no record contents.** `~/Library/Logs/MindChart` records what failed and where in the code, so it's safe to send to the developer.
- **Updates are code-only and verified.** Updates download from GitHub over HTTPS, are checked against a published SHA-256 checksum, replace only the app's files, and never touch the encrypted store. They never upload anything.
- **Deleting is thorough.** Deleting a patient removes their documents, facts and drafts, and the database is rewritten so deleted records don't linger in free space.
- **Tested before every release.** An automated test suite (138 tests) runs on a clean Mac with Apple's built-in Python, and on the developer's Mac before anything is published. Publishing is blocked if any test fails.

## What the organization is responsible for

**Before using real patient records:**

1. **Approval and risk analysis.** Include MindChart in the organization's HIPAA risk analysis and get sign-off from the privacy/compliance officer. Consider New York requirements too: Mental Hygiene Law §33.13 (clinical records), 42 CFR Part 2 (substance use treatment records), FERPA (education records such as IEPs) and Family Court confidentiality.
2. **An approved, managed Mac.** FileVault on, a strong login password, automatic screen lock, current macOS updates, and no shared user accounts. MindChart's own encryption doesn't replace these.
3. **Password custody (emergency access).** Keep the MindChart password in the organization's password manager or another controlled place. A lost password means the records can't be recovered, by anyone.
4. **Exported files.** Word exports are ordinary, unencrypted files. Decide where they may be saved, how they're shared, and when they're deleted.
5. **Backups.** Time Machine backs up the store still encrypted. Decide whether backups are allowed and where backup drives are kept.
6. **Retention and disposal.** Set how long records stay in MindChart under the organization's retention policy, and delete them when that period ends.
7. **Training.** Clinicians should know that AI drafts must be checked against source records. Don't use other AI tools, or Ollama's own command line (`ollama run`, which keeps a history), with patient data. Lock or quit MindChart when stepping away.
8. **Incidents.** Include a lost or stolen Mac in the incident response plan. The encrypted store and FileVault protect the data, but the incident still needs assessing.

## Known limitations

- **One user per Mac.** There are no separate accounts or roles. Anyone with the password sees all records.
- **No two-factor sign-in.** Access depends on the password and on the Mac's own login. With Touch ID turned on, anyone whose fingerprint is enrolled on that Mac can open the records, so only turn it on when the Mac's fingerprints are all the clinician's.
- **The audit log detects tampering but can't prevent it.** Someone with the password and technical skill could delete the whole database. The fingerprint chain shows when individual entries have been altered or removed.
- **AI drafts can be wrong or incomplete.** Every sentence cites its sources, and facts whose quote isn't found in the source are left out. In testing, drafts contained 27 to 32 of 32 known key facts. The clinician must review every draft.

## For the developer

The developer publishes code updates and never has access to anyone's records: there is no upload path, no telemetry and no remote access. Feedback and bug reports should never include patient information. The error log is designed to be safe to share. Whether a business associate agreement is needed depends on the arrangement, and is a question for the organization's counsel.
