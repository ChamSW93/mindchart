# Testing MindChart

Thank you for trying MindChart! It's an early version, so rough edges are expected, and your feedback shapes what comes next.

**Please test only with the fictional sample records** (the installer puts them in your **Documents** folder, in **MindChart sample records (fictional)**). Don't use real patient records unless your organization has approved MindChart and this Mac for patient data.

## What you need

- A Mac with Apple Silicon (M1 or newer) and at least 16 GB of memory
- About 20 GB of free disk space
- An internet connection for installing (MindChart itself works offline afterwards)

## Installing

1. Open **Terminal** (press ⌘-Space, type *Terminal*, press Return).
2. Paste this line and press Return:

   ```
   curl -fsSL https://raw.githubusercontent.com/ChamSW93/mindchart/main/install.sh | bash
   ```

3. Follow what it says. It may ask you to install Apple's command line tools or Ollama, then run the line again. The AI model download is several GB.
4. When it says **Done**, MindChart opens. From then on, open it from your **Applications** folder or **Launchpad**, like any app.
5. Enter your name and choose a password. Keep the password somewhere safe: if it's lost, your test records can't be recovered, not even by the developer.

Updates install by themselves when you open MindChart. To quit, press ⌘Q; that also locks your records.

## Things to try

1. Add a patient with **+ New patient**.
2. On the patient's page, **Upload documents** and choose the files in Documents → **MindChart sample records (fictional)**.
3. Click **Build history** and wait (it takes several minutes; you can keep using your Mac).
4. Review the draft: check the citations, the red "disagree" boxes, and **Export to Word**.
5. Open the court order, IEP and psychological evaluation from the patient's page. Check the details the AI filled in (marked **✦ AI**, with the quote it came from) and correct one with **Edit**.
6. In the bar at the top, try:
   - *Which patients have an order of protection?*
   - *Which cottage is Jordan in?*
   - *Add a note for Jordan: had a calm week, no incidents, sleeping 8 hours*
   - *Change Jordan's status to Discharged*
7. Browse **Patients**, **Documents** and **Facts**, open a record and **Edit** it.
8. Click **Lock MindChart**, then sign back in.

## Feedback

Notes on any of these help:

- Anything confusing, slow or broken (what you did, what happened, a screenshot if you can)
- Facts the draft got wrong or missed
- Whether the draft would save you time, and what would make it more useful
- Which version you're on (shown at the bottom left)
- If something breaks: the error log in `~/Library/Logs/MindChart` (it never contains record contents)
