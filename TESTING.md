# Testing MindChart

Thank you for trying MindChart! It's an early version, so rough edges are expected, and your feedback shapes what comes next.

**Please test only with the fictional sample records** (in `~/Applications/MindChart/sample-records`). Don't use real patient records unless your organization has approved MindChart and this Mac for patient data.

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
4. When it says **Done**, double-click **MindChart** on your Desktop. The first time, macOS may say it's from an unidentified developer: right-click it, choose **Open**, then **Open** again.
5. Choose a password. Keep it somewhere safe: if it's lost, records can't be recovered.

Updates install by themselves when you start MindChart.

## Things to try

1. Add a patient with **+ New patient**.
2. On the patient's page, **Upload documents** and choose the files in `sample-records`.
3. Click **Build history** and wait (it takes several minutes; you can keep using your Mac).
4. Review the draft: check the citations, the red "disagree" boxes, and **Export to Word**.
5. In the bar at the top, try:
   - *Which patients have an order of protection?*
   - *Add a note for Jordan: had a calm week, no incidents, sleeping 8 hours*
   - *Change Jordan's status to Discharged*
6. Browse **Patients**, **Documents** and **Facts**, open a record and **Edit** it.
7. Click **Lock MindChart**, then sign back in.

## Feedback

Notes on any of these help:

- Anything confusing, slow or broken (what you did, what happened, a screenshot if you can)
- Facts the draft got wrong or missed
- Whether the draft would save you time, and what would make it more useful
- Which version you're on (shown at the bottom left)
