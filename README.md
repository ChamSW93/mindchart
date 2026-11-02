# MindChart

A private, offline app for keeping child and adolescent psychiatric records and drafting a **psychiatric evaluation** from many sources: the clinician's own evaluation notes plus the records, past psychiatric and psychological evaluations, clinical notes, IEPs and court orders, as PDFs, Word files, photos or faxes.

Everything runs on the Mac. The app, its server and the AI model (through [Ollama](https://ollama.com)) never send record contents over the internet.

> **This repository contains code only.** Patient data is never stored here. MindChart keeps it in an encrypted store on each Mac, outside the app folder. `sample-records/` holds **fictional** records for testing.

## Installing

See [TESTING.md](TESTING.md). In short, paste this into Terminal on an Apple Silicon Mac with macOS 12 or newer and 8 GB or more (16 GB or more recommended):

```
curl -fsSL https://raw.githubusercontent.com/ChamSW93/mindchart/main/install.sh | bash
```

This puts **MindChart.app** in the Applications folder (and Launchpad), downloads the AI model that suits the Mac, and opens it. MindChart runs in its own window, with no Terminal or browser. (If the account can't add apps there, it uses the Applications folder in the home folder instead.) Its code lives in `~/Library/Application Support/MindChart/App`. The app and PDF reader are built for macOS 12 and newer, and publishing stops if a build would need anything newer.

**Developing.** `scripts/build_app.sh --dev` builds `~/Applications/MindChart.app` to run this working copy instead. `Start MindChart.command` also still works, and opens MindChart in a browser.

## Using it

- **Sign in.** The first launch asks for your name (shown in the activity log) and a password, which encrypts all of MindChart's data. Every launch after that asks for the password, or a fingerprint once **Touch ID** is turned on in Settings (the password is then kept encrypted by the Mac's Secure Enclave, and opens only after Touch ID on that Mac). MindChart locks itself after 15 minutes without activity, when 🔒 Lock is clicked, and when the app quits or crashes. After 5 wrong passwords, each further attempt has to wait. **A lost password can't be recovered.** It can be changed in **Settings**.
- **A tour, and help on every page.** The first time MindChart opens (and once after updating to 0.5.0), a short tour points out each part of the app; it can be skipped. A getting-started checklist on Home ticks itself off as each feature is tried. The **?** button next to the command bar explains what can be done on the current page, and Settings can restart the tour.
- **Security, in plain language.** A green padlock marks where records are protected: the sign-in screen, the sidebar ("Encrypted · on this Mac"), Uploads and Settings. Clicking it explains, in plain terms, what keeps records private and what's up to the clinician (exported Word files, the Mac's own FileVault and screen lock).
- **Records, like a CRM.** Patients, Guardians, Cottages, Incidents, Follow-ups, Medications, Documents, Facts, Drafts and Activity each have a list view and record pages. Every record has its own ID with a prefix for its type, such as `PT-7K3M9Q` (patient), `LG-…` (legal guardian), `CTG-…` (cottage), `INC-…` (incident), `FUP-…`, `MED-…`, `MSR-…` (measure), `DOC-…`, `FCT-…`, `DRF-…` and `ACT-…`.
- **Dates.** Every date box can be typed into or filled from a small calendar, with month and year lists for dates of birth. The slashes go in as you type ("12271994" becomes 12/27/1994, "3/4/24" becomes 03/04/2024), and a day that doesn't exist is flagged before saving. Partial dates from the records, like "2019", are kept as written.
- **Legal guardians.** A patient can have several, and a guardian several patients. A guardian's **ZIP code** gives their town and a rough drive time from Children's Village, worked out on the Mac from a built-in ZIP code table (nothing is looked up online).
- **Medications** are picked from a searchable list of about 100 common psychiatric and general medications (brand names find them too, and "Zoloft" is saved as Sertraline), or typed in as new ones. How often is chosen from standard choices (Daily, At bedtime, Twice a day, Every 6 hours, Weekly, As needed…), and shorthand such as "BID" or "qhs" is saved the standard way. The Medications page shows, for each medication, who is on it now and who was before.
- **Incidents** (fights, injuries, running away, restraints…) link to every youth involved and show on each one's page and timeline. Describe one in the bar at the top ("Eric had mild wounds from the incident with Janno") and MindChart names everyone involved, matches them to their records, offers a new patient record for anyone not on file, and shows it all to check before saving. It keeps only the date, time and severity actually said.
- **Cottages.** The Children's Village cottages, each with its current and past residents (a patient's Cottage is chosen from them, A to Z). **Experimental:** the AI can write a short briefing on a cottage's dynamics, or on dynamics across cottages, from the residents' records: shared themes, what's recorded between residents, and what to watch for, every point linked to the records it comes from. It's not a clinical assessment. Each briefing asks for feedback (Helpful or Not helpful, and what to do differently), and recent feedback is passed to the AI next time.
- **Object → record type → fields.** People and documents have **record types**, and each record type adds its own fields to the object's shared ones:
  - People: **Patient** and **Legal Guardian**. Everyone has a first and last name. Patients also have date of birth, gender (Male, Female or X), MRN, status, a **placement** (Residential treatment, Crisis / stabilization, Foster care or Outpatient / community), whose own details show for it (cottage and admission dates, program, foster home and caseworker, clinic), and a **legal guardian**, picked from the guardians' records (or added on the spot). Guardians have a relationship, organization, phone, email and address, and their page lists the patients they're responsible for, so siblings can share one.
  - Documents: Court Order (case type, court, docket number, judge, dates, order of protection, visitation terms), IEP (classification, class ratio, school, grade, services, accommodations, behavior plan), Psychological Evaluation (examiner, tests, Full Scale IQ and index scores), Psychiatric Evaluation, Discharge Summary, Progress Note, Clinician Note and Other. New document types (say, Safety Plan) can be added from an upload's review card.

  When a document is read, **the AI fills in its record type's fields**, each backed by an exact quote that's checked against the source and shown under the value with an **✦ AI** badge. Values that someone has edited are never overwritten. The full list is in `fields.py`.
