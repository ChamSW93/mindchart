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

MindChart is now a **chat**: an assistant with the knowledge of an experienced psychiatric nurse practitioner. (The records screens, such as Patients and Uploads, are on hold for now.) It starts with a one-minute tour; take it, or skip it and take it later from **Settings**. The **?** button at the top explains whatever you're looking at, and the green padlock explains how your chats are protected.

Use **only fictional details** while testing (the sample records in Documents → **MindChart sample records (fictional)** are made up).

1. Look at the bottom left: it says **AI off**. The AI isn't started when MindChart opens. Send your first message and it starts (it takes a few seconds the first time), then shows **AI on**.
2. Click **Write a psychiatric evaluation**, then type or paste a few lines of made-up evaluation notes (what the youth said, a mental status, an impression, diagnoses and a plan) and send. Check that it uses only what you wrote and marks anything missing as **[not documented]**.
3. Click **Write a progress note** with a few lines of made-up visit notes, and **Mental status exam** with some observations.
4. Ask a **Medication question** with a made-up age, weight, diagnosis and current medications. Check it says whether the medication is approved for that age or used off-label, and tells you to check doses against prescribing information. Tell us about anything it got wrong.
5. Include a rating scale in a message, such as *PHQ-A today, item scores 1,2,1,2,1,1,1,0,1*. MindChart scores it itself and shows the result under your message; an item 9 above 0 is flagged in red, and the answer should start with the safety concern.
6. Ask how old someone is from a made-up date of birth, or how long ago something happened: these are worked out exactly.
7. Attach a file with the paperclip, or drop one onto the window (try a sample record, or a photo of a printed page), and click **Summarize a document**.
8. Try **Differential diagnosis**, **Safety plan** and **Explain it to a family**.
9. Rename a chat by clicking its name at the top, open an older chat from the list on the left, and delete one.
10. While an answer is being written, click **Stop**; then send a message and switch to another chat and back: the answer keeps going.
11. Click **Stop AI** at the bottom left to free the memory it uses, then send another message.
12. Click **Lock MindChart**, then sign back in.

## Feedback

Notes on any of these help:

- Anything confusing, slow or broken (what you did, what happened, a screenshot if you can)
- Answers that got something wrong, made something up, or missed a safety concern (copy the question and answer)
- How long answers take on your Mac, and whether it stays responsive while the AI is on
- Whether the tour and the ? help were clear, and anything they didn't explain
- Whether the drafts match how you actually write evaluations and notes, and what's missing
- Tasks you'd want MindChart to do that aren't there
- Which version you're on (shown at the bottom left)
- If something breaks: the error log in `~/Library/Logs/MindChart` (it never contains record contents)
