# Testing MindChart

Thank you for trying MindChart! It's an early version, so rough edges are expected, and your feedback shapes what comes next.

**Please test only with the fictional sample records** (the installer puts them in your **Documents** folder, in **MindChart sample records (fictional)**). Don't use real patient records unless your organization has approved MindChart and this Mac for patient data.

## What you need

- A Mac with Apple Silicon (M1 or newer), macOS 12 (Monterey) or newer, and at least 8 GB of memory. 16 GB or more is better: on 8 GB, MindChart uses a smaller, faster AI model that finds a little less, and it runs best with other apps (like Zoom or a busy web browser) closed
- About 20 GB of free disk space
- An internet connection for installing (MindChart itself works offline afterwards)

## Installing

1. Open **Terminal** (press ⌘-Space, type *Terminal*, press Return).
2. Paste this line and press Return:

   ```
   curl -fsSL https://raw.githubusercontent.com/ChamSW93/mindchart/main/install.sh | bash
   ```

3. Follow what it says. It may ask you to install Apple's command line tools or Ollama, then run the line again. The AI model download is several GB.
4. When it says **Done**, MindChart opens. From then on, open it from your **Applications** folder or **Launchpad**, like any app. If it says MindChart didn't open, send a screenshot of the Terminal window.
5. Enter your name and choose a password. Keep the password somewhere safe: if it's lost, your test records can't be recovered, not even by the developer.

Updates install by themselves when you open MindChart. **Settings → Version and updates** shows the version you have and how the last check went, with **Check for updates** and **Install now** buttons. If it can't update, paste the install line into Terminal again: your records stay as they are. To quit, press ⌘Q; that also locks your records.

## Things to try

MindChart starts with a short tour of each part of the app. Take it (or skip it, and take it later from **Settings**). The **?** button next to the bar at the top explains whatever page you're on, and the green padlock at the top left explains how your records are protected.

1. Add a patient with **+ New patient**: first name *Jordan*, last name *Avery*, date of birth *05/14/2011* (the child in the sample records).
2. Drag the files from Documents → **MindChart sample records (fictional)** onto the MindChart window, or click **Upload documents** on Home.
3. In **Uploads**, check each review card: the type MindChart suggests (and how sure it is), the patient it matched, and what it will take from the document. Confirm them one by one, or with **Confirm all that are ready**.
   - Try dropping a file onto a *different* patient's page. The card should warn you.
   - Try **+ New type…** for a kind of document MindChart doesn't know.
   - Try a photo of a printed page (fictional records only), or a sideways scan.
   - Try a photo of some made-up handwritten notes, then **Check pages** on its card: the photo shows beside the text MindChart read. Correct anything it misread and save.
4. Watch the bottom left of the sidebar: confirmed documents are read in the background. You can keep working meanwhile.
5. On Jordan's page, click **Write evaluation**. Type a few lines of made-up evaluation notes (what Jordan said, a mental status, an impression, diagnoses and a plan), and write the draft. Review it: check that it reads like an evaluation, that every sentence's citation is right, that the impression, diagnoses and plan are only what you wrote, and try **Export to Word**. The AI can make mistakes: tell us about anything it got wrong or made up.
   - While it's writing, you can open other pages and come back: it carries on and opens when it's done.
   - Rewrite a section or two the way you'd write them, click **Save changes**, then **Suggest from my edits**. Keep the suggestions that are right. They show in **Settings → What MindChart has learned**, and the next evaluation should follow them.
6. Open the court order, IEP and psychological evaluation from the patient's page. Check the details the AI filled in (marked **✦ AI**, with the quote it came from) and correct one with **Edit**. Changing a document's type teaches MindChart too, and so does correcting a fact the AI found (open it from **Facts found**): the next document of that type is read with your corrections as examples.
   - On Jordan's page, look at **Current picture**: the newest information on each topic, with what older records said under it (for example, his guanfacine dose before and after the 2024 change). Each fact shows when it was true, such as "as of 08/2024" or "earlier".
7. On Jordan's page, try the newer parts:
   - **Suggest from records** under Medications, then add the ones that are right and set their consent.
   - **+ Add a score or vitals**: a few PHQ-A scores on different dates, to see the chart.
   - **+ Add follow-up**, then look at **Coming up** on Home.
   - Open the **Timeline**.
   - **Write visit note**: type a few lines, click through the mental status exam, add a plan, and **Draft the note with AI**. Try **Copy**.
8. In the bar at the top, try:
   - *Which patients have an order of protection?*
   - *Which cottage is Jordan in?*
   - *Add a note for Jordan: had a calm week, no incidents, sleeping 8 hours*
   - *Change Jordan's status to Discharged*
   - *Jordan's PHQ-A was 11 today*
   - *Remind me to renew Jordan's medication consent by 12/01*
   - *Who is on sertraline?*
9. Browse **Patients**, **Guardians**, **Documents** and **Facts**, open a record and **Edit** it. Give a patient one or more legal guardians (choose **New guardian…** to add one) and a guardian's ZIP code, and try typing a date or picking one from the calendar in the box.
10. Add a medication from the list (try typing a brand name, and choose how often), and look at **Medications → By medication**. In the bar at the top, describe an incident between two youths, one of them not on file (say, *Eric had mild wounds from the incident with Jordan*), and check what MindChart proposes.
11. Put a few fictional patients in a cottage, open it under **Cottages**, and try **Analyze with AI** (experimental). Tell it what was helpful or not.
12. Click **Lock MindChart**, then sign back in.

## Feedback

Notes on any of these help:

- Anything confusing, slow or broken (what you did, what happened, a screenshot if you can)
- Facts the draft got wrong or missed
- Uploads whose type or patient MindChart got wrong
- Whether the tour and the ? help were clear, and anything they didn't explain
- Whether visit notes, medications, scores and follow-ups match how you actually work, and what's missing
- Whether the draft would save you time, and what would make it more useful
- Which version you're on (shown at the bottom left)
- If something breaks: the error log in `~/Library/Logs/MindChart` (it never contains record contents)