- **Field types can change later.** Field definitions are stored as data. Turning free text (say, Cottage or Medications) into a picklist converts every saved value, lists any that don't fit, backs up first, and logs the change. Supported types: text, long text, number, date, date-time, checkbox, picklist and multi-select picklist.
- **The command bar** (top of every page) takes plain English:
  - *Questions* such as "Which patients have an order of protection?" become a read-only database lookup, and the results show as a table. They can never change data.
  - *Logging* such as "Add a note for Jordan: started sertraline 25 mg today", "New patient Maria Lopez, DOB 03/04/2012", "Change Jordan's status to Discharged", "Jordan's PHQ-A was 11 today", "Casey started aripiprazole 2 mg daily today" or "Remind me to renew Maria's consent by 12/01" comes back as a proposal to check and edit. **Nothing is saved until Save is clicked.**
- **Uploading.** Drop files anywhere in MindChart, or click Upload documents (on Home, Documents or a patient's page). PDFs, Word files, photos (including iPhone HEIC) and faxes (including multi-page TIFFs) are accepted. Scanned and photographed pages are read with Apple's built-in OCR on the Mac, which also handles sideways and upside-down pages, scans with a stamp of real text on them, and pictures pasted into Word files. Pages it can't read well (messy handwriting, poor copies) are flagged "hard to read".
- **Checking pages.** **Check pages** (on an upload's card, or a filed document's page) shows each page's picture beside the text MindChart read, hard-to-read pages first, so misread handwriting can be corrected. The corrected text is used from then on: an upload is identified again, and a filed document is read again (facts edited by hand are kept). OCR can be confidently wrong about handwriting, so handwritten pages are worth checking even when they aren't flagged. The page pictures are made inside the encrypted store.
- **Reviewing uploads.** Files wait in **Uploads** until they're checked. Each one gets a review card:
  - **Type**, with how sure MindChart is (Sure, Likely or Unsure) and why.
  - **Patient**, matched by name, date of birth and MRN against patients already in MindChart. A file about a child who isn't in MindChart yet offers a new patient, filled in from the document. A file dropped on one patient's page that names someone else (say, a sibling) gets a warning.
  - **Date and author**, read from the document.
  - **What to take from it**: facts for history drafts, and which of the type's details to fill in. The choice is remembered for that type.

  Nothing is filed until Confirm is clicked. "Confirm all that are ready" files the ones MindChart is sure of, with no warnings. The AI reads each file's opening text in the background, so cards fill in within a minute or so.
- **Learning document types.** Every confirmation (and every later correction of a document's type) is kept as an example in the encrypted store. New uploads are compared with those examples word by word, so documents like ones seen before, such as an agency's note template or a particular court's orders, are recognized straight away. The most similar examples are also shown to the AI. Before there are examples, built-in wording hints give a first guess. The AI model itself never changes, and nothing learned leaves the Mac.
- **Learning from corrections.** The AI model itself never changes; what MindChart learns is shown to it as examples, and stays in the encrypted store.
  - **Corrected facts.** When a fact the AI found is corrected or deleted, it's kept as an example. The next time a document of that type is read, the AI is shown the 8 most recent corrections for that type and told never to take anything from them (a fact copied from one would be left out anyway, since its quote isn't in the document being read).
  - **Writing preferences.** After editing an evaluation draft, **Suggest from my edits** has the AI compare the clinician's version with its own and suggest up to five general habits, such as "Refer to the patient as 'the youth'". Suggestions naming the patient or their family, or containing a number, date or medication, are dropped, and nothing is kept until the clinician approves it. Kept preferences are followed in every evaluation from then on.
  - **Settings → What MindChart has learned** shows both. Corrections can be forgotten for a document type, and preferences edited, deleted or added.
- **When facts were true.** Records from different years say different things: "10th grade" in a 2023 evaluation and "11th grade" in a 2024 IEP are both right, for their dates. So every fact is tagged as it's read: an **event** (happened once, on its date), **ongoing** (true when its record was written, and can change: grade, placement, custody, a medication's dose, a diagnosis) or **background** (doesn't change, such as birth history). Ongoing facts also get a topic, such as "grade" or "medication: sertraline".
  - Which fact on a topic is **current** is then worked out from the records' dates, not by the AI: the newest is current, and older ones are marked **earlier**. Broad topics like "symptoms" never make other facts earlier, since newer nightmares don't mean older anxiety has gone.
  - Every fact shows when it was true ("as of 09/18/2024", "earlier"), and a patient's page has a **Current picture**: the newest information on school, home and legal status, treatment and how they're doing, with what earlier records said under each.
  - Evaluations are written the same way: earlier facts are never called current, and change over time isn't flagged as a disagreement.
  - Facts read before this existed are dated in the background, and notes typed into MindChart are read in the background like files, so their facts show up straight away. A fact's kind, topic and date can be corrected by hand, and MindChart learns from the correction.
- **Background reading.** Confirmed documents are read in the background, one at a time: facts pulled out and the type's details filled in. The sidebar shows what's being read. It pauses while MindChart is locked and carries on after signing in. It doesn't count as activity, so MindChart still locks itself when left alone.
- **Follow-ups and Coming up.** Each patient keeps follow-ups (court dates, IEP meetings, consents, labs, appointments). Home's **Coming up** lists everything due in the next six weeks, and anything overdue, across all patients. It also picks up court order expiry dates, IEP annual reviews (a year after the last meeting), planned discharges and medications still waiting for consent, by itself.
- **Medications.** Current and past medications per patient, with dose, target symptoms, response, why it was stopped, and **consent** (on file, requested, from whom and when). **Suggest from records** drafts the list from the medication facts in the patient's documents, each with the documents it came from, for the clinician to check and add.
- **Rating scales and vitals.** PHQ-A, GAD-7, SCARED (with subscales), Vanderbilt ADHD, CRAFFT, weight, height, BMI (worked out from height and weight), blood pressure and pulse, charted over time. PHQ-A, GAD-7, SCARED and CRAFFT totals show their published score bands, and a PHQ-A question 9 above 0 is flagged. Only totals are kept, never a questionnaire's wording.
- **Timeline.** A patient's history in date order: dated facts from their documents (verified ones only, including partial dates like "March 2019"), documents, medications started and stopped, scores, follow-ups and admission.
- **Visit notes.** On a patient's page, Write visit note: the clinician types what happened since the last visit, clicks through a **mental status exam** (with "Mark the usual findings", and choices that rule each other out, such as "denies suicidal ideation" and "suicidal ideation with plan"), and adds an assessment and plan. Current medications and latest scores can be included. The AI writes it up as Subjective, Objective, Assessment and Plan without adding anything (or it can be put together without AI). It can then be edited, **copied** into the agency's records system, or saved to the patient's record, where it feeds future history drafts.
- **Search inside documents.** The Documents list searches each document's text, not just its name.
- **Several photos of one document** (pages of handwritten notes, say) can be ticked in Uploads and combined into one document, read as a whole. **Evaluation notes** is its own document type, so a clinician's working notes aren't mistaken for a finished evaluation.
- **Writing an evaluation.** On a patient's page, click **Write evaluation**: tick the sources, add your evaluation notes (type, paste or dictate them, or upload them, as photos combined into one document if need be), and choose **Psychiatric evaluation** or **Detailed history**.
  - The evaluation is written the way a clinician writes one: concise, with repeated events synthesized and empty sections left out, in the usual order (identifying information, reason for referral, interview and current presentation, the history sections, mental status, risk assessment, formulation and impression, diagnoses, recommendations).
  - The history comes from all the sources; the interview, mental status, risk assessment, impression, diagnoses and plan come **only from the clinician's evaluation notes**, never from the AI, and a past evaluation's diagnoses stay history.
  - Every sentence cites its sources (`[D2 p.4]`), red boxes flag where sources disagree, and facts whose quote isn't found are left out. Documents already read are reused, and corrections to facts are used.
  - The writing runs on its own, so the rest of MindChart can be used meanwhile and the page can be closed and reopened. MindChart keeps the Mac from sleeping while it writes.
  - **Strong warnings** that the AI can leave things out or state things the records don't say ("hallucinate") appear before writing, at the top of every draft and in bold red at the top of the Word file. The latest evaluation shows on the patient's page.
  - **Detailed history** instead lists everything the records say, section by section.

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

**Where the data is:** `~/Library/Application Support/MindChart/Patient Records.sparsebundle`, an AES-256 encrypted disk image built into macOS. Its password is never stored, unless Touch ID is turned on (`touchid.json` next to it, sealed by the Secure Enclave). Uploads waiting for review, the examples MindChart learns from (document types and corrected facts) and writing preferences are kept there too, and an example is deleted along with its document. Time Machine backs it up still encrypted. Every sign-in, view, edit (with old and new values), build, export and deletion is recorded under Activity, in a tamper-evident log that Settings can verify and export.

**Error log:** `~/Library/Logs/MindChart`. It records what failed and where, never record contents, so it's safe to send when reporting a problem.

## Hardware and models

MindChart reads the Mac's memory and picks a model that fits the GPU's share of it (about two thirds of memory). It also sets how rich the animations are.

| Mac memory | Default model | Effects |
|---|---|---|
| 24 GB or more | gpt-oss:20b | Rich |
| 16 GB (e.g. MacBook Air M2) | ornith-1.5:9b | Standard |
| 8 GB | bonsai-2:9b | Minimal |

macOS "Reduce motion" turns animations off. Other downloaded models can be picked at the bottom of the sidebar, and ones too big for the Mac are labelled. To compare a new model, run it on `sample-records/` first.

**Who makes them.** All run offline; none sends anything to its publisher.

| Model | Publisher | License |
|---|---|---|
| gpt-oss:20b | OpenAI | Apache 2.0 |
| ornith-1.5:9b | Ornith Team ([ornith.ai](https://ornith.ai)), built on Alibaba's Qwen 3.5 | MIT ([model card](https://huggingface.co/ornith-ai/Ornith-1.5-9B)) |
| bonsai-2:9b | Prism ML ([prismml.com](https://prismml.com)) | Apache 2.0 |

**Models for 16 GB Macs.** A 16 GB Mac gives the GPU about 10.7 GB, which has to hold the model and its working memory. Tested on the five fictional records, scoring 32 key facts known to be in them:

| Model | Key facts found | Caught the planted contradiction | Time* | Memory used |
|---|---|---|---|---|
| **ornith-1.5:9b** | 32 / 32 | Yes | 17 min | 5.3 GB |
| bonsai-2:27b | 31 / 32 | Yes | 37 min | 8.6 GB |
| mistral-nemo | 28 / 32 | No | 18 min | 9.7 GB |
| bonsai-2:9b | 28 / 32 | Yes | 11 min | 3.1 GB |

**On 8 GB**, the GPU gets about 5.4 GB, so MindChart uses bonsai-2:9b (3.1 GB): the fastest of these, finding 28 of 32 key facts and catching the contradiction. It works best with other apps closed, since macOS, the browser and the model share the same 8 GB.

**On 24 GB or more**, gpt-oss:20b (13 GB) found 27 to 32 of 32 key facts across six runs, caught the contradiction every time, and took 11 to 15 minutes.

\*Measured on an M3. An M2 has the same memory speed, which mostly sets how fast these models run, so timings should be similar. Results vary a little between runs.

## Tests

```
scripts/test.sh
```

171 automated tests cover:
- document reading, including OCR of scans, photos, sideways pages and pictures in Word files
- uploads, patient matching (including the wrong-sibling warning) and learning document types
- background reading, quote checking and citations, and the drafting flow
- dating facts (current and earlier ones, events and background), the current picture, and writing evaluations as a job the page follows
- checking pages against the original, short quotes from terse notes, and learning from corrected facts and edited drafts (without patient details)
- record types and field types, including converting a field's type and adding new types
- people: first and last names, gender, placements, several guardians per patient, ZIP codes and drive times
- typed dates, the medication list and how often, cottages, incidents (including from the command bar), and the cottage briefings keeping only points the records support
- medications (including suggestions from the records), rating scales and their bands, follow-ups, Coming up and the timeline
- visit notes, and searching inside documents
- AI-filled fields, the encrypted store, the audit trail, sign-in limits, password changes and Touch ID
- data upgrades, the command bar, the getting-started checklist, the server's security checks and the updater They run on **Apple's built-in Python** (the one every Mac has), so code that only works with a newer Python can't slip through. They also run automatically on GitHub for every change. They don't need the AI model: stand-in answers take its place.

## Updates

Installed copies check for a newer release each time they start, download it, verify its checksum and replace only the app's files (and the app in Applications). The data store isn't touched. If the recommended AI model changed, MindChart downloads it in the background. If GitHub's API can't be reached (it limits how often one network address can ask, which a shared office network can use up), the release page's own link is used instead. Each check's result is kept in `~/Library/Logs/MindChart/last-update-check.json` and shown in **Settings → Version and updates**, which can also check and install on demand.

To publish a version:

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
| `fields.py` | Record types and their fields, field types, and converting values when a type changes |
| `history.py` | Identifying documents, fact extraction, quote checking, section drafting, Word export |
| `intake.py` | Uploads: matching a file to a patient and suggesting its type, for the clinician to confirm |
| `learning.py` | Recognizing document types from built-in hints and the clinician's confirmed examples |
| `worker.py` | Background work: identifying uploads, reading confirmed documents, then dating older facts |
| `picture.py` | When each fact was true: which ongoing facts are current and which are earlier, worked out from the dates |
| `assistant.py` | The command bar: questions and logging proposals |
| `documents.py` | Reads PDF, image, Word, text and spreadsheet files (with OCR for scans, photos and pictures in Word files) |
| `app.html`, `static/` | The app's page, styles and script (`static/tour.js`: the tour, help for each page, and the security explainer) |
| `chat.html` | General chat with the local model |
| `updater.py` | Self-updates from GitHub releases |
| `install.sh` | One-line installer |
| `tools/MindChartApp.swift` | The Mac app: its window, menus, file pickers and Save dialogs |
| `tools/make_icon.swift` | Draws the app icon |
| `tools/pdftext.swift` | PDF and image reader with OCR (any orientation), using built-in macOS frameworks |
| `zipcodes.py`, `data/zipcodes.tsv.gz` | Towns and rough drive times from ZIP codes, on the Mac. ZIP code data © GeoNames (geonames.org), licensed CC BY 4.0 |
| `touchid.py`, `tools/touchid.swift` | Optional Touch ID sign-in: the password sealed by a Secure Enclave key that needs a fingerprint |
| `scripts/build_app.sh` | Builds MindChart.app |
| `scripts/test.sh`, `tests/` | The automated tests |
| `scripts/release.sh` | Publishes a new version (only if the tests pass) |
| `HIPAA.md` | Security and HIPAA overview for compliance reviewers |
| `sample-records/` | **Fictional** test records. Not real patients. |
